from __future__ import annotations

import argparse
import hashlib
import json
import sys
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from rainpulse_algo.datasets.mrms_precip import read_mrms_precip_frames
from rainpulse_algo.grid import RegularLatLonGrid

SELECTION_PROTOCOL_VERSION = "mrms-observation-holdout-v1"
SPLIT_SELECTION_PROTOCOL_VERSION = "mrms-observation-split-v2"
SCREEN_CADENCE_MINUTES = 60
COVERAGE_MINIMUM_RATIO = 0.99
WET_ACTIVE_FRACTION_MINIMUM = 0.005
WET_ACTIVE_HOUR_MINIMUM = 4
WET_CASES_PER_MONTH = 2
DRY_CASES_PER_MONTH = 1
CASE_SEPARATION_HOURS = 48


class MRMSHoldoutSelectionError(ValueError):
    """Raised when the observation-only holdout selection contract cannot be satisfied."""


@dataclass(frozen=True)
class HoldoutRegion:
    region_id: str
    label: str
    grid: RegularLatLonGrid


@dataclass(frozen=True)
class HoldoutRegionCatalog:
    catalog_version: str
    catalog_sha256: str
    regions: tuple[HoldoutRegion, ...]


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _grid(raw: dict[str, Any], contract: dict[str, Any]) -> RegularLatLonGrid:
    bounds = raw["bounds"]
    spacing = float(contract["spacing_degrees"])
    grid = RegularLatLonGrid(
        grid_id=str(raw["grid_id"]),
        config_version=str(raw["config_version"]),
        west=float(bounds["west"]),
        east=float(bounds["east"]),
        south=float(bounds["south"]),
        north=float(bounds["north"]),
        longitude_interval_deg=spacing,
        latitude_interval_deg=spacing,
        longitude_count=int(contract["longitude_count"]),
        latitude_count=int(contract["latitude_count"]),
        reference_latitude_deg=(float(bounds["south"]) + float(bounds["north"])) / 2.0,
        ancillary_domain_id=str(contract["ancillary_domain_id"]),
    )
    if grid.coordinate_sha256 != str(raw["coordinate_sha256"]):
        raise MRMSHoldoutSelectionError(
            f"holdout region {grid.grid_id} coordinate SHA-256 differs from the catalog"
        )
    return grid


def load_holdout_region_catalog(path: Path) -> HoldoutRegionCatalog:
    try:
        catalog_bytes = path.read_bytes()
        raw = yaml.safe_load(catalog_bytes)
        if raw["schema_version"] != "1.0":
            raise MRMSHoldoutSelectionError("unsupported holdout region catalog schema")
        if raw["grid_contract"]["registration"] != "point":
            raise MRMSHoldoutSelectionError("holdout regions must use point registration")
        regions = tuple(
            HoldoutRegion(
                region_id=str(item["region_id"]),
                label=str(item["label"]),
                grid=_grid(item["grid"], raw["grid_contract"]),
            )
            for item in raw["regions"]
        )
    except MRMSHoldoutSelectionError:
        raise
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise MRMSHoldoutSelectionError(f"invalid holdout region catalog {path}: {exc}") from exc
    if len(regions) < 3 or len({item.region_id for item in regions}) != len(regions):
        raise MRMSHoldoutSelectionError("holdout region catalog must contain unique regions")
    return HoldoutRegionCatalog(
        catalog_version=str(raw["catalog_version"]),
        catalog_sha256=hashlib.sha256(catalog_bytes).hexdigest(),
        regions=regions,
    )


