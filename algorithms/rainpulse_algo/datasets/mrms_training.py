from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .mrms_archive import PRODUCT, iter_dates, manifest_path, sha256_file

MONTH_PATTERN = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
INDEXED_SPLITS = ("training", "development")
AUDITED_SPLITS = (
    "training",
    "development",
    "independent_holdout",
    "spent_verification",
    "reserve",
)


class MRMSTrainingProfileError(ValueError):
    """Raised when the frozen offline-training profile is inconsistent."""


class MRMSTrainingAuditError(RuntimeError):
    """Raised when an audit cannot safely read or publish its inventory."""


@dataclass(frozen=True)
class MRMSTrainingProfile:
    profile_version: str
    profile_sha256: str
    inventory_start: datetime
    inventory_end: datetime
    cadence_minutes: int
    input_frames: int
    target_frames: int
    total_frames: int
    rain_rate_cap_mm_h: float
    training_months: frozenset[str]
    development_months: frozenset[str]
    independent_holdout_months: frozenset[str]
    spent_verification_months: frozenset[str]
    manifest_hash_required: bool
    raw_immutable: bool
    holdout_open: bool
    pilot_sample_count: int
    pilot_minimum_free_bytes: int
    pilot_maximum_output_bytes: int
    full_sample_target: int
    full_sample_minimum_free_bytes: int

    def split_for(self, value: datetime) -> str:
        month = value.strftime("%Y-%m")
        if month in self.training_months:
            return "training"
        if month in self.development_months:
            return "development"
        if month in self.independent_holdout_months:
            return "independent_holdout"
        if month in self.spent_verification_months:
            return "spent_verification"
        return "reserve"


