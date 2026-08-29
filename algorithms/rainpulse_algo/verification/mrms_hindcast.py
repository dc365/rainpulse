from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
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
from rainpulse_algo.nowcast.pysteps_lk import (
    PystepsLKFields,
    forecast_domain_valid_mask,
    run_pysteps_lk_fields,
)
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile

from .baselines import build_phase_correlation_forecast
from .deterministic import (
    DeterministicForecast,
    score_accumulation_forecasts,
    score_deterministic_forecasts,
    summarize_coverage,
    summarize_coverage_provenance,
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
    coverage_provenance = summary["coverage_provenance_summary"]["models"]["lk"]
    adaptation = summary["adaptation_summary"]
    performance = summary["performance_summary"]
    total_runtime = performance["total_runtime_ms"]
    core_runtime = performance["core_runtime_ms"]
    peak_rss = performance["peak_rss_bytes"]
    lines = [
        f"# {summary['profile_version']} MRMS offline hindcast",
        "",
        "This is an engineering validation report and is not an operational-readiness claim.",
        "",
        f"- Completed issues: {summary['completed_issue_count']}",
        f"- Failed issues: {summary['failed_issue_count']}",
        f"- Motion-fallback issues: {summary['motion_fallback_issue_count']}",
        (
            "- Independent-baseline fallback issues: "
            f"{summary['phase_correlation_fallback_issue_count']}"
        ),
        f"- Skill status: `{skill['status']}`",
        (
            f"- Coverage gate: `{coverage['all_models_pass']}` at "
            f"{coverage['minimum_required_ratio']:.0%}"
        ),
        (
            "- LK mean coverage loss (boundary/interior): "
            f"{coverage_provenance['mean_advection_boundary_loss_ratio']}/"
            f"{coverage_provenance['mean_interior_missing_loss_ratio']}"
        ),
        (
            "- LK minimum coverage after excluding advection boundary: "
            f"{coverage_provenance['minimum_boundary_adjusted_forecast_to_truth_coverage']}"
        ),
        f"- Adapted-minus-native LK mean FSS: {adaptation['mean_fss_difference']}",
        (
            "- Total issue runtime P50/P95/max (ms): "
            f"{total_runtime['p50']}/{total_runtime['p95']}/{total_runtime['max']}"
        ),
        (
            "- Core forecast/scoring runtime P50/P95/max (ms): "
            f"{core_runtime['p50']}/{core_runtime['p95']}/{core_runtime['max']}"
        ),
        (
            "- Whole-process peak RSS P50/P95/max (bytes): "
            f"{peak_rss['p50']}/{peak_rss['p95']}/{peak_rss['max']}"
        ),
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
            required.update(issue_time - timedelta(minutes=minutes) for minutes in (20, 10, 0))
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
    configured_revision = os.getenv("RAINPULSE_BUILD_REVISION", "").strip().lower()
    if configured_revision:
        if len(configured_revision) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in configured_revision
        ):
            raise ValueError("RAINPULSE_BUILD_REVISION must be a full hexadecimal revision")
        commit = configured_revision
        commit_source = "environment"
    else:
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            commit_source = "git"
        except (OSError, subprocess.SubprocessError):
            commit = None
            commit_source = None
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
        "git_commit_source": commit_source,
        "verification_profile_sha256": profile.profile_sha256,
        "packages": packages,
        "thread_environment": thread_variables,
    }


def _resident_memory_bytes() -> int:
    """Return process resident memory without counting only Python allocations."""

    try:
        resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (IndexError, OSError, ValueError):
        pass

    try:
        import resource

        maximum_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return maximum_rss if sys.platform == "darwin" else maximum_rss * 1024
    except (ImportError, OSError, ValueError):
        return 0


