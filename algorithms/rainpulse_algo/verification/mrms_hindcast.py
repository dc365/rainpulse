from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np

from rainpulse_algo.datasets.mrms_archive import manifest_path, sha256_file
from rainpulse_algo.datasets.mrms_precip import (
    MRMSPrecipError,
    MRMSPrecipFrame,
    build_mrms_validation_sequence,
    read_mrms_precip_frame,
)
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKFields, run_pysteps_lk_fields
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile

from .deterministic import (
    DeterministicForecast,
    score_deterministic_forecasts,
    summarize_fss_skill,
)
from .map_bundle import (
    VerificationMapProfile,
    build_verification_map_bundle,
    load_verification_map_profile,
    write_verification_map_bundle,
    write_verification_map_index,
)
from .mrms_profile import MRMSVerificationProfile, load_mrms_verification_profile


class MRMSFrameSource(Protocol):
    def read(self, valid_time: datetime, grid: RegularLatLonGrid) -> MRMSPrecipFrame: ...


class MRMSArchiveFrameSource:
    """Read fixed-cadence MRMS frames from the immutable archive layout."""

    def __init__(self, root: Path, *, cadence_minutes: int = 10, verify_hash: bool = True):
        self.root = root
        self.cadence_minutes = cadence_minutes
        self.verify_hash = verify_hash
        self._verified_assets: set[Path] = set()

    def path_for(self, valid_time: datetime) -> Path:
        filename = f"MRMS_PrecipRate_00.00_{valid_time:%Y%m%d-%H%M%S}.grib2.gz"
        candidates = (
            self.root
            / "raw"
            / "noaa-mrms-pds"
            / "CONUS"
            / "PrecipRate_00.00"
            / f"{self.cadence_minutes}min"
            / f"{valid_time:%Y/%m/%d}"
            / filename,
            self.root
            / "raw"
            / "iem-mtarchive"
            / "CONUS"
            / "PrecipRate_00.00"
            / f"{self.cadence_minutes}min"
            / f"{valid_time:%Y/%m/%d}"
            / filename.removeprefix("MRMS_"),
        )
        for path in candidates:
            if path.is_file():
                return path
        raise MRMSPrecipError(f"missing required MRMS source slot {valid_time.isoformat()}")

    def _verify_asset(self, path: Path, valid_time: datetime) -> None:
        if not self.verify_hash or path in self._verified_assets:
            return
        manifest = manifest_path(self.root, valid_time.date(), self.cadence_minutes)
        if not manifest.is_file():
            raise MRMSPrecipError(f"missing MRMS manifest {manifest}")
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            relative = path.relative_to(self.root).as_posix()
            asset = next(
                value for value in payload.get("assets", []) if value["relative_path"] == relative
            )
            expected_size = int(asset["size_bytes"])
            expected_hash = str(asset["sha256"])
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MRMSPrecipError(f"manifest does not describe required MRMS asset {path}") from exc
        if path.stat().st_size != expected_size:
            raise MRMSPrecipError(f"size mismatch for required MRMS asset {path}")
        if sha256_file(path) != expected_hash:
            raise MRMSPrecipError(f"sha256 mismatch for required MRMS asset {path}")
        self._verified_assets.add(path)

    def read(self, valid_time: datetime, grid: RegularLatLonGrid) -> MRMSPrecipFrame:
        path = self.path_for(valid_time)
        self._verify_asset(path, valid_time)
        return read_mrms_precip_frame(path, grid)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# RP016 MRMS offline hindcast",
        "",
        "This is an engineering validation report and is not an operational-readiness claim.",
        "",
        f"- Profile: `{summary['profile_version']}`",
        f"- Completed issues: {summary['completed_issue_count']}",
        f"- Failed issues: {summary['failed_issue_count']}",
        f"- Motion-fallback issues: {summary['motion_fallback_issue_count']}",
        f"- Map bundles: {summary['map_bundle_count']}",
        f"- Map layers: {summary['map_layer_count']}",
        f"- Skill status: `{summary['skill_summary']['status']}`",
        "- Primary truth: observed 10-minute MRMS PrecipRate frames",
        "- Reflectivity input: surrogate derived from MRMS rate with Z=200R^1.6",
        "- Not covered: polar radar QC, RQI/quality validation, confidence calibration",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def conform_mrms_cases(
    profile: MRMSVerificationProfile,
    *,
    frame_source: MRMSFrameSource,
    case_ids: Collection[str] | None = None,
    maximum_issues: int | None = None,
) -> dict[str, object]:
    """Validate only the source frames required by the selected frozen hindcast issues."""

    if maximum_issues is not None and maximum_issues < 1:
        raise ValueError("maximum_issues must be positive")
    selected = [case for case in profile.cases if case_ids is None or case.case_id in case_ids]
    if not selected:
        raise ValueError("no MRMS verification cases selected")

    checked_issues = 0
    checked_frames = 0
    errors: list[dict[str, str]] = []
    for case in selected:
        required: set[datetime] = set()
        for issue_time in case.issue_times:
            if maximum_issues is not None and checked_issues >= maximum_issues:
                break
            checked_issues += 1
            required.update(
                issue_time - timedelta(minutes=minutes) for minutes in (20, 10, 0)
            )
            required.update(
                issue_time + timedelta(minutes=minutes) for minutes in profile.lead_minutes
            )
        for valid_time in sorted(required):
            try:
                frame_source.read(valid_time, case.grid)
                checked_frames += 1
            except Exception as exc:  # noqa: BLE001 - report every external asset failure
                errors.append(
                    {
                        "case_id": case.case_id,
                        "valid_time_utc": valid_time.isoformat().replace("+00:00", "Z"),
                        "error": str(exc),
                    }
                )
        if maximum_issues is not None and checked_issues >= maximum_issues:
            break
    return {
        "schema_version": "1.0",
        "profile_version": profile.profile_version,
        "checked_issue_count": checked_issues,
        "checked_frame_count": checked_frames,
        "failed_frame_count": len(errors),
        "complete": not errors,
        "operational_eligible": False,
        "errors": errors,
    }