def _parse_month(value: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise MRMSHoldoutSelectionError(f"invalid holdout month {value}") from exc
    return parsed.year, parsed.month


def _month_hours(value: str) -> tuple[datetime, ...]:
    year, month = _parse_month(value)
    count = monthrange(year, month)[1] * 24
    start = datetime(year, month, 1, tzinfo=UTC)
    return tuple(start + timedelta(hours=index) for index in range(count))


def _source_path(root: Path, valid_time: datetime) -> Path:
    filename = f"MRMS_PrecipRate_00.00_{valid_time:%Y%m%d-%H%M%S}.grib2.gz"
    candidates = (
        root
        / "raw"
        / "noaa-mrms-pds"
        / "CONUS"
        / "PrecipRate_00.00"
        / "10min"
        / f"{valid_time:%Y/%m/%d}"
        / filename,
        root
        / "raw"
        / "iem-mtarchive"
        / "CONUS"
        / "PrecipRate_00.00"
        / "10min"
        / f"{valid_time:%Y/%m/%d}"
        / filename.removeprefix("MRMS_"),
    )
    for path in candidates:
        if path.is_file():
            return path
    raise MRMSHoldoutSelectionError(f"missing hourly MRMS screening slot {valid_time.isoformat()}")


def _manifest_summary(root: Path, month: str) -> tuple[dict[str, Any], dict[str, tuple[int, str]]]:
    year, month_number = _parse_month(month)
    directory = (
        root
        / "manifests"
        / "PrecipRate_00.00"
        / "10min"
        / f"{year:04d}"
        / f"{month_number:02d}"
    )
    digest = hashlib.sha256()
    assets: dict[str, tuple[int, str]] = {}
    paths = sorted(directory.glob("*.json"))
    expected_days = monthrange(year, month_number)[1]
    if len(paths) != expected_days:
        raise MRMSHoldoutSelectionError(f"month {month} does not have one manifest per day")
    for path in paths:
        payload_bytes = path.read_bytes()
        digest.update(path.name.encode("ascii"))
        digest.update(b"\0")
        digest.update(payload_bytes)
        payload = json.loads(payload_bytes)
        if not payload.get("complete") or int(payload["available_count"]) != int(
            payload["expected_count"]
        ):
            raise MRMSHoldoutSelectionError(f"incomplete MRMS manifest {path}")
        for asset in payload["assets"]:
            assets[str(asset["relative_path"])] = (
                int(asset["size_bytes"]),
                str(asset["sha256"]),
            )
    return (
        {
            "month": month,
            "daily_manifest_count": len(paths),
            "described_asset_count": len(assets),
            "daily_manifests_sha256": digest.hexdigest(),
            "complete": True,
        },
        assets,
    )


def _frame_statistics(region: HoldoutRegion, valid_time: datetime, frame) -> dict[str, Any]:
    valid = frame.valid_mask == 1
    valid_count = int(np.count_nonzero(valid))
    total_count = int(valid.size)
    rate = frame.rate_mm_h

    def fraction(threshold: float) -> float:
        if valid_count == 0:
            return 0.0
        return round(float(np.count_nonzero(valid & (rate >= threshold)) / valid_count), 12)

    maximum = float(np.nanmax(rate)) if valid_count else None
    return {
        "month": valid_time.strftime("%Y-%m"),
        "region_id": region.region_id,
        "valid_time_utc": valid_time.isoformat().replace("+00:00", "Z"),
        "valid_fraction": round(valid_count / total_count, 12),
        "rain_fraction_ge_0p1": fraction(0.1),
        "rain_fraction_ge_1": fraction(1.0),
        "rain_fraction_ge_10": fraction(10.0),
        "maximum_rate_mm_h": round(maximum, 6) if maximum is not None else None,
    }


def scan_month_observations(
    root: Path,
    catalog: HoldoutRegionCatalog,
    month: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read hourly observations only; no forecast implementation is imported or executed."""

    manifest_summary, manifest_assets = _manifest_summary(root, month)
    rows: list[dict[str, Any]] = []
    grids = tuple(region.grid for region in catalog.regions)
    for index, valid_time in enumerate(_month_hours(month), start=1):
        path = _source_path(root, valid_time)
        relative = path.relative_to(root).as_posix()
        expected = manifest_assets.get(relative)
        if expected is None or path.stat().st_size != expected[0]:
            raise MRMSHoldoutSelectionError(
                f"hourly screening asset differs from its manifest: {relative}"
            )
        frames = read_mrms_precip_frames(path, grids)
        rows.extend(
            _frame_statistics(region, valid_time, frames[region.grid.grid_id])
            for region in catalog.regions
        )
        if index % 24 == 0:
            print(
                f"screened {month}: {index}/{len(_month_hours(month))} hourly frames",
                file=sys.stderr,
                flush=True,
            )
    manifest_summary["screened_hourly_asset_count"] = len(_month_hours(month))
    manifest_summary["screened_asset_size_checks_passed"] = True
    return rows, manifest_summary


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _block(
    index: dict[tuple[str, datetime], dict[str, Any]],
    region_id: str,
    anchor: datetime,
    offsets: range,
) -> list[dict[str, Any]] | None:
    values: list[dict[str, Any]] = []
    for offset in offsets:
        row = index.get((region_id, anchor + timedelta(hours=offset)))
        if row is None:
            return None
        values.append(row)
    return values


def _candidate(
    region: HoldoutRegion,
    anchor: datetime,
    block: list[dict[str, Any]],
    category: str,
) -> dict[str, Any]:
    rain_0p1 = [float(row["rain_fraction_ge_0p1"]) for row in block]
    rain_1 = [float(row["rain_fraction_ge_1"]) for row in block]
    rain_10 = [float(row["rain_fraction_ge_10"]) for row in block]
    wet_score = float(np.mean(rain_1) + 2.0 * np.mean(rain_10))
    return {
        "month": anchor.strftime("%Y-%m"),
        "region_id": region.region_id,
        "region_label": region.label,
        "anchor_time_utc": anchor.isoformat().replace("+00:00", "Z"),
        "category": category,
        "minimum_valid_fraction": min(float(row["valid_fraction"]) for row in block),
        "mean_rain_fraction_ge_0p1": round(float(np.mean(rain_0p1)), 12),
        "maximum_rain_fraction_ge_0p1": max(rain_0p1),
        "mean_rain_fraction_ge_1": round(float(np.mean(rain_1)), 12),
        "mean_rain_fraction_ge_10": round(float(np.mean(rain_10)), 12),
        "maximum_rate_mm_h": max(float(row["maximum_rate_mm_h"] or 0.0) for row in block),
        "wet_active_hour_count": sum(
            value >= WET_ACTIVE_FRACTION_MINIMUM for value in rain_1
        ),
        "wet_score": round(wet_score, 12),
    }


def _selected_case(
    candidate: dict[str, Any],
    region: HoldoutRegion,
    rank: int,
    *,
    case_prefix: str,
) -> dict[str, Any]:
    anchor = _parse_time(str(candidate["anchor_time_utc"]))
    is_wet = candidate["category"] == "wet"
    issue_start = anchor - timedelta(hours=2 if is_wet else 3)
    issue_end = anchor + timedelta(hours=2 if is_wet else 3)
    step = 30 if is_wet else 60
    case_id = (
        f"{case_prefix}_{anchor:%Y%m%d}_{region.region_id}_"
        f"{'wet' if is_wet else 'dry'}_{rank}"
    )
    return {
        "case_id": case_id,
        "label": f"{region.label} observation-screened {'wet event' if is_wet else 'dry control'}",
        "category": candidate["category"],
        "anchor_time_utc": candidate["anchor_time_utc"],
        "issues": {
            "start": issue_start.isoformat().replace("+00:00", "Z"),
            "end": issue_end.isoformat().replace("+00:00", "Z"),
            "step_minutes": step,
        },
        "grid": {
            "grid_id": region.grid.grid_id,
            "config_version": region.grid.config_version,
            "bounds": {
                "west": region.grid.west,
                "east": region.grid.east,
                "south": region.grid.south,
                "north": region.grid.north,
            },
            "coordinate_sha256": region.grid.coordinate_sha256,
        },
        "observation_screen": candidate,
    }


def select_observation_holdout(
    rows: list[dict[str, Any]],
    catalog: HoldoutRegionCatalog,
    months: tuple[str, ...],
    *,
    case_prefix: str = "holdout",
) -> list[dict[str, Any]]:
    """Select balanced wet/dry cases from hourly observation statistics only."""

    region_by_id = {region.region_id: region for region in catalog.regions}
    index = {
        (str(row["region_id"]), _parse_time(str(row["valid_time_utc"]))): row
        for row in rows
    }
    selected_cases: list[dict[str, Any]] = []
    for month in months:
        month_rows = [row for row in rows if row["month"] == month]
        wet_candidates: list[dict[str, Any]] = []
        dry_candidates: list[dict[str, Any]] = []
        for row in month_rows:
            region = region_by_id[str(row["region_id"])]
            anchor = _parse_time(str(row["valid_time_utc"]))
            wet_block = _block(index, region.region_id, anchor, range(-2, 3))
            if wet_block is not None:
                candidate = _candidate(region, anchor, wet_block, "wet")
                if (
                    candidate["minimum_valid_fraction"] >= COVERAGE_MINIMUM_RATIO
                    and candidate["wet_active_hour_count"] >= WET_ACTIVE_HOUR_MINIMUM
                ):
                    wet_candidates.append(candidate)
            dry_block = _block(index, region.region_id, anchor, range(-3, 4))
            if dry_block is not None:
                candidate = _candidate(region, anchor, dry_block, "dry")
                if candidate["minimum_valid_fraction"] >= COVERAGE_MINIMUM_RATIO:
                    dry_candidates.append(candidate)

        wet_candidates.sort(
            key=lambda item: (
                -float(item["wet_score"]),
                str(item["anchor_time_utc"]),
                str(item["region_id"]),
            )
        )
        selected_wet: list[dict[str, Any]] = []
        for candidate in wet_candidates:
            anchor = _parse_time(str(candidate["anchor_time_utc"]))
            if any(candidate["region_id"] == item["region_id"] for item in selected_wet):
                continue
            if any(
                abs(anchor - _parse_time(str(item["anchor_time_utc"])))
                < timedelta(hours=CASE_SEPARATION_HOURS)
                for item in selected_wet
            ):
                continue
            selected_wet.append(candidate)
            if len(selected_wet) == WET_CASES_PER_MONTH:
                break
        if len(selected_wet) != WET_CASES_PER_MONTH:
            raise MRMSHoldoutSelectionError(
                f"month {month} does not satisfy the frozen wet-case diversity rules"
            )

        dry_candidates.sort(
            key=lambda item: (
                float(item["maximum_rain_fraction_ge_0p1"]),
                float(item["mean_rain_fraction_ge_0p1"]),
                str(item["anchor_time_utc"]),
                str(item["region_id"]),
            )
        )
        selected_dry = next(
            (
                candidate
                for candidate in dry_candidates
                if all(
                    abs(
                        _parse_time(str(candidate["anchor_time_utc"]))
                        - _parse_time(str(wet["anchor_time_utc"]))
                    )
                    >= timedelta(hours=CASE_SEPARATION_HOURS)
                    for wet in selected_wet
                )
            ),
            None,
        )
        if selected_dry is None:
            raise MRMSHoldoutSelectionError(
                f"month {month} does not satisfy the frozen dry-control separation rule"
            )

        for rank, candidate in enumerate(selected_wet, start=1):
            selected_cases.append(
                _selected_case(
                    candidate,
                    region_by_id[str(candidate["region_id"])],
                    rank,
                    case_prefix=case_prefix,
                )
            )
        selected_cases.append(
            _selected_case(
                selected_dry,
                region_by_id[str(selected_dry["region_id"])],
                1,
                case_prefix=case_prefix,
            )
        )
    return selected_cases


def build_selection_evidence(
    *,
    rows: list[dict[str, Any]],
    catalog: HoldoutRegionCatalog,
    months: tuple[str, ...],
    manifests: list[dict[str, Any]],
    generated_at: datetime | None = None,
    selection_role: str | None = None,
) -> dict[str, Any]:
    if selection_role not in {None, "development", "independent_holdout"}:
        raise MRMSHoldoutSelectionError("selection role is unsupported")
    case_prefix = "development" if selection_role == "development" else "holdout"
    selected = select_observation_holdout(
        rows,
        catalog,
        months,
        case_prefix=case_prefix,
    )
    issue_count = sum(
        int(
            (
                _parse_time(case["issues"]["end"])
                - _parse_time(case["issues"]["start"])
            ).total_seconds()
            // 60
            // int(case["issues"]["step_minutes"])
        )
        + 1
        for case in selected
    )
    evidence = {
        "schema_version": "1.0",
        "selection_protocol_version": (
            SPLIT_SELECTION_PROTOCOL_VERSION
            if selection_role is not None
            else SELECTION_PROTOCOL_VERSION
        ),
        "generated_at_utc": (generated_at or datetime.now(UTC))
        .isoformat()
        .replace("+00:00", "Z"),
        "claim": (
            "Cases were frozen from MRMS observations before any forecast run for this split."
            if selection_role is not None
            else "Cases were frozen from MRMS observations before any holdout forecast run."
        ),
        "model_forecast_or_skill_fields_read": False,
        "source": {
            "product": "PrecipRate_00.00",
            "units": "mm/h",
            "months": list(months),
            "screen_cadence_minutes": SCREEN_CADENCE_MINUTES,
            "manifest_summaries": manifests,
            "selected_assets_require_full_hash_conformance_before_hindcast": True,
        },
        "candidate_catalog": {
            "catalog_version": catalog.catalog_version,
            "catalog_sha256": catalog.catalog_sha256,
            "region_count": len(catalog.regions),
        },
        "selection_rules": {
            "minimum_valid_fraction": COVERAGE_MINIMUM_RATIO,
            "wet_score": "mean(area_fraction_rate_ge_1)+2*mean(area_fraction_rate_ge_10)",
            "wet_screen_offsets_hours": [-2, -1, 0, 1, 2],
            "wet_active_fraction_minimum": WET_ACTIVE_FRACTION_MINIMUM,
            "wet_active_hour_minimum": WET_ACTIVE_HOUR_MINIMUM,
            "wet_cases_per_month": WET_CASES_PER_MONTH,
            "wet_regions_must_be_distinct_within_month": True,
            "dry_screen_offsets_hours": [-3, -2, -1, 0, 1, 2, 3],
            "dry_rank": "minimum max(area_fraction_rate_ge_0.1), then minimum mean",
            "dry_cases_per_month": DRY_CASES_PER_MONTH,
            "minimum_case_anchor_separation_hours": CASE_SEPARATION_HOURS,
            "wet_issue_window_hours": [-2, 2],
            "wet_issue_step_minutes": 30,
            "dry_issue_window_hours": [-3, 3],
            "dry_issue_step_minutes": 60,
        },
        "screened_observation_row_count": len(rows),
        "screened_observation_statistics_sha256": _canonical_sha256(rows),
        "selected_case_count": len(selected),
        "selected_issue_count": issue_count,
        "selected_cases": selected,
    }
    if selection_role is not None:
        evidence["selection_role"] = selection_role
        evidence["case_namespace"] = case_prefix
    return evidence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze MRMS holdout cases from observations only")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--month", action="append", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--role", choices=("development", "independent_holdout"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        months = tuple(str(value) for value in args.month)
        if len(months) != len(set(months)):
            raise MRMSHoldoutSelectionError("holdout months must be unique")
        catalog = load_holdout_region_catalog(args.catalog)
        rows: list[dict[str, Any]] = []
        manifests: list[dict[str, Any]] = []
        for month in months:
            month_rows, manifest = scan_month_observations(args.root, catalog, month)
            rows.extend(month_rows)
            manifests.append(manifest)
        evidence = build_selection_evidence(
            rows=rows,
            catalog=catalog,
            months=months,
            manifests=manifests,
            selection_role=args.role,
        )
        payload = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