@dataclass(frozen=True)
class _Asset:
    valid_time: datetime
    relative_path: str
    size_bytes: int
    sha256: str
    source_id: str
    split: str
    asset_index: int = -1


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MRMSTrainingProfileError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MRMSTrainingProfileError(f"{field} must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _month_index(value: str) -> int:
    if not MONTH_PATTERN.fullmatch(value):
        raise MRMSTrainingProfileError(f"invalid month: {value}")
    year, month = (int(part) for part in value.split("-"))
    return year * 12 + month - 1


def _expand_months(start: str, end: str) -> frozenset[str]:
    start_index = _month_index(start)
    end_index = _month_index(end)
    if end_index < start_index:
        raise MRMSTrainingProfileError("training month range is reversed")
    return frozenset(
        f"{month_index // 12:04d}-{month_index % 12 + 1:02d}"
        for month_index in range(start_index, end_index + 1)
    )


def _month_set(values: Sequence[Any], field: str) -> frozenset[str]:
    months = tuple(str(value) for value in values)
    if len(months) != len(set(months)):
        raise MRMSTrainingProfileError(f"{field} contains duplicate months")
    for value in months:
        _month_index(value)
    return frozenset(months)


def load_mrms_training_profile(path: Path) -> MRMSTrainingProfile:
    try:
        payload = path.read_bytes()
        raw = yaml.safe_load(payload)
        if not isinstance(raw, dict):
            raise MRMSTrainingProfileError("training profile must be an object")
        source = raw["source"]
        sequence = raw["sequence"]
        spatial = raw["spatial"]
        splits = raw["splits"]
        sampling = raw["sampling"]
        pilot = raw["pilot"]
        full_sample = raw["full_sample"]
        holdout_gate = raw["holdout_gate"]
        training = splits["training"]
    except (KeyError, OSError, TypeError, yaml.YAMLError) as exc:
        raise MRMSTrainingProfileError(f"cannot load training profile {path}: {exc}") from exc

    inventory_start = _parse_utc(str(source["inventory_start"]), "inventory_start")
    inventory_end = _parse_utc(str(source["inventory_end"]), "inventory_end")
    cadence = int(source["cadence_minutes"])
    if inventory_end < inventory_start or cadence != 10:
        raise MRMSTrainingProfileError("inventory time range or cadence differs from v1")
    if any(
        value.second or value.microsecond or value.minute % cadence
        for value in (inventory_start, inventory_end)
    ):
        raise MRMSTrainingProfileError("inventory boundary is not cadence aligned")

    training_months = _expand_months(
        str(training["start_month"]), str(training["end_month"])
    )
    development_months = _month_set(splits["development_months"], "development_months")
    holdout_months = _month_set(
        splits["independent_holdout_months"], "independent_holdout_months"
    )
    spent_months = _month_set(
        splits["spent_verification_months"], "spent_verification_months"
    )
    if training_months & development_months or training_months & holdout_months:
        raise MRMSTrainingProfileError("training split overlaps development or holdout")
    if development_months & holdout_months:
        raise MRMSTrainingProfileError("development and holdout splits overlap")
    if development_months & spent_months or holdout_months & spent_months:
        raise MRMSTrainingProfileError("development or holdout overlaps spent verification data")

    input_frames = int(sequence["input_frames"])
    target_frames = int(sequence["target_frames"])
    total_frames = int(sequence["total_frames"])
    if (input_frames, target_frames, total_frames) != (9, 20, 29):
        raise MRMSTrainingProfileError("sequence must remain 9 input plus 20 target frames")
    if input_frames + target_frames != total_frames:
        raise MRMSTrainingProfileError("sequence frame counts do not close")
    if (
        raw.get("schema_version") != "1.0"
        or raw.get("profile_version") != "nowcastnet-mrms-training-v1"
        or raw.get("lifecycle") != "offline_training"
        or raw.get("operational_eligible") is not False
        or source.get("product") != PRODUCT
        or source.get("unit") != "mm/h"
        or source.get("manifest_hash_required") is not True
        or source.get("raw_immutable") is not True
        or spatial.get("crs") != "EPSG:4326"
        or float(spatial.get("resolution_deg")) != 0.01
        or int(spatial.get("training_crop_size")) != 256
        or int(spatial.get("inference_window_size")) != 512
        or spatial.get("training_dtype") != "float16"
        or float(sampling.get("importance_fraction")) != 0.8
        or float(sampling.get("uniform_fraction")) != 0.2
        or sampling.get("importance_formula") != "sum(1-exp(-rain_rate))+epsilon"
        or holdout_gate.get("status") != "closed"
        or holdout_gate.get("forecast_results_read") is not False
        or holdout_gate.get("open_after_development_gate") is not True
    ):
        raise MRMSTrainingProfileError("training profile differs from frozen v1 invariants")

    return MRMSTrainingProfile(
        profile_version=str(raw["profile_version"]),
        profile_sha256=hashlib.sha256(payload).hexdigest(),
        inventory_start=inventory_start,
        inventory_end=inventory_end,
        cadence_minutes=cadence,
        input_frames=input_frames,
        target_frames=target_frames,
        total_frames=total_frames,
        rain_rate_cap_mm_h=float(sequence["rain_rate_cap_mm_h"]),
        training_months=training_months,
        development_months=development_months,
        independent_holdout_months=holdout_months,
        spent_verification_months=spent_months,
        manifest_hash_required=True,
        raw_immutable=True,
        holdout_open=False,
        pilot_sample_count=int(pilot["sample_count"]),
        pilot_minimum_free_bytes=int(pilot["minimum_free_bytes"]),
        pilot_maximum_output_bytes=int(pilot["maximum_output_bytes"]),
        full_sample_target=int(full_sample["target_sample_count"]),
        full_sample_minimum_free_bytes=int(full_sample["minimum_free_bytes"]),
    )


def _iter_times(start: datetime, end: datetime, cadence_minutes: int) -> Iterable[datetime]:
    current = start
    step = timedelta(minutes=cadence_minutes)
    while current <= end:
        yield current
        current += step


def _json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _safe_asset_path(dataset_root: Path, relative_value: Any) -> tuple[Path, str]:
    relative = Path(str(relative_value))
    if relative.is_absolute() or ".." in relative.parts:
        raise MRMSTrainingAuditError(f"unsafe asset path: {relative}")
    root = dataset_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise MRMSTrainingAuditError(f"asset escapes dataset root: {relative}")
    return resolved, relative.as_posix()


def _validate_dataset_metadata(dataset_root: Path, profile: MRMSTrainingProfile) -> list[str]:
    path = dataset_root / "dataset.json"
    if not path.is_file():
        return [f"missing dataset metadata: {path}"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid dataset metadata {path}: {exc}"]
    errors = []
    if raw.get("product") != PRODUCT:
        errors.append("dataset metadata product differs from training profile")
    if int(raw.get("cadence_minutes", -1)) != profile.cadence_minutes:
        errors.append("dataset metadata cadence differs from training profile")
    return errors


def _load_assets(
    profile: MRMSTrainingProfile,
    dataset_root: Path,
    start: date,
    end: date,
    *,
    full_hash: bool,
) -> tuple[list[_Asset], list[str], list[str], int]:
    assets_by_time: dict[datetime, _Asset] = {}
    errors = _validate_dataset_metadata(dataset_root, profile)
    incomplete_days: list[str] = []
    manifest_count = 0
    for day in iter_dates(start, end):
        path = manifest_path(dataset_root, day, profile.cadence_minutes)
        if not path.is_file():
            errors.append(f"missing manifest: {path}")
            incomplete_days.append(day.isoformat())
            continue
        manifest_count += 1
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid manifest {path}: {exc}")
            incomplete_days.append(day.isoformat())
            continue
        if (
            raw.get("day") != day.isoformat()
            or raw.get("product") != PRODUCT
            or int(raw.get("cadence_minutes", -1)) != profile.cadence_minutes
            or not isinstance(raw.get("assets"), list)
        ):
            errors.append(f"manifest contract differs: {path}")
            incomplete_days.append(day.isoformat())
            continue
        if raw.get("failures"):
            errors.append(f"manifest records download failures: {path}")
        if raw.get("complete") is not True:
            incomplete_days.append(day.isoformat())

        for raw_asset in raw["assets"]:
            try:
                valid_time = _parse_utc(str(raw_asset["valid_time"]), "asset valid_time")
                if (
                    valid_time.date() != day
                    or valid_time.second
                    or valid_time.microsecond
                    or valid_time.minute % profile.cadence_minutes
                ):
                    raise MRMSTrainingAuditError("asset valid time is outside its manifest slot")
                target, relative = _safe_asset_path(dataset_root, raw_asset["relative_path"])
                expected_size = int(raw_asset["size_bytes"])
                expected_sha = str(raw_asset["sha256"])
                if expected_size <= 0:
                    raise MRMSTrainingAuditError("asset has a non-positive manifest size")
                if profile.manifest_hash_required and not SHA256_PATTERN.fullmatch(expected_sha):
                    raise MRMSTrainingAuditError("asset has no valid manifest SHA-256")
                try:
                    target_stat = target.stat()
                except FileNotFoundError as exc:
                    raise MRMSTrainingAuditError(f"missing asset: {target}") from exc
                if not stat.S_ISREG(target_stat.st_mode):
                    raise MRMSTrainingAuditError(f"missing asset: {target}")
                if target_stat.st_size != expected_size:
                    raise MRMSTrainingAuditError(
                        f"asset size mismatch: {target} expected={expected_size} "
                        f"actual={target_stat.st_size}"
                    )
                if full_hash and sha256_file(target) != expected_sha:
                    raise MRMSTrainingAuditError(f"asset SHA-256 mismatch: {target}")
                if valid_time in assets_by_time:
                    raise MRMSTrainingAuditError(
                        f"duplicate authoritative asset time: {valid_time.isoformat()}"
                    )
                assets_by_time[valid_time] = _Asset(
                    valid_time=valid_time,
                    relative_path=relative,
                    size_bytes=expected_size,
                    sha256=expected_sha,
                    source_id=str(raw_asset.get("source_id", "unknown")),
                    split=profile.split_for(valid_time),
                )
            except (KeyError, TypeError, ValueError, MRMSTrainingAuditError) as exc:
                errors.append(f"invalid asset in {path}: {exc}")

    indexed = [
        _Asset(
            valid_time=asset.valid_time,
            relative_path=asset.relative_path,
            size_bytes=asset.size_bytes,
            sha256=asset.sha256,
            source_id=asset.source_id,
            split=asset.split,
            asset_index=index,
        )
        for index, asset in enumerate(
            sorted(assets_by_time.values(), key=lambda item: item.valid_time)
        )
    ]
    return indexed, errors, sorted(set(incomplete_days)), manifest_count


def _segment_counts(
    times: Sequence[datetime],
    profile: MRMSTrainingProfile,
) -> dict[str, int]:
    counts = {name: 0 for name in AUDITED_SPLITS}
    if not times:
        return counts
    step = timedelta(minutes=profile.cadence_minutes)
    start_index = 0
    while start_index < len(times):
        split = profile.split_for(times[start_index])
        end_index = start_index + 1
        while (
            end_index < len(times)
            and profile.split_for(times[end_index]) == split
            and times[end_index] - times[end_index - 1] == step
        ):
            end_index += 1
        if split in ("training", "development", "independent_holdout"):
            counts[split] += max(0, end_index - start_index - profile.total_frames + 1)
        start_index = end_index
    return counts


def _window_id(profile: MRMSTrainingProfile, split: str, assets: Sequence[_Asset]) -> str:
    payload = {
        "asset_sha256": [asset.sha256 for asset in assets],
        "profile_version": profile.profile_version,
        "split": split,
        "valid_times": [asset.valid_time.isoformat() for asset in assets],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_inventory(path: Path, assets: Sequence[_Asset]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for asset in assets:
            handle.write(
                _json_line(
                    {
                        "asset_index": asset.asset_index,
                        "relative_path": asset.relative_path,
                        "sha256": asset.sha256,
                        "size_bytes": asset.size_bytes,
                        "source_id": asset.source_id,
                        "split": asset.split,
                        "valid_time": asset.valid_time.isoformat().replace("+00:00", "Z"),
                    }
                )
            )


def _write_window_index(
    path: Path,
    assets: Sequence[_Asset],
    profile: MRMSTrainingProfile,
) -> dict[str, int]:
    counts = {name: 0 for name in AUDITED_SPLITS}
    step = timedelta(minutes=profile.cadence_minutes)
    start_index = 0
    with path.open("w", encoding="utf-8") as handle:
        while start_index < len(assets):
            split = assets[start_index].split
            end_index = start_index + 1
            while (
                end_index < len(assets)
                and assets[end_index].split == split
                and assets[end_index].valid_time - assets[end_index - 1].valid_time == step
            ):
                end_index += 1
            if split in ("training", "development", "independent_holdout"):
                window_count = max(0, end_index - start_index - profile.total_frames + 1)
                counts[split] += window_count
                if split in INDEXED_SPLITS:
                    for offset in range(window_count):
                        window_assets = assets[
                            start_index + offset : start_index + offset + profile.total_frames
                        ]
                        first = window_assets[0]
                        last = window_assets[-1]
                        issue = window_assets[profile.input_frames - 1]
                        handle.write(
                            _json_line(
                                {
                                    "asset_count": profile.total_frames,
                                    "asset_end_index": last.asset_index,
                                    "asset_start_index": first.asset_index,
                                    "first_valid_time": first.valid_time.isoformat().replace(
                                        "+00:00", "Z"
                                    ),
                                    "issue_time": issue.valid_time.isoformat().replace(
                                        "+00:00", "Z"
                                    ),
                                    "last_valid_time": last.valid_time.isoformat().replace(
                                        "+00:00", "Z"
                                    ),
                                    "split": split,
                                    "window_id": _window_id(profile, split, window_assets),
                                }
                            )
                        )
            start_index = end_index
    return counts


def _bounded_dates(
    profile: MRMSTrainingProfile,
    start: date | None,
    end: date | None,
) -> tuple[date, date]:
    actual_start = start or profile.inventory_start.date()
    actual_end = end or profile.inventory_end.date()
    if actual_end < actual_start:
        raise MRMSTrainingAuditError("audit end date is before start date")
    if (
        actual_start < profile.inventory_start.date()
        or actual_end > profile.inventory_end.date()
    ):
        raise MRMSTrainingAuditError("audit range escapes the frozen inventory range")
    return actual_start, actual_end


def audit_training_archive(
    profile: MRMSTrainingProfile,
    *,
    dataset_root: Path,
    output_root: Path,
    start: date | None = None,
    end: date | None = None,
    full_hash: bool = False,
) -> dict[str, Any]:
    actual_start, actual_end = _bounded_dates(profile, start, end)
    audit_started = datetime.now(UTC)
    if output_root.exists():
        raise MRMSTrainingAuditError(f"audit output already exists: {output_root}")
    if not dataset_root.is_dir():
        raise MRMSTrainingAuditError(f"dataset root does not exist: {dataset_root}")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.tmp-", dir=output_root.parent)
    )
    try:
        assets, errors, incomplete_days, manifest_count = _load_assets(
            profile,
            dataset_root,
            actual_start,
            actual_end,
            full_hash=full_hash,
        )
        inventory_path = temporary / "asset-inventory.jsonl"
        windows_path = temporary / "window-index.jsonl"
        _write_inventory(inventory_path, assets)
        actual_windows = _write_window_index(windows_path, assets, profile)

        start_time = datetime(actual_start.year, actual_start.month, actual_start.day, tzinfo=UTC)
        end_time = datetime(
            actual_end.year, actual_end.month, actual_end.day, 23, 50, tzinfo=UTC
        )
        expected_times = tuple(_iter_times(start_time, end_time, profile.cadence_minutes))
        expected_set = set(expected_times)
        available_set = {asset.valid_time for asset in assets}
        missing_times = sorted(expected_set - available_set)
        incomplete_days = sorted(
            set(incomplete_days) | {value.date().isoformat() for value in missing_times}
        )
        candidate_windows = _segment_counts(expected_times, profile)
        asset_counts = {name: 0 for name in AUDITED_SPLITS}
        missing_counts = {name: 0 for name in AUDITED_SPLITS}
        for asset in assets:
            asset_counts[asset.split] += 1
        for valid_time in missing_times:
            missing_counts[profile.split_for(valid_time)] += 1

        split_report = {}
        for name in AUDITED_SPLITS:
            eligible = actual_windows[name]
            candidate = candidate_windows[name]
            split_report[name] = {
                "asset_count": asset_counts[name],
                "candidate_window_count": candidate,
                "eligible_window_count": eligible,
                "indexed_window_count": eligible if name in INDEXED_SPLITS else 0,
                "missing_slot_count": missing_counts[name],
                "rejected_window_count": max(0, candidate - eligible),
                "window_index_status": (
                    "emitted"
                    if name in INDEXED_SPLITS
                    else "closed_by_holdout_gate"
                    if name == "independent_holdout"
                    else "not_training_eligible"
                ),
            }

        audit_finished = datetime.now(UTC)
        report = {
            "schema_version": "1.0",
            "profile_version": profile.profile_version,
            "profile_sha256": profile.profile_sha256,
            "started_at": audit_started.isoformat(),
            "finished_at": audit_finished.isoformat(),
            "duration_seconds": (audit_finished - audit_started).total_seconds(),
            "audit_range": {
                "start": actual_start.isoformat(),
                "end": actual_end.isoformat(),
                "full_inventory_range": (
                    actual_start == profile.inventory_start.date()
                    and actual_end == profile.inventory_end.date()
                ),
            },
            "asset_count": len(assets),
            "total_bytes": sum(asset.size_bytes for asset in assets),
            "expected_slot_count": len(expected_times),
            "missing_slot_count": len(missing_times),
            "missing_source_times": [
                value.isoformat().replace("+00:00", "Z") for value in missing_times
            ],
            "manifest_count": manifest_count,
            "incomplete_day_count": len(incomplete_days),
            "incomplete_days": incomplete_days,
            "full_content_hash_verified": full_hash,
            "transport_integrity": not errors,
            "source_completeness": not missing_times,
            "error_count": len(errors),
            "errors": errors,
            "asset_inventory_file": inventory_path.name,
            "asset_inventory_sha256": sha256_file(inventory_path),
            "window_index_file": windows_path.name,
            "window_index_sha256": sha256_file(windows_path),
            "indexed_splits": list(INDEXED_SPLITS),
            "splits": split_report,
            "holdout_gate": {
                "status": "closed",
                "forecast_results_read": False,
                "window_index_emitted": False,
            },
            "operational_eligible": False,
        }
        report_path = temporary / "audit-report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_root)
        return report
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit MRMS assets for NowcastNet training")
    parser.add_argument("command", choices=("audit",))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", type=_date_arg)
    parser.add_argument("--end", type=_date_arg)
    parser.add_argument("--full-hash", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        profile = load_mrms_training_profile(args.profile)
        report = audit_training_archive(
            profile,
            dataset_root=args.dataset_root,
            output_root=args.output_root,
            start=args.start,
            end=args.end,
            full_hash=args.full_hash,
        )
    except (MRMSTrainingProfileError, MRMSTrainingAuditError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["transport_integrity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