def run_mrms_hindcast(
    profile: MRMSVerificationProfile,
    *,
    repository_root: Path,
    frame_source: MRMSFrameSource,
    output_directory: Path,
    case_ids: Collection[str] | None = None,
    maximum_issues: int | None = None,
    map_profile: VerificationMapProfile | None = None,
) -> dict[str, object]:
    """Run selected frozen MRMS cases through the shared production algorithm core."""

    if maximum_issues is not None and maximum_issues < 1:
        raise ValueError("maximum_issues must be positive")
    configured_nowcast = load_pysteps_lk_profile(repository_root / profile.nowcast_profile)
    configured_map = map_profile or load_verification_map_profile(
        repository_root / "configs" / "verification" / "algorithm-map-v1.yaml"
    )
    selected = [case for case in profile.cases if case_ids is None or case.case_id in case_ids]
    if not selected:
        raise ValueError("no MRMS verification cases selected")

    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    completed = 0
    fallback_count = 0
    map_layer_count = 0
    map_issues: list[dict[str, object]] = []
    stop = False
    for case in selected:
        case_profile = replace(
            configured_nowcast,
            grid_id=case.grid.grid_id,
            grid_config_version=case.grid.config_version,
        )
        metric = case.grid.metric()
        pixel_spacing_km = float(
            np.sqrt(
                np.median(metric.x_spacing_m_by_latitude)
                * np.median(metric.y_spacing_m_by_latitude)
            )
            / 1000.0
        )
        for issue_time in case.issue_times:
            if maximum_issues is not None and completed + len(errors) >= maximum_issues:
                stop = True
                break
            try:
                input_times = tuple(
                    issue_time - timedelta(minutes=minutes) for minutes in (20, 10, 0)
                )
                input_frames = {
                    valid_time: frame_source.read(valid_time, case.grid)
                    for valid_time in input_times
                }
                sequence = build_mrms_validation_sequence(
                    input_frames,
                    issue_time,
                    case.grid,
                )
                result = run_pysteps_lk_fields(
                    PystepsLKFields(
                        reflectivity_dbz=sequence.reflectivity_dbz,
                        rate_mm_h=sequence.rate_mm_h,
                        quality_index=sequence.quality_index,
                        valid_mask=sequence.valid_mask,
                        low_quality_mask=sequence.low_quality_mask,
                    ),
                    profile=case_profile,
                    grid=case.grid,
                )
                truth_frames = tuple(
                    frame_source.read(issue_time + timedelta(minutes=lead), case.grid)
                    for lead in profile.lead_minutes
                )
                truth_rate = np.stack([frame.rate_mm_h for frame in truth_frames])
                truth_valid = np.stack([frame.valid_mask for frame in truth_frames])
                lead_indices = np.asarray(
                    [
                        lead // case_profile.extrapolation.lead_step_minutes - 1
                        for lead in profile.lead_minutes
                    ]
                )
                forecasts = {
                    "lk": DeterministicForecast(
                        result.rain_rate[0, lead_indices],
                        result.output_valid_mask[lead_indices],
                    ),
                    "persistence": DeterministicForecast(
                        result.persistence_rain_rate[lead_indices],
                        result.persistence_valid_mask[lead_indices],
                    ),
                    "translation": DeterministicForecast(
                        result.translation_rain_rate[lead_indices],
                        result.translation_valid_mask[lead_indices],
                    ),
                }
                issue_rows = score_deterministic_forecasts(
                    truth_rate,
                    truth_valid,
                    forecasts,
                    lead_minutes=profile.lead_minutes,
                    thresholds_mm_h=profile.thresholds_mm_h,
                    windows_pixels=profile.fss_windows_pixels,
                    pixel_spacing_km=pixel_spacing_km,
                )
                for row in issue_rows:
                    row["case_id"] = case.case_id
                    row["case_category"] = case.category
                    row["issue_time_utc"] = issue_time.isoformat().replace("+00:00", "Z")
                    row["truth_kind"] = profile.primary_truth_kind
                map_manifest, map_objects = build_verification_map_bundle(
                    profile=configured_map,
                    verification_profile_version=profile.profile_version,
                    case_id=case.case_id,
                    truth_kind=profile.primary_truth_kind,
                    issue_time=issue_time,
                    lead_minutes=profile.lead_minutes,
                    grid=case.grid,
                    truth_rate=truth_rate,
                    truth_valid=truth_valid,
                    forecasts={
                        model: (forecast.rate_mm_h, forecast.valid_mask)
                        for model, forecast in forecasts.items()
                    },
                    velocity_pixels_per_step=result.velocity_pixels_per_step,
                    motion_valid_mask=result.motion_valid_mask,
                    motion_fallback_used=result.motion_fallback_used,
                    motion_fallback_reason=result.motion_fallback_reason,
                    motion_feature_count=result.motion_feature_count,
                    trackable_rain_pixel_count=result.trackable_rain_pixel_count,
                )
                write_verification_map_bundle(
                    output_directory / "maps",
                    map_manifest,
                    map_objects,
                )
                manifest_path = (
                    Path(case.case_id) / str(map_manifest["issue_key"]) / "manifest.json"
                )
                map_issues.append(
                    {
                        "case_id": case.case_id,
                        "issue_time_utc": issue_time.isoformat().replace("+00:00", "Z"),
                        "issue_key": map_manifest["issue_key"],
                        "manifest_path": manifest_path.as_posix(),
                        "layer_count": len(map_manifest["layers"]),
                    }
                )
                map_layer_count += len(map_manifest["layers"])
                rows.extend(issue_rows)
                completed += 1
                fallback_count += int(result.motion_fallback_used)
            except Exception as exc:  # noqa: BLE001 - isolate failures by frozen issue
                errors.append(
                    {
                        "case_id": case.case_id,
                        "issue_time_utc": issue_time.isoformat().replace("+00:00", "Z"),
                        "error": str(exc),
                    }
                )
        if stop:
            break

    summary: dict[str, object] = {
        "schema_version": "1.0",
        "profile_version": profile.profile_version,
        "primary_truth_kind": profile.primary_truth_kind,
        "operational_eligible": False,
        "completed_issue_count": completed,
        "failed_issue_count": len(errors),
        "motion_fallback_issue_count": fallback_count,
        "metric_row_count": len(rows),
        "map_bundle_count": len(map_issues),
        "map_layer_count": map_layer_count,
        "map_renderer_version": configured_map.renderer_version,
        "errors": errors,
        "skill_summary": summarize_fss_skill(rows),
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    write_verification_map_index(
        output_directory / "maps",
        verification_profile_version=profile.profile_version,
        renderer_version=configured_map.renderer_version,
        issues=map_issues,
        layer_count=map_layer_count,
    )
    _write_csv(output_directory / "metrics.csv", rows)
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_report(output_directory / "report.md", summary)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RP016 MRMS offline verification")
    parser.add_argument("command", choices=("conformance", "hindcast"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("configs/verification/rp016-mrms-v1.yaml"),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("runtime/reports/mrms"))
    parser.add_argument("--run-id")
    parser.add_argument("--case", action="append")
    parser.add_argument("--max-issues", type=int)
    parser.add_argument("--skip-hash", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    profile_path = args.profile
    if not profile_path.is_absolute():
        profile_path = repository_root / profile_path
    try:
        profile = load_mrms_verification_profile(profile_path)
        source = MRMSArchiveFrameSource(
            args.root,
            cadence_minutes=profile.source_cadence_minutes,
            verify_hash=not args.skip_hash,
        )
        if args.command == "conformance":
            report = conform_mrms_cases(
                profile,
                frame_source=source,
                case_ids=set(args.case) if args.case else None,
                maximum_issues=args.max_issues,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["complete"] else 1

        run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_directory = args.output_root / profile.profile_version / run_id
        summary = run_mrms_hindcast(
            profile,
            repository_root=repository_root,
            frame_source=source,
            output_directory=output_directory,
            case_ids=set(args.case) if args.case else None,
            maximum_issues=args.max_issues,
        )
        printed = {**summary, "output_directory": str(output_directory)}
        print(json.dumps(printed, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if summary["failed_issue_count"] == 0 else 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