class _IssueResourceSampler:
    """Sample whole-process RSS while one frozen issue is evaluated."""

    def __init__(self, *, interval_seconds: float = 0.05):
        self.interval_seconds = interval_seconds
        self.peak_rss_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def sample(self) -> None:
        self.peak_rss_bytes = max(self.peak_rss_bytes, _resident_memory_bytes())

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 4))
        self.sample()
        return self.peak_rss_bytes

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.sample()


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _nearest_rank(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _performance_distribution(
    rows: Sequence[Mapping[str, Any]], field: str
) -> dict[str, int | None]:
    values = [
        int(row[field])
        for row in rows
        if row.get("status") == "completed" and row.get(field) is not None
    ]
    return {
        "p50": _nearest_rank(values, 0.50),
        "p95": _nearest_rank(values, 0.95),
        "max": max(values) if values else None,
    }


def _summarize_performance(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "completed_issue_count": sum(row.get("status") == "completed" for row in rows),
        "failed_issue_count": sum(row.get("status") == "failed" for row in rows),
        "rss_scope": "whole_process_resident_set_sampled_per_issue",
        "rss_sample_interval_ms": 50,
        "total_runtime_ms": _performance_distribution(rows, "total_runtime_ms"),
        "core_runtime_ms": _performance_distribution(rows, "core_runtime_ms"),
        "peak_rss_bytes": _performance_distribution(rows, "peak_rss_bytes"),
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

    runtime_fingerprint = _runtime_fingerprint(profile, repository_root)
    output_directory.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    truth_domain_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    adaptation_rows: list[dict[str, Any]] = []
    accumulation_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
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
            issue_time_utc = issue_time.isoformat().replace("+00:00", "Z")
            issue_started = time.perf_counter()
            resource_sampler = _IssueResourceSampler()
            resource_sampler.start()
            stage = "input_read"
            stage_runtime_ms = {
                "input_read_ms": 0,
                "nowcast_runtime_ms": 0,
                "truth_read_ms": 0,
                "scoring_runtime_ms": 0,
                "map_runtime_ms": 0,
            }
            try:
                stage_started = time.perf_counter()
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
                stage_runtime_ms["input_read_ms"] = _elapsed_ms(stage_started)

                stage = "nowcast"
                stage_started = time.perf_counter()
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
                result_domain = forecast_domain_valid_mask(
                    case.grid.shape,
                    result.velocity_pixels_per_step,
                    case_profile.extrapolation.lead_count,
                )
                translation_velocity = np.empty_like(result.velocity_pixels_per_step)
                translation_velocity[0] = result.global_translation_pixels_per_step[0]
                translation_velocity[1] = result.global_translation_pixels_per_step[1]
                translation_domain = forecast_domain_valid_mask(
                    case.grid.shape,
                    translation_velocity,
                    case_profile.extrapolation.lead_count,
                )
                native_domain = forecast_domain_valid_mask(
                    case.grid.shape,
                    native_result.velocity_pixels_per_step,
                    native_profile.extrapolation.lead_count,
                )
                stage_runtime_ms["nowcast_runtime_ms"] = _elapsed_ms(stage_started)

                stage = "truth_read"
                stage_started = time.perf_counter()
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
                stage_runtime_ms["truth_read_ms"] = _elapsed_ms(stage_started)

                stage = "scoring"
                stage_started = time.perf_counter()
                primary_forecasts = {
                    "lk": DeterministicForecast(
                        result.rain_rate[0, lead_indices],
                        result.output_valid_mask[lead_indices],
                        result_domain[lead_indices],
                    ),
                    "persistence": DeterministicForecast(
                        result.persistence_rain_rate[lead_indices],
                        result.persistence_valid_mask[lead_indices],
                        np.ones_like(result.persistence_valid_mask[lead_indices]),
                    ),
                    "translation": DeterministicForecast(
                        result.translation_rain_rate[lead_indices],
                        result.translation_valid_mask[lead_indices],
                        translation_domain[lead_indices],
                    ),
                }
                rigorous_forecasts = {
                    **primary_forecasts,
                    "phase_correlation": DeterministicForecast(
                        phase.rate_mm_h,
                        phase.valid_mask,
                        phase.domain_valid_mask,
                    ),
                    "lk_native_10min": DeterministicForecast(
                        native_result.rain_rate[0],
                        native_result.output_valid_mask,
                        native_domain,
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
                stage_runtime_ms["scoring_runtime_ms"] = _elapsed_ms(stage_started)

                stage = "map_render"
                stage_started = time.perf_counter()
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
                        for model, forecast in rigorous_forecasts.items()
                        if model != "lk_native_10min"
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
                stage_runtime_ms["map_runtime_ms"] = _elapsed_ms(stage_started)
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
                peak_rss_bytes = resource_sampler.stop()
                runtime_rows.append(
                    {
                        "case_id": case.case_id,
                        "case_category": case.category,
                        "issue_time_utc": issue_time_utc,
                        "status": "completed",
                        **stage_runtime_ms,
                        "core_runtime_ms": (
                            stage_runtime_ms["nowcast_runtime_ms"]
                            + stage_runtime_ms["scoring_runtime_ms"]
                        ),
                        "total_runtime_ms": _elapsed_ms(issue_started),
                        "peak_rss_bytes": peak_rss_bytes,
                        "failed_stage": "",
                        "error": "",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - isolate failures by frozen issue
                peak_rss_bytes = resource_sampler.stop()
                runtime_rows.append(
                    {
                        "case_id": case.case_id,
                        "case_category": case.category,
                        "issue_time_utc": issue_time_utc,
                        "status": "failed",
                        **stage_runtime_ms,
                        "core_runtime_ms": (
                            stage_runtime_ms["nowcast_runtime_ms"]
                            + stage_runtime_ms["scoring_runtime_ms"]
                        ),
                        "total_runtime_ms": _elapsed_ms(issue_started),
                        "peak_rss_bytes": peak_rss_bytes,
                        "failed_stage": stage,
                        "error": str(exc),
                    }
                )
                errors.append(
                    {
                        "case_id": case.case_id,
                        "issue_time_utc": issue_time_utc,
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
        coverage_field=profile.coverage_gate_metric,
    )
    far_skill_summary = summarize_fss_skill(
        gate_rows,
        minimum_lead_minutes=far_minimum,
        maximum_lead_minutes=far_maximum,
        baselines=rigorous_baselines,
        minimum_forecast_to_truth_coverage=profile.coverage_minimum_ratio,
        coverage_field=profile.coverage_gate_metric,
    )
    coverage_summary = summarize_coverage(
        gate_rows,
        models=("lk", "persistence", "translation", "phase_correlation", "lk_native_10min"),
        minimum_ratio=profile.coverage_minimum_ratio,
        coverage_field=profile.coverage_gate_metric,
    )
    coverage_provenance_summary = summarize_coverage_provenance(
        gate_rows,
        models=("lk", "persistence", "translation", "phase_correlation", "lk_native_10min"),
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
    performance_summary = _summarize_performance(runtime_rows)
    summary: dict[str, object] = {
        "schema_version": "1.3",
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
        "coverage_provenance_summary": coverage_provenance_summary,
        "adaptation_summary": adaptation_summary,
        "accumulation_summary": accumulation_summary,
        "performance_summary": performance_summary,
        "runtime_fingerprint": runtime_fingerprint,
        "report_files": {
            "common_domain": "metrics.csv",
            "fixed_truth_domain": "metrics_truth_domain.csv",
            "native_cadence_comparison": "adaptation_metrics.csv",
            "accumulation": "accumulation_metrics.csv",
            "performance": "runtime_metrics.csv",
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
    _write_csv(output_directory / "runtime_metrics.csv", runtime_rows)
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
