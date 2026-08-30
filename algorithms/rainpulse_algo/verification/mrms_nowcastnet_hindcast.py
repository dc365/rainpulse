from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from rainpulse_algo.datasets.mrms_precip import (
    build_mrms_observed_sequence,
    build_mrms_validation_sequence,
)
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.nowcastnet_adapter import NowcastNetBackend, run_nowcastnet_fields
from rainpulse_algo.nowcast.nowcastnet_profile import load_nowcastnet_profile
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile
from rainpulse_algo.nowcast.pysteps_steps import run_pysteps_steps_fields
from rainpulse_algo.nowcast.steps_profile import load_pysteps_steps_profile

from .baselines import build_phase_correlation_forecast
from .map_bundle import (
    VerificationMapProfile,
    build_probabilistic_verification_map_bundle,
    load_verification_map_profile,
    write_verification_map_bundle,
    write_verification_map_index,
)
from .mrms_ensemble_hindcast import (
    CachedMRMSFrameSource,
    _aggregate_reliability,
    _decorate_score_rows,
    _fields,
    _json_safe,
)
from .mrms_hindcast import (
    MRMSArchiveFrameSource,
    MRMSFrameSource,
    _elapsed_ms,
    _IssueResourceSampler,
    _performance_distribution,
    _write_csv,
)
from .mrms_nowcastnet_profile import (
    MRMSNowcastNetProfile,
    load_mrms_nowcastnet_profile,
)
from .probabilistic import (
    score_deterministic_probability_baseline,
    score_probabilistic_forecast,
)


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    near = summary["lead_band_summary"]["near"]
    wet_near = summary["category_lead_band_summary"]["wet"]["near"]
    persistence = wet_near["nowcastnet_skill"]["persistence"]
    runtime = summary["performance_summary"]["total_runtime_ms"]
    lines = [
        f"# {summary['profile_version']} MRMS offline hindcast",
        "",
        "This is offline engineering evidence, not an operational-readiness claim.",
        "",
        f"- Split: `{summary['split']}`",
        f"- Completed issues: {summary['completed_issue_count']}",
        f"- Failed issues: {summary['failed_issue_count']}",
        (
            "- Common-support near-lead minimum coverage: "
            f"{near['minimum_common_verification_coverage']}"
        ),
        f"- Wet near-lead CRPS skill against persistence: {persistence['crps_skill']}",
        (
            "- Total issue runtime P50/P95/max (ms): "
            f"{runtime['p50']}/{runtime['p95']}/{runtime['max']}"
        ),
        "- Candidate: official NowcastNet, four raw ensemble members",
        "- Comparators: 12-member STEPS, LK, persistence, phase correlation",
        "- Verification support: truth and every forecast model valid",
        "- Product publication: disabled",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def compute_nowcastnet_grid(
    target: RegularLatLonGrid,
    *,
    height: int = 512,
    width: int = 512,
) -> tuple[RegularLatLonGrid, tuple[slice, slice]]:
    if height < target.latitude_count or width < target.longitude_count:
        raise ValueError("NowcastNet model grid cannot be smaller than the verification target")
    south_cells = (height - target.latitude_count) // 2
    north_cells = height - target.latitude_count - south_cells
    west_cells = (width - target.longitude_count) // 2
    east_cells = width - target.longitude_count - west_cells
    model_grid = RegularLatLonGrid(
        grid_id=f"{target.grid_id}_nowcastnet_{height}x{width}",
        config_version=f"{target.config_version}-nowcastnet-{height}x{width}",
        west=target.west - west_cells * target.longitude_interval_deg,
        east=target.east + east_cells * target.longitude_interval_deg,
        south=target.south - south_cells * target.latitude_interval_deg,
        north=target.north + north_cells * target.latitude_interval_deg,
        longitude_interval_deg=target.longitude_interval_deg,
        latitude_interval_deg=target.latitude_interval_deg,
        longitude_count=width,
        latitude_count=height,
        reference_latitude_deg=target.reference_latitude_deg,
        ancillary_domain_id=target.ancillary_domain_id,
    )
    crop = (
        slice(south_cells, south_cells + target.latitude_count),
        slice(west_cells, west_cells + target.longitude_count),
    )
    if (
        not np.array_equal(model_grid.latitude[crop[0]], target.latitude)
        or not np.array_equal(model_grid.longitude[crop[1]], target.longitude)
    ):
        raise ValueError("NowcastNet model grid does not crop exactly to the target")
    return model_grid, crop


def issue_random_seed(base_seed: int, case_id: str, issue_time: datetime) -> int:
    identity = f"{base_seed}:{case_id}:{issue_time.astimezone(UTC).isoformat()}".encode()
    return int.from_bytes(hashlib.sha256(identity).digest()[:4], "big")


def _crop(values: np.ndarray, crop: tuple[slice, slice]) -> np.ndarray:
    return values[..., crop[0], crop[1]]


def _reset_gpu_peak_memory(backend: NowcastNetBackend) -> None:
    reset = getattr(backend, "reset_peak_memory_stats", None)
    if callable(reset):
        reset()


def _gpu_peak_memory(backend: NowcastNetBackend) -> dict[str, int | None]:
    read = getattr(backend, "peak_memory_stats", None)
    if not callable(read):
        return {
            "gpu_peak_allocated_bytes": None,
            "gpu_peak_reserved_bytes": None,
        }
    values = read()
    return {
        "gpu_peak_allocated_bytes": values.get("gpu_peak_allocated_bytes"),
        "gpu_peak_reserved_bytes": values.get("gpu_peak_reserved_bytes"),
    }


def _coverage_rows(
    *,
    split: str,
    case_id: str,
    case_category: str,
    issue_time: datetime,
    lead_minutes: Sequence[int],
    truth_valid: np.ndarray,
    forecast_masks: Mapping[str, np.ndarray],
    ensemble_member_masks: Mapping[str, np.ndarray],
    common_valid: np.ndarray,
) -> list[dict[str, Any]]:
    issue = issue_time.isoformat().replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []
    for index, lead in enumerate(lead_minutes):
        truth = truth_valid[index]
        truth_count = int(np.count_nonzero(truth))
        common_count = int(np.count_nonzero(truth & common_valid[index]))
        for model, forecast_valid in forecast_masks.items():
            forecast_count = int(np.count_nonzero(truth & forecast_valid[index]))
            member_masks = ensemble_member_masks.get(model)
            member_mean: float | None = None
            member_minimum: float | None = None
            if member_masks is not None and truth_count:
                ratios = (
                    np.count_nonzero(
                        member_masks[:, index] & truth[np.newaxis, ...],
                        axis=(1, 2),
                    )
                    / truth_count
                )
                member_mean = float(np.mean(ratios))
                member_minimum = float(np.min(ratios))
            rows.append(
                {
                    "split": split,
                    "case_id": case_id,
                    "case_category": case_category,
                    "issue_time_utc": issue,
                    "lead_minutes": int(lead),
                    "model": model,
                    "truth_valid_cell_count": truth_count,
                    "forecast_valid_on_truth_cell_count": forecast_count,
                    "forecast_to_truth_coverage": (
                        forecast_count / truth_count if truth_count else float("nan")
                    ),
                    "common_verification_cell_count": common_count,
                    "common_verification_coverage": (
                        common_count / truth_count if truth_count else float("nan")
                    ),
                    "member_mean_coverage": member_mean,
                    "member_minimum_coverage": member_minimum,
                }
            )
    return rows


def _weighted_mean(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
    values = [
        (float(row[field]), int(row["evaluation_cell_count"]))
        for row in rows
        if int(row["evaluation_cell_count"]) > 0 and np.isfinite(float(row[field]))
    ]
    total = sum(weight for _, weight in values)
    if not total:
        return None
    return float(sum(value * weight for value, weight in values) / total)


def _skill(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline <= 0:
        return None
    return float(1.0 - candidate / baseline)


def _summarize_band(
    metric_rows: Sequence[Mapping[str, Any]],
    coverage_rows: Sequence[Mapping[str, Any]],
    *,
    minimum_lead: int,
    maximum_lead: int,
    thresholds: Sequence[float],
    models: Sequence[str],
) -> dict[str, Any]:
    selected = [
        row
        for row in metric_rows
        if minimum_lead <= int(row["lead_minutes"]) <= maximum_lead
    ]
    scores: dict[str, Any] = {}
    for model in models:
        model_rows = [row for row in selected if row["model"] == model]
        first_threshold = float(thresholds[0])
        continuous = [
            row for row in model_rows if float(row["threshold_mm_h"]) == first_threshold
        ]
        scores[model] = {
            "crps_mm_h": _weighted_mean(continuous, "crps_mm_h"),
            "ensemble_mean_rmse_mm_h": _weighted_mean(
                continuous, "ensemble_mean_rmse_mm_h"
            ),
            "mean_ensemble_spread_mm_h": _weighted_mean(
                continuous, "mean_ensemble_spread_mm_h"
            ),
            "brier_score_by_threshold": {
                str(threshold): _weighted_mean(
                    [
                        row
                        for row in model_rows
                        if float(row["threshold_mm_h"]) == float(threshold)
                    ],
                    "brier_score",
                )
                for threshold in thresholds
            },
        }
    candidate = scores["nowcastnet"]
    skills = {
        baseline: {
            "crps_skill": _skill(candidate["crps_mm_h"], scores[baseline]["crps_mm_h"]),
            "brier_skill_by_threshold": {
                str(threshold): _skill(
                    candidate["brier_score_by_threshold"][str(threshold)],
                    scores[baseline]["brier_score_by_threshold"][str(threshold)],
                )
                for threshold in thresholds
            },
        }
        for baseline in models
        if baseline != "nowcastnet"
    }
    band_coverage = [
        row
        for row in coverage_rows
        if minimum_lead <= int(row["lead_minutes"]) <= maximum_lead
    ]

    def minimum_member_coverage(model: str) -> float | None:
        values = [
            float(row["member_mean_coverage"])
            for row in band_coverage
            if row["model"] == model and row["member_mean_coverage"] is not None
        ]
        return min(values) if values else None

    return {
        "lead_minutes": [minimum_lead, maximum_lead],
        "scores": scores,
        "nowcastnet_skill": skills,
        "minimum_common_verification_coverage": (
            min(float(row["common_verification_coverage"]) for row in band_coverage)
            if band_coverage
            else None
        ),
        "minimum_nowcastnet_member_mean_coverage": minimum_member_coverage("nowcastnet"),
        "minimum_steps_member_mean_coverage": minimum_member_coverage("steps"),
    }


def conform_nowcastnet_split(
    profile: MRMSNowcastNetProfile,
    *,
    split: str,
    frame_source: MRMSFrameSource,
    case_ids: Collection[str] | None = None,
    maximum_issues: int | None = None,
) -> dict[str, Any]:
    selected_split = profile.split(split)
    cases = [
        case for case in selected_split.cases if case_ids is None or case.case_id in case_ids
    ]
    if not cases:
        raise ValueError("no RP-026 cases selected")
    checked_issues = 0
    checked_frames = 0
    errors: list[dict[str, str]] = []
    stop = False
    for case in cases:
        model_grid, _ = compute_nowcastnet_grid(
            case.grid, height=profile.model_height, width=profile.model_width
        )
        input_required: set[datetime] = set()
        truth_required: set[datetime] = set()
        for issue_time in case.issue_times:
            if maximum_issues is not None and checked_issues >= maximum_issues:
                stop = True
                break
            checked_issues += 1
            input_required.update(
                issue_time - timedelta(minutes=value) for value in range(80, -1, -10)
            )
            truth_required.update(
                issue_time + timedelta(minutes=value) for value in profile.lead_minutes
            )
        dependencies = (
            *((valid_time, model_grid) for valid_time in sorted(input_required)),
            *((valid_time, case.grid) for valid_time in sorted(truth_required)),
        )
        for valid_time, grid in dependencies:
            try:
                frame_source.read(valid_time, grid)
                checked_frames += 1
            except Exception as exc:  # noqa: BLE001 - report every frozen dependency
                errors.append(
                    {
                        "case_id": case.case_id,
                        "valid_time_utc": valid_time.isoformat().replace("+00:00", "Z"),
                        "error": str(exc),
                    }
                )
        if stop:
            break
    return {
        "schema_version": "1.0",
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "split": split,
        "selection_evidence_sha256": selected_split.selection_evidence_sha256,
        "checked_issue_count": checked_issues,
        "checked_frame_count": checked_frames,
        "failed_frame_count": len(errors),
        "complete": not errors,
        "operational_eligible": False,
        "errors": errors,
    }


def run_mrms_nowcastnet_hindcast(
    profile: MRMSNowcastNetProfile,
    *,
    split: str,
    frame_source: MRMSFrameSource,
    output_directory: Path,
    nowcastnet_backend: NowcastNetBackend,
    runtime_info: Mapping[str, Any],
    case_ids: Collection[str] | None = None,
    maximum_issues: int | None = None,
    base_random_seed: int = 20260830,
    map_profile: VerificationMapProfile | None = None,
) -> dict[str, Any]:
    if split == "holdout" and profile.gate.status != "frozen_before_holdout":
        raise ValueError("RP-026 holdout is locked until the development gate is frozen")
    selected_split = profile.split(split)
    cases = [
        case for case in selected_split.cases if case_ids is None or case.case_id in case_ids
    ]
    if not cases:
        raise ValueError("no RP-026 cases selected")
    if maximum_issues is not None and maximum_issues < 1:
        raise ValueError("maximum_issues must be positive")
    nowcastnet_profile = load_nowcastnet_profile(profile.nowcastnet_profile)
    configured_steps = load_pysteps_steps_profile(profile.steps_profile)
    configured_lk = load_pysteps_lk_profile(profile.lk_profile)
    if configured_steps.ensemble.member_count != profile.steps_member_count:
        raise ValueError("RP-026 profile and STEPS member counts differ")

    output_directory.mkdir(parents=True, exist_ok=True)
    metric_rows: list[dict[str, Any]] = []
    reliability_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    map_issues: list[dict[str, Any]] = []
    map_layer_count = 0
    errors: list[dict[str, str]] = []
    completed = 0
    nowcastnet_clipped_inputs = 0
    nowcastnet_clipped_outputs = 0
    steps_fallbacks = 0
    motion_fallbacks = 0
    phase_fallbacks = 0
    stop = False
    for case in cases:
        model_grid, crop = compute_nowcastnet_grid(
            case.grid, height=profile.model_height, width=profile.model_width
        )
        steps_profile = replace(
            configured_steps,
            grid_id=model_grid.grid_id,
            grid_config_version=model_grid.config_version,
        )
        lk_profile = replace(
            configured_lk,
            grid_id=model_grid.grid_id,
            grid_config_version=model_grid.config_version,
        )
        for issue_time in case.issue_times:
            if maximum_issues is not None and completed + len(errors) >= maximum_issues:
                stop = True
                break
            issue_key = issue_time.isoformat().replace("+00:00", "Z")
            started = time.perf_counter()
            sampler = _IssueResourceSampler()
            sampler.start()
            _reset_gpu_peak_memory(nowcastnet_backend)
            stage = "input_read"
            map_runtime_ms = 0
            try:
                input_times = tuple(
                    issue_time - timedelta(minutes=value) for value in range(80, -1, -10)
                )
                input_frames = {
                    valid_time: frame_source.read(valid_time, model_grid)
                    for valid_time in input_times
                }
                rate = np.stack([input_frames[value].rate_mm_h for value in input_times])
                valid = np.stack([input_frames[value].valid_mask for value in input_times])
                seed = issue_random_seed(base_random_seed, case.case_id, issue_time)
                stage = "nowcastnet_forecast"
                nowcastnet_started = time.perf_counter()
                nowcastnet_result = run_nowcastnet_fields(
                    rate,
                    valid,
                    profile=nowcastnet_profile,
                    backend=nowcastnet_backend,
                    random_seed=seed,
                )
                nowcastnet_runtime_ms = _elapsed_ms(nowcastnet_started)
                baseline_times = tuple(
                    issue_time - timedelta(minutes=value) for value in (20, 10, 0)
                )
                baseline_frames = {value: input_frames[value] for value in baseline_times}
                adapted = build_mrms_validation_sequence(
                    baseline_frames, issue_time, model_grid
                )
                observed = build_mrms_observed_sequence(
                    baseline_frames, issue_time, model_grid
                )
                stage = "steps_forecast"
                steps_started = time.perf_counter()
                steps_result = run_pysteps_steps_fields(
                    _fields(adapted),
                    profile=steps_profile,
                    lk_profile=lk_profile,
                    grid=model_grid,
                )
                steps_runtime_ms = _elapsed_ms(steps_started)
                phase = build_phase_correlation_forecast(
                    observed.rate_mm_h[-2],
                    observed.rate_mm_h[-1],
                    observed.valid_mask[-2],
                    observed.valid_mask[-1],
                    lead_minutes=profile.lead_minutes,
                    source_interval_minutes=profile.source_cadence_minutes,
                )
                stage = "truth_read"
                truth_frames = tuple(
                    frame_source.read(issue_time + timedelta(minutes=lead), case.grid)
                    for lead in profile.lead_minutes
                )
                truth_rate = np.stack([frame.rate_mm_h for frame in truth_frames])
                truth_valid = np.stack([frame.valid_mask for frame in truth_frames]) == 1
                indices = np.asarray([lead // 5 - 1 for lead in profile.lead_minutes])
                nowcastnet_members = _crop(
                    nowcastnet_result.rain_rate_mm_h[:, : len(profile.lead_minutes)], crop
                )
                nowcastnet_member_valid = _crop(
                    nowcastnet_result.valid_mask[:, : len(profile.lead_minutes)], crop
                ) == 1
                steps_members = _crop(steps_result.rain_rate[:, indices], crop)
                steps_member_valid = _crop(
                    steps_result.member_valid_mask[:, indices], crop
                ) == 1
                deterministic = steps_result.deterministic
                forecasts = {
                    "lk": _crop(deterministic.rain_rate[0, indices], crop),
                    "persistence": _crop(
                        deterministic.persistence_rain_rate[indices], crop
                    ),
                    "phase_correlation": _crop(phase.rate_mm_h, crop),
                }
                forecast_masks = {
                    "nowcastnet": np.all(nowcastnet_member_valid, axis=0),
                    "steps": _crop(steps_result.output_valid_mask[indices], crop) == 1,
                    "lk": _crop(deterministic.output_valid_mask[indices], crop) == 1,
                    "persistence": _crop(
                        deterministic.persistence_valid_mask[indices], crop
                    )
                    == 1,
                    "phase_correlation": _crop(phase.valid_mask, crop) == 1,
                }
                common_valid = truth_valid.copy()
                for value in forecast_masks.values():
                    common_valid &= value
                stage = "scoring"
                scoring_started = time.perf_counter()
                for model, members in (
                    ("nowcastnet", nowcastnet_members),
                    ("steps", steps_members),
                ):
                    score_rows = score_probabilistic_forecast(
                        truth_rate,
                        truth_valid,
                        members,
                        common_valid,
                        lead_minutes=profile.lead_minutes,
                        thresholds_mm_h=profile.thresholds_mm_h,
                        reliability_bin_edges=profile.reliability_bin_edges,
                    )
                    decorated, reliability = _decorate_score_rows(
                        score_rows,
                        model=model,
                        split=split,
                        case_id=case.case_id,
                        case_category=case.category,
                        issue_time=issue_time,
                    )
                    metric_rows.extend(decorated)
                    reliability_rows.extend(reliability)
                for model, values in forecasts.items():
                    score_rows = score_deterministic_probability_baseline(
                        truth_rate,
                        truth_valid,
                        values,
                        common_valid,
                        lead_minutes=profile.lead_minutes,
                        thresholds_mm_h=profile.thresholds_mm_h,
                        reliability_bin_edges=profile.reliability_bin_edges,
                    )
                    decorated, reliability = _decorate_score_rows(
                        score_rows,
                        model=model,
                        split=split,
                        case_id=case.case_id,
                        case_category=case.category,
                        issue_time=issue_time,
                    )
                    metric_rows.extend(decorated)
                    reliability_rows.extend(reliability)
                coverage_rows.extend(
                    _coverage_rows(
                        split=split,
                        case_id=case.case_id,
                        case_category=case.category,
                        issue_time=issue_time,
                        lead_minutes=profile.lead_minutes,
                        truth_valid=truth_valid,
                        forecast_masks=forecast_masks,
                        ensemble_member_masks={
                            "nowcastnet": nowcastnet_member_valid,
                            "steps": steps_member_valid,
                        },
                        common_valid=common_valid,
                    )
                )
                scoring_runtime_ms = _elapsed_ms(scoring_started)
                if map_profile is not None:
                    stage = "map_render"
                    map_started = time.perf_counter()
                    map_manifest, map_objects = build_probabilistic_verification_map_bundle(
                        profile=map_profile,
                        verification_profile_version=profile.profile_version,
                        case_id=case.case_id,
                        truth_kind="observed_mrms_preciprate_10min",
                        issue_time=issue_time,
                        lead_minutes=profile.lead_minutes,
                        grid=case.grid,
                        truth_rate=truth_rate,
                        truth_valid=truth_valid,
                        nowcastnet_members=nowcastnet_members,
                        nowcastnet_member_valid=nowcastnet_member_valid,
                        steps_members=steps_members,
                        steps_member_valid=steps_member_valid,
                        deterministic_forecasts={
                            model: (values, forecast_masks[model])
                            for model, values in forecasts.items()
                        },
                        velocity_pixels_per_step=_crop(
                            deterministic.velocity_pixels_per_step, crop
                        ),
                        motion_valid_mask=_crop(deterministic.motion_valid_mask, crop),
                        motion_fallback_used=deterministic.motion_fallback_used,
                        motion_fallback_reason=deterministic.motion_fallback_reason,
                        motion_feature_count=deterministic.motion_feature_count,
                        trackable_rain_pixel_count=deterministic.trackable_rain_pixel_count,
                    )
                    write_verification_map_bundle(
                        output_directory / "maps", map_manifest, map_objects
                    )
                    map_runtime_ms = _elapsed_ms(map_started)
                    map_layer_count += len(map_manifest["layers"])
                    map_issues.append(
                        {
                            "case_id": case.case_id,
                            "case_category": case.category,
                            "issue_time_utc": issue_key,
                            "issue_key": map_manifest["issue_key"],
                            "manifest_path": (
                                Path(case.case_id)
                                / str(map_manifest["issue_key"])
                                / "manifest.json"
                            ).as_posix(),
                            "layer_count": len(map_manifest["layers"]),
                        }
                    )
                completed += 1
                nowcastnet_clipped_inputs += nowcastnet_result.clipped_input_pixel_count
                nowcastnet_clipped_outputs += (
                    nowcastnet_result.clipped_negative_output_pixel_count
                )
                steps_fallbacks += int(steps_result.ensemble_fallback_used)
                motion_fallbacks += int(deterministic.motion_fallback_used)
                phase_fallbacks += int(phase.estimate.fallback_used)
                runtime_rows.append(
                    {
                        "split": split,
                        "case_id": case.case_id,
                        "case_category": case.category,
                        "issue_time_utc": issue_key,
                        "status": "completed",
                        "random_seed": seed,
                        "nowcastnet_runtime_ms": nowcastnet_runtime_ms,
                        "steps_runtime_ms": steps_runtime_ms,
                        "scoring_runtime_ms": scoring_runtime_ms,
                        "map_runtime_ms": map_runtime_ms,
                        "total_runtime_ms": _elapsed_ms(started),
                        "peak_rss_bytes": sampler.stop(),
                        **_gpu_peak_memory(nowcastnet_backend),
                        "failed_stage": "",
                        "error": "",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - isolate every frozen issue
                runtime_rows.append(
                    {
                        "split": split,
                        "case_id": case.case_id,
                        "case_category": case.category,
                        "issue_time_utc": issue_key,
                        "status": "failed",
                        "random_seed": None,
                        "nowcastnet_runtime_ms": None,
                        "steps_runtime_ms": None,
                        "scoring_runtime_ms": None,
                        "map_runtime_ms": None,
                        "total_runtime_ms": _elapsed_ms(started),
                        "peak_rss_bytes": sampler.stop(),
                        **_gpu_peak_memory(nowcastnet_backend),
                        "failed_stage": stage,
                        "error": str(exc),
                    }
                )
                errors.append(
                    {"case_id": case.case_id, "issue_time_utc": issue_key, "error": str(exc)}
                )
        if stop:
            break

    reliability_summary = _aggregate_reliability(reliability_rows)
    models = profile.comparison_models

    def band(rows: Sequence[Mapping[str, Any]], coverage: Sequence[Mapping[str, Any]], near: bool):
        limits = profile.near_lead_minutes if near else profile.far_lead_minutes
        return _summarize_band(
            rows,
            coverage,
            minimum_lead=limits[0],
            maximum_lead=limits[1],
            thresholds=profile.thresholds_mm_h,
            models=models,
        )

    summary = _json_safe(
        {
            "schema_version": "1.0",
            "profile_version": profile.profile_version,
            "profile_sha256": profile.profile_sha256,
            "configuration_sha256": profile.configuration_sha256,
            "split": split,
            "selection_evidence_sha256": selected_split.selection_evidence_sha256,
            "operational_eligible": False,
            "product_publication_enabled": False,
            "primary_truth_kind": "observed_mrms_preciprate_10min",
            "calibration_status": "raw_ensemble_relative_frequency_uncalibrated",
            "models": list(models),
            "lead_minutes": list(profile.lead_minutes),
            "thresholds_mm_h": list(profile.thresholds_mm_h),
            "nowcastnet_member_count": profile.nowcastnet_member_count,
            "steps_member_count": profile.steps_member_count,
            "model_grid_shape": [profile.model_height, profile.model_width],
            "completed_issue_count": completed,
            "failed_issue_count": len(errors),
            "metric_row_count": len(metric_rows),
            "coverage_row_count": len(coverage_rows),
            "reliability_row_count": len(reliability_rows),
            "nowcastnet_clipped_input_pixel_count": nowcastnet_clipped_inputs,
            "nowcastnet_clipped_negative_output_pixel_count": nowcastnet_clipped_outputs,
            "steps_ensemble_fallback_issue_count": steps_fallbacks,
            "motion_fallback_issue_count": motion_fallbacks,
            "phase_correlation_fallback_issue_count": phase_fallbacks,
            "map_bundle_count": len(map_issues),
            "map_layer_count": map_layer_count,
            "map_renderer_version": map_profile.renderer_version if map_profile else "",
            "errors": errors,
            "runtime": dict(runtime_info),
            "performance_summary": {
                field: _performance_distribution(runtime_rows, field)
                for field in (
                    "nowcastnet_runtime_ms",
                    "steps_runtime_ms",
                    "scoring_runtime_ms",
                    "map_runtime_ms",
                    "total_runtime_ms",
                    "peak_rss_bytes",
                    "gpu_peak_allocated_bytes",
                    "gpu_peak_reserved_bytes",
                )
            },
            "lead_band_summary": {
                "near": band(metric_rows, coverage_rows, True),
                "far": band(metric_rows, coverage_rows, False),
            },
            "category_lead_band_summary": {
                category: {
                    "near": band(
                        [row for row in metric_rows if row["case_category"] == category],
                        [row for row in coverage_rows if row["case_category"] == category],
                        True,
                    ),
                    "far": band(
                        [row for row in metric_rows if row["case_category"] == category],
                        [row for row in coverage_rows if row["case_category"] == category],
                        False,
                    ),
                }
                for category in ("wet", "dry")
            },
            "report_files": {
                "probabilistic_metrics": "probabilistic_metrics.csv",
                "coverage": "coverage.csv",
                "reliability": "reliability.json",
                "runtime": "runtime_metrics.csv",
            },
        }
    )
    if map_profile is not None:
        write_verification_map_index(
            output_directory / "maps",
            verification_profile_version=profile.profile_version,
            renderer_version=map_profile.renderer_version,
            issues=map_issues,
            layer_count=map_layer_count,
        )
    _write_csv(output_directory / "probabilistic_metrics.csv", metric_rows)
    _write_csv(output_directory / "coverage.csv", coverage_rows)
    _write_csv(output_directory / "runtime_metrics.csv", runtime_rows)
    (output_directory / "reliability.json").write_text(
        json.dumps(_json_safe(reliability_summary), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_report(output_directory / "report.md", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run frozen RP-026 MRMS verification")
    parser.add_argument("command", choices=("conformance", "hindcast"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "holdout"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("runtime/reports/mrms"))
    parser.add_argument("--capsule-root", type=Path)
    parser.add_argument(
        "--map-profile",
        type=Path,
        default=Path("configs/verification/algorithm-map-v1.yaml"),
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--run-id")
    parser.add_argument("--case", action="append")
    parser.add_argument("--max-issues", type=int)
    parser.add_argument("--skip-hash", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    profile_path = args.profile
    if not profile_path.is_absolute():
        profile_path = repository_root / profile_path
    try:
        profile = load_mrms_nowcastnet_profile(profile_path, repository_root=repository_root)
        source = CachedMRMSFrameSource(
            MRMSArchiveFrameSource(
                args.root,
                cadence_minutes=profile.source_cadence_minutes,
                verify_hash=not args.skip_hash,
            ),
            maximum_frames=160,
        )
        selected = set(args.case) if args.case else None
        if args.command == "conformance":
            report = conform_nowcastnet_split(
                profile,
                split=args.split,
                frame_source=source,
                case_ids=selected,
                maximum_issues=args.max_issues,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["complete"] else 1
        if args.capsule_root is None:
            raise ValueError("--capsule-root is required for RP-026 hindcast")
        nowcastnet_profile = load_nowcastnet_profile(profile.nowcastnet_profile)
        from rainpulse_algo.nowcast.nowcastnet_official_backend import (
            OfficialNowcastNetBackend,
        )

        backend = OfficialNowcastNetBackend(
            args.capsule_root,
            profile=nowcastnet_profile,
            device=args.device,
        )
        map_profile_path = args.map_profile
        if not map_profile_path.is_absolute():
            map_profile_path = repository_root / map_profile_path
        map_profile = load_verification_map_profile(map_profile_path)
        run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = args.output_root / profile.profile_version / args.split / run_id
        summary = run_mrms_nowcastnet_hindcast(
            profile,
            split=args.split,
            frame_source=source,
            output_directory=output,
            nowcastnet_backend=backend,
            runtime_info=backend.runtime_info(),
            case_ids=selected,
            maximum_issues=args.max_issues,
            map_profile=map_profile,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not summary["errors"] else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
