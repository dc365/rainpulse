from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from rainpulse_algo.datasets.mrms_archive import manifest_path, sha256_file
from rainpulse_algo.datasets.mrms_precip import (
    MRMSPrecipError,
    MRMSPrecipFrame,
    build_mrms_observed_sequence,
    build_mrms_validation_sequence,
    read_mrms_precip_frame,
)
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKFields, run_pysteps_lk_fields
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile

from .baselines import build_phase_correlation_forecast
from .deterministic import (
    DeterministicForecast,
    score_accumulation_forecasts,
    score_deterministic_forecasts,
    summarize_coverage,
    summarize_fss_skill,
    summarize_model_fss_difference,
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


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for name in row:
            if name not in seen:
                fieldnames.append(name)
                seen.add(name)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    skill = summary["skill_summary"]
    coverage = summary["coverage_summary"]
    adaptation = summary["adaptation_summary"]
    lines = [
        f"# {summary['profile_version']} MRMS offline hindcast",
        "",
        "This is an engineering validation report and is not an operational-readiness claim.",
        "",
        f"- Completed issues: {summary['completed_issue_count']}",
        f"- Failed issues: {summary['failed_issue_count']}",
        f"- Motion-fallback issues: {summary['motion_fallback_issue_count']}",
        f"- Independent-baseline fallback issues: {summary['phase_correlation_fallback_issue_count']}",
        f"- Skill status: `{skill['status']}`",
        f"- Coverage gate: `{coverage['all_models_pass']}` at {coverage['minimum_required_ratio']:.0%}",
        f"- Adapted-minus-native LK mean FSS: {adaptation['mean_fss_difference']}",
        "- Primary truth: observed 10-minute MRMS PrecipRate frames",
        "- Primary report: common-domain legacy metrics for API compatibility",
        "- Rigorous report: fixed truth domain, physical FSS scales and coverage penalty",
        "- Independent baseline: FFT phase-correlation whole-field translation",
        "- Native-cadence sensitivity: three observed 10-minute frames without interpolation",
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
        "profile_sha256": profile.profile_sha256,
        "checked_issue_count": checked_issues,
        "checked_frame_count": checked_frames,
        "failed_frame_count": len(errors),
        "complete": not errors,
        "operational_eligible": False,
        "errors": errors,
    }


def _fields(sequence) -> PystepsLKFields:
    return PystepsLKFields(
        reflectivity_dbz=sequence.reflectivity_dbz,
        rate_mm_h=sequence.rate_mm_h,
        quality_index=sequence.quality_index,
        valid_mask=sequence.valid_mask,
        low_quality_mask=sequence.low_quality_mask,
    )


def _decorate_rows(
    rows: Sequence[dict[str, Any]],
    *,
    case_id: str,
    case_category: str,
    issue_time: datetime,
    truth_kind: str,
) -> None:
    issue = issue_time.isoformat().replace("+00:00", "Z")
    for row in rows:
        row["case_id"] = case_id
        row["case_category"] = case_category
        row["issue_time_utc"] = issue
        row["truth_kind"] = truth_kind


def _runtime_fingerprint(profile: MRMSVerificationProfile, repository_root: Path) -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for package in ("numpy", "scipy", "opencv-python-headless", "pysteps", "rasterio"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    thread_variables = {
        name: os.getenv(name)
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
    }
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "git_commit": commit,
        "verification_profile_sha256": profile.profile_sha256,
        "packages": packages,
        "thread_environment": thread_variables,
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
    """Run selected frozen MRMS cases through production and rigorous validation paths."""

    if maximum_issues is not None and maximum_issues < 1:
        raise ValueError("maximum_issues must be positive")
    configured_nowcast = load_pysteps_lk_profile(repository_root / profile.nowcast_profile)
    configured_map = map_profile or load_verification_map_profile(
        repository_root / "configs" / "verification" / "algorithm-map-v1.yaml"
    )
    selected = [case for case in profile.cases if case_ids is None or case.case_id in case_ids]
    if not selected:
        raise ValueError("no MRMS verification cases selected")

    output_directory.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    truth_domain_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    adaptation_rows: list[dict[str, Any]] = []
    accumulation_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    completed = 0
    fallback_count = 0
    native_fallback_count = 0
    phase_fallback_count = 0
    map_layer_count = 0
    map_issues: list[dict[str, object]] = []
    stop = False
    for case in selected:
        case_profile = replace(
            configured_nowcast,
            grid_id=case.grid.grid_id,
            grid_config_version=case.grid.config_version,
        )
        native_profile = replace(
            case_profile,
            sequence=replace(
                case_profile.sequence,
                timestep_minutes=profile.source_cadence_minutes,
            ),
            extrapolation=replace(
                case_profile.extrapolation,
                lead_count=len(profile.lead_minutes),
                lead_step_minutes=profile.source_cadence_minutes,
            ),
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
                adapted_sequence = build_mrms_validation_sequence(
                    input_frames,
                    issue_time,
                    case.grid,
                )
                observed_sequence = build_mrms_observed_sequence(
                    input_frames,
                    issue_time,
                    case.grid,
                )
                result = run_pysteps_lk_fields(
                    _fields(adapted_sequence),
                    profile=case_profile,
                    grid=case.grid,
                )
                native_result = run_pysteps_lk_fields(
                    _fields(observed_sequence),
                    profile=native_profile,
                    grid=case.grid,
                )
                phase = build_phase_correlation_forecast(
                    observed_sequence.rate_mm_h[-2],
                    observed_sequence.rate_mm_h[-1],
                    observed_sequence.valid_mask[-2],
                    observed_sequence.valid_mask[-1],
                    lead_minutes=profile.lead_minutes,
                    source_interval_minutes=profile.source_cadence_minutes,
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
                primary_forecasts = {
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
                rigorous_forecasts = {
                    **primary_forecasts,
                    "phase_correlation": DeterministicForecast(
                        phase.rate_mm_h,
                        phase.valid_mask,
                    ),
                    "lk_native_10min": DeterministicForecast(
                        native_result.rain_rate[0],
                        native_result.output_valid_mask,
                    ),
                }

                issue_rows = score_deterministic_forecasts(
                    truth_rate,
                    truth_valid,
                    primary_forecasts,
                    lead_minutes=profile.lead_minutes,
                    thresholds_mm_h=profile.thresholds_mm_h,
                    windows_pixels=profile.fss_windows_pixels,
                    pixel_spacing_km=pixel_spacing_km,
                    validity_domain="common",
                )
                issue_truth_rows = score_deterministic_forecasts(
                    truth_rate,
                    truth_valid,
                    rigorous_forecasts,
                    lead_minutes=profile.lead_minutes,
                    thresholds_mm_h=profile.thresholds_mm_h,
                    windows_pixels=(),
                    windows_km=profile.fss_windows_km,
                    pixel_spacing_km=pixel_spacing_km,
                    validity_domain="truth",
                )
                issue_gate_rows = score_deterministic_forecasts(
                    truth_rate,
                    truth_valid,
                    rigorous_forecasts,
                    lead_minutes=profile.lead_minutes,
                    thresholds_mm_h=profile.thresholds_mm_h,
                    windows_pixels=(11,),
                    pixel_spacing_km=pixel_spacing_km,
                    validity_domain="truth",
                )
                issue_adaptation_rows = score_deterministic_forecasts(
                    truth_rate,
                    truth_valid,
                    {
                        "lk_adapted_5min": rigorous_forecasts["lk"],
                        "lk_native_10min": rigorous_forecasts["lk_native_10min"],
                    },
                    lead_minutes=profile.lead_minutes,
                    thresholds_mm_h=profile.thresholds_mm_h,
                    windows_pixels=(),
                    windows_km=profile.fss_windows_km,
                    pixel_spacing_km=pixel_spacing_km,
                    validity_domain="truth",
                )
                issue_accumulation_rows = score_accumulation_forecasts(
                    truth_rate,
                    truth_valid,
                    rigorous_forecasts,
                    lead_minutes=profile.lead_minutes,
                    accumulation_windows_minutes=profile.accumulation_windows_minutes,
                    thresholds_mm=profile.accumulation_thresholds_mm,
                    windows_pixels=(),
                    windows_km=profile.fss_windows_km,
                    pixel_spacing_km=pixel_spacing_km,
                    validity_domain="truth",
                )
                for collection in (
                    issue_rows,
                    issue_truth_rows,
                    issue_gate_rows,
                    issue_adaptation_rows,
                    issue_accumulation_rows,
                ):
                    _decorate_rows(
                        collection,
                        case_id=case.case_id,
                        case_category=case.category,
                        issue_time=issue_time,
                        truth_kind=profile.primary_truth_kind,
                    )

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
                        for model, forecast in primary_forecasts.items()
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
                bundle_manifest_path = (
                    Path(case.case_id) / str(map_manifest["issue_key"]) / "manifest.json"
                )
                map_issues.append(
                    {
                        "case_id": case.case_id,
                        "issue_time_utc": issue_time.isoformat().replace("+00:00", "Z"),
                        "issue_key": map_manifest["issue_key"],
                        "manifest_path": bundle_manifest_path.as_posix(),
                        "layer_count": len(map_manifest["layers"]),
                    }
                )
                map_layer_count += len(map_manifest["layers"])
                rows.extend(issue_rows)
                truth_domain_rows.extend(issue_truth_rows)
                gate_rows.extend(issue_gate_rows)
                adaptation_rows.extend(issue_adaptation_rows)
                accumulation_rows.extend(issue_accumulation_rows)
                completed += 1
                fallback_count += int(result.motion_fallback_used)
                native_fallback_count += int(native_result.motion_fallback_used)
                phase_fallback_count += int(phase.estimate.fallback_used)
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

    near_minimum, near_maximum = profile.near_lead_minutes
    far_minimum, far_maximum = profile.far_lead_minutes
    rigorous_baselines = ("persistence", "translation", "phase_correlation")
    skill_summary = summarize_fss_skill(
        gate_rows,
        minimum_lead_minutes=near_minimum,
        maximum_lead_minutes=near_maximum,
        baselines=rigorous_baselines,
        minimum_forecast_to_truth_coverage=profile.coverage_minimum_ratio,
    )
    far_skill_summary = summarize_fss_skill(
        gate_rows,
        minimum_lead_minutes=far_minimum,
        maximum_lead_minutes=far_maximum,
        baselines=rigorous_baselines,
        minimum_forecast_to_truth_coverage=profile.coverage_minimum_ratio,
    )
    coverage_summary = summarize_coverage(
        gate_rows,
        models=("lk", "persistence", "translation", "phase_correlation", "lk_native_10min"),
        minimum_ratio=profile.coverage_minimum_ratio,
    )
    adaptation_summary = summarize_model_fss_difference(
        adaptation_rows,
        candidate="lk_adapted_5min",
        reference="lk_native_10min",
    )
    accumulation_summary = {
        "row_count": len(accumulation_rows),
        "windows_minutes": list(profile.accumulation_windows_minutes),
        "thresholds_mm": list(profile.accumulation_thresholds_mm),
        "validity_domain": "truth",
    }
    summary: dict[str, object] = {
        "schema_version": "1.1",
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "primary_truth_kind": profile.primary_truth_kind,
        "operational_eligible": False,
        "completed_issue_count": completed,
        "failed_issue_count": len(errors),
        "motion_fallback_issue_count": fallback_count,
        "native_motion_fallback_issue_count": native_fallback_count,
        "phase_correlation_fallback_issue_count": phase_fallback_count,
        "metric_row_count": len(rows),
        "truth_domain_metric_row_count": len(truth_domain_rows),
        "adaptation_metric_row_count": len(adaptation_rows),
        "accumulation_metric_row_count": len(accumulation_rows),
        "map_bundle_count": len(map_issues),
        "map_layer_count": map_layer_count,
        "map_renderer_version": configured_map.renderer_version,
        "errors": errors,
        "skill_summary": skill_summary,
        "near_skill_summary": skill_summary,
        "far_skill_summary": far_skill_summary,
        "coverage_summary": coverage_summary,
        "adaptation_summary": adaptation_summary,
        "accumulation_summary": accumulation_summary,
        "runtime_fingerprint": _runtime_fingerprint(profile, repository_root),
        "report_files": {
            "common_domain": "metrics.csv",
            "fixed_truth_domain": "metrics_truth_domain.csv",
            "native_cadence_comparison": "adaptation_metrics.csv",
            "accumulation": "accumulation_metrics.csv",
        },
    }
    write_verification_map_index(
        output_directory / "maps",
        verification_profile_version=profile.profile_version,
        renderer_version=configured_map.renderer_version,
        issues=map_issues,
        layer_count=map_layer_count,
    )
    _write_csv(output_directory / "metrics.csv", rows)
    _write_csv(output_directory / "metrics_truth_domain.csv", truth_domain_rows)
    _write_csv(output_directory / "adaptation_metrics.csv", adaptation_rows)
    _write_csv(output_directory / "accumulation_metrics.csv", accumulation_rows)
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_report(output_directory / "report.md", summary)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run versioned MRMS offline verification")
    parser.add_argument("command", choices=("conformance", "hindcast"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("configs/verification/rp018-mrms-v1.yaml"),
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
