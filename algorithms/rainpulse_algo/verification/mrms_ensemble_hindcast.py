from __future__ import annotations

import argparse
import json
import sys
import time
from collections import OrderedDict
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
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKFields
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile
from rainpulse_algo.nowcast.pysteps_steps import run_pysteps_steps_fields
from rainpulse_algo.nowcast.steps_profile import load_pysteps_steps_profile

from .baselines import build_phase_correlation_forecast
from .mrms_ensemble_gate import evaluate_ensemble_summary
from .mrms_ensemble_profile import MRMSEnsembleProfile, load_mrms_ensemble_profile
from .mrms_hindcast import (
    MRMSArchiveFrameSource,
    MRMSFrameSource,
    _elapsed_ms,
    _IssueResourceSampler,
    _performance_distribution,
    _runtime_fingerprint,
    _write_csv,
)
from .probabilistic import (
    score_deterministic_probability_baseline,
    score_probabilistic_forecast,
)


class CachedMRMSFrameSource:
    """Bound repeated issue-window reads without changing immutable frame semantics."""

    def __init__(self, source: MRMSFrameSource, *, maximum_frames: int = 96):
        if maximum_frames < 1:
            raise ValueError("MRMS frame cache must retain at least one frame")
        self.source = source
        self.maximum_frames = maximum_frames
        self._frames: OrderedDict[
            tuple[datetime, str, str], Any
        ] = OrderedDict()

    def read(self, valid_time: datetime, grid) -> Any:
        key = (valid_time, grid.grid_id, grid.coordinate_sha256)
        cached = self._frames.get(key)
        if cached is not None:
            self._frames.move_to_end(key)
            return cached
        frame = self.source.read(valid_time, grid)
        self._frames[key] = frame
        self._frames.move_to_end(key)
        while len(self._frames) > self.maximum_frames:
            self._frames.popitem(last=False)
        return frame


def _compute_grid(
    target: RegularLatLonGrid,
    halo_cells: int,
) -> tuple[RegularLatLonGrid, tuple[slice, slice]]:
    if halo_cells < 1:
        raise ValueError("RP-024 compute halo must contain at least one cell")
    latitude_count = target.latitude_count + 2 * halo_cells
    longitude_count = target.longitude_count + 2 * halo_cells
    compute = RegularLatLonGrid(
        grid_id=f"{target.grid_id}_compute_halo_{halo_cells}",
        config_version=f"{target.config_version}-compute-halo-{halo_cells}",
        west=target.west - halo_cells * target.longitude_interval_deg,
        east=target.east + halo_cells * target.longitude_interval_deg,
        south=target.south - halo_cells * target.latitude_interval_deg,
        north=target.north + halo_cells * target.latitude_interval_deg,
        longitude_interval_deg=target.longitude_interval_deg,
        latitude_interval_deg=target.latitude_interval_deg,
        longitude_count=longitude_count,
        latitude_count=latitude_count,
        reference_latitude_deg=target.reference_latitude_deg,
        ancillary_domain_id=target.ancillary_domain_id,
    )
    crop = (
        slice(halo_cells, halo_cells + target.latitude_count),
        slice(halo_cells, halo_cells + target.longitude_count),
    )
    if (
        not np.array_equal(compute.latitude[crop[0]], target.latitude)
        or not np.array_equal(compute.longitude[crop[1]], target.longitude)
    ):
        raise ValueError("RP-024 compute halo does not crop exactly to the target grid")
    return compute, crop


def _fields(sequence) -> PystepsLKFields:
    return PystepsLKFields(
        reflectivity_dbz=sequence.reflectivity_dbz,
        rate_mm_h=sequence.rate_mm_h,
        quality_index=sequence.quality_index,
        valid_mask=sequence.valid_mask,
        low_quality_mask=sequence.low_quality_mask,
    )


def _decorate_score_rows(
    rows: Sequence[dict[str, Any]],
    *,
    model: str,
    split: str,
    case_id: str,
    case_category: str,
    issue_time: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics: list[dict[str, Any]] = []
    reliability: list[dict[str, Any]] = []
    issue = issue_time.isoformat().replace("+00:00", "Z")
    for source in rows:
        row = dict(source)
        bins = row.pop("reliability")
        identity = {
            "split": split,
            "case_id": case_id,
            "case_category": case_category,
            "issue_time_utc": issue,
            "model": model,
        }
        metrics.append({**identity, **row})
        reliability.extend(
            {
                **identity,
                "lead_minutes": row["lead_minutes"],
                "threshold_mm_h": row["threshold_mm_h"],
                **value,
            }
            for value in bins
        )
    return metrics, reliability


def _coverage_rows(
    *,
    split: str,
    case_id: str,
    case_category: str,
    issue_time: datetime,
    lead_minutes: Sequence[int],
    truth_valid: np.ndarray,
    forecast_masks: Mapping[str, np.ndarray],
    member_valid: np.ndarray,
    common_valid: np.ndarray,
) -> list[dict[str, Any]]:
    issue = issue_time.isoformat().replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []
    for index, lead in enumerate(lead_minutes):
        truth = truth_valid[index]
        truth_count = int(np.count_nonzero(truth))
        common_count = int(np.count_nonzero(truth & common_valid[index]))
        member_ratios = (
            np.count_nonzero(member_valid[:, index] & truth[np.newaxis, ...], axis=(1, 2))
            / truth_count
            if truth_count
            else np.full(member_valid.shape[0], np.nan)
        )
        for model, forecast_valid in forecast_masks.items():
            forecast_count = int(np.count_nonzero(truth & forecast_valid[index]))
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
                    "steps_member_mean_coverage": (
                        float(np.mean(member_ratios)) if truth_count else float("nan")
                    ),
                    "steps_member_minimum_coverage": (
                        float(np.min(member_ratios)) if truth_count else float("nan")
                    ),
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
) -> dict[str, Any]:
    selected = [
        row
        for row in metric_rows
        if minimum_lead <= int(row["lead_minutes"]) <= maximum_lead
    ]
    models = ("steps", "lk", "persistence", "phase_correlation")
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
                continuous,
                "ensemble_mean_rmse_mm_h",
            ),
            "mean_ensemble_spread_mm_h": _weighted_mean(
                continuous,
                "mean_ensemble_spread_mm_h",
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
    steps_scores = scores["steps"]
    skill: dict[str, Any] = {}
    for baseline in ("lk", "persistence", "phase_correlation"):
        skill[baseline] = {
            "crps_skill": _skill(
                steps_scores["crps_mm_h"],
                scores[baseline]["crps_mm_h"],
            ),
            "brier_skill_by_threshold": {
                str(threshold): _skill(
                    steps_scores["brier_score_by_threshold"][str(threshold)],
                    scores[baseline]["brier_score_by_threshold"][str(threshold)],
                )
                for threshold in thresholds
            },
        }
    band_coverage = [
        row
        for row in coverage_rows
        if minimum_lead <= int(row["lead_minutes"]) <= maximum_lead
        and row["model"] == "steps"
    ]
    return {
        "lead_minutes": [minimum_lead, maximum_lead],
        "scores": scores,
        "steps_skill_against_deterministic_baselines": skill,
        "minimum_common_verification_coverage": (
            min(float(row["common_verification_coverage"]) for row in band_coverage)
            if band_coverage
            else None
        ),
        "minimum_steps_member_mean_coverage": (
            min(float(row["steps_member_mean_coverage"]) for row in band_coverage)
            if band_coverage
            else None
        ),
    }


def _aggregate_reliability(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for row in rows:
        lead = int(row["lead_minutes"])
        band = "near" if lead <= 60 else "far"
        key = (
            row["model"],
            band,
            float(row["threshold_mm_h"]),
            float(row["lower_probability"]),
            float(row["upper_probability"]),
            bool(row["includes_upper"]),
        )
        grouped.setdefault(key, []).append(row)
    output: list[dict[str, Any]] = []
    for key, values in sorted(grouped.items(), key=lambda item: item[0]):
        counts = [int(value["forecast_count"]) for value in values]
        total = sum(counts)

        def weighted(field: str) -> float | None:
            if not total:
                return None
            return float(
                sum(
                    float(value[field]) * count
                    for value, count in zip(values, counts, strict=True)
                    if count > 0
                )
                / total
            )

        output.append(
            {
                "model": key[0],
                "lead_band": key[1],
                "threshold_mm_h": key[2],
                "lower_probability": key[3],
                "upper_probability": key[4],
                "includes_upper": key[5],
                "forecast_count": total,
                "mean_forecast_probability": weighted("mean_forecast_probability"),
                "observed_frequency": weighted("observed_frequency"),
            }
        )
    return output


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    near = summary["lead_band_summary"]["near"]
    far = summary["lead_band_summary"]["far"]
    runtime = summary["performance_summary"]

    def display(value: Any, digits: int = 4) -> str:
        return "n/a" if value is None else f"{float(value):.{digits}f}"

    def band_row(name: str, value: Mapping[str, Any]) -> str:
        scores = value["scores"]
        skill = value["steps_skill_against_deterministic_baselines"]["persistence"]
        return "| {} | {} | {} | {} | {} |".format(
            name,
            display(scores["steps"]["crps_mm_h"]),
            display(skill["crps_skill"]),
            display(value["minimum_common_verification_coverage"]),
            display(value["minimum_steps_member_mean_coverage"]),
        )

    lines = [
        f"# {summary['profile_version']} {summary['split']} ensemble hindcast",
        "",
        "This is an offline engineering evaluation and is not operationally eligible.",
        "",
        f"- Completed issues: {summary['completed_issue_count']}",
        f"- Failed issues: {summary['failed_issue_count']}",
        f"- Ensemble members: {summary['member_count']}",
        "- Probability status: raw ensemble relative frequency, uncalibrated",
        "- Verification support: common truth-valid support across STEPS and all baselines",
        "- Baselines: LK, persistence, independent phase-correlation translation",
        "- Lead bands: 10–60 and 70–120 minutes",
        (
            "- Total runtime P50/P95/max (ms): "
            f"{runtime['total_runtime_ms']['p50']}/"
            f"{runtime['total_runtime_ms']['p95']}/"
            f"{runtime['total_runtime_ms']['max']}"
        ),
        (
            "- Peak RSS P50/P95/max (bytes): "
            f"{runtime['peak_rss_bytes']['p50']}/"
            f"{runtime['peak_rss_bytes']['p95']}/"
            f"{runtime['peak_rss_bytes']['max']}"
        ),
        "",
        (
            "| Lead band | STEPS CRPS (mm/h) | CRPS skill vs persistence | "
            "Minimum common coverage | Minimum member-mean coverage |"
        ),
        "|---|---:|---:|---:|---:|",
        band_row("10–60 min", near),
        band_row("70–120 min", far),
        "",
    ]
    if summary.get("holdout_gate_evaluation") is not None:
        evaluation = summary["holdout_gate_evaluation"]
        lines.extend(
            [
                f"- Frozen independent gate passed: `{evaluation['passed']}`",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def conform_ensemble_split(
    profile: MRMSEnsembleProfile,
    *,
    split: str,
    frame_source: MRMSFrameSource,
    case_ids: Collection[str] | None = None,
    maximum_issues: int | None = None,
) -> dict[str, Any]:
    selected_split = profile.split(split)
    cases = [
        case
        for case in selected_split.cases
        if case_ids is None or case.case_id in case_ids
    ]
    if not cases:
        raise ValueError("no RP-024 cases selected")
    checked_issues = 0
    checked_frames = 0
    errors: list[dict[str, str]] = []
    stop = False
    for case in cases:
        compute_grid, _ = _compute_grid(case.grid, profile.compute_halo_cells)
        input_required: set[datetime] = set()
        truth_required: set[datetime] = set()
        for issue_time in case.issue_times:
            if maximum_issues is not None and checked_issues >= maximum_issues:
                stop = True
                break
            checked_issues += 1
            input_required.update(
                issue_time - timedelta(minutes=value) for value in (20, 10, 0)
            )
            truth_required.update(
                issue_time + timedelta(minutes=value) for value in profile.lead_minutes
            )
        dependencies = (
            *((valid_time, compute_grid) for valid_time in sorted(input_required)),
            *((valid_time, case.grid) for valid_time in sorted(truth_required)),
        )
        for valid_time, dependency_grid in dependencies:
            try:
                frame_source.read(valid_time, dependency_grid)
                checked_frames += 1
            except Exception as exc:  # noqa: BLE001 - report every frozen input failure
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


def run_mrms_ensemble_hindcast(
    profile: MRMSEnsembleProfile,
    *,
    split: str,
    repository_root: Path,
    frame_source: MRMSFrameSource,
    output_directory: Path,
    case_ids: Collection[str] | None = None,
    maximum_issues: int | None = None,
) -> dict[str, Any]:
    if split == "holdout" and profile.gate.status != "frozen_before_holdout":
        raise ValueError("RP-024 holdout forecast is locked until the development gate is frozen")
    selected_split = profile.split(split)
    cases = [
        case
        for case in selected_split.cases
        if case_ids is None or case.case_id in case_ids
    ]
    if not cases:
        raise ValueError("no RP-024 cases selected")
    if maximum_issues is not None and maximum_issues < 1:
        raise ValueError("maximum_issues must be positive")
    configured_steps = load_pysteps_steps_profile(profile.steps_profile)
    configured_lk = load_pysteps_lk_profile(profile.lk_profile)
    if configured_steps.ensemble.member_count != profile.member_count:
        raise ValueError("RP-024 profile and STEPS member counts differ")

    output_directory.mkdir(parents=True, exist_ok=True)
    metric_rows: list[dict[str, Any]] = []
    reliability_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    completed = 0
    ensemble_fallbacks = 0
    motion_fallbacks = 0
    phase_fallbacks = 0
    stop = False
    for case in cases:
        compute_grid, crop = _compute_grid(case.grid, profile.compute_halo_cells)
        steps_profile = replace(
            configured_steps,
            grid_id=compute_grid.grid_id,
            grid_config_version=compute_grid.config_version,
        )
        lk_profile = replace(
            configured_lk,
            grid_id=compute_grid.grid_id,
            grid_config_version=compute_grid.config_version,
        )
        for issue_time in case.issue_times:
            if maximum_issues is not None and completed + len(errors) >= maximum_issues:
                stop = True
                break
            issue_key = issue_time.isoformat().replace("+00:00", "Z")
            started = time.perf_counter()
            sampler = _IssueResourceSampler()
            sampler.start()
            stage = "input_read"
            try:
                input_times = tuple(
                    issue_time - timedelta(minutes=value) for value in (20, 10, 0)
                )
                input_frames = {
                    valid_time: frame_source.read(valid_time, compute_grid)
                    for valid_time in input_times
                }
                adapted = build_mrms_validation_sequence(
                    input_frames,
                    issue_time,
                    compute_grid,
                )
                observed = build_mrms_observed_sequence(
                    input_frames,
                    issue_time,
                    compute_grid,
                )
                stage = "forecast"
                forecast_started = time.perf_counter()
                result = run_pysteps_steps_fields(
                    _fields(adapted),
                    profile=steps_profile,
                    lk_profile=lk_profile,
                    grid=compute_grid,
                )
                phase = build_phase_correlation_forecast(
                    observed.rate_mm_h[-2],
                    observed.rate_mm_h[-1],
                    observed.valid_mask[-2],
                    observed.valid_mask[-1],
                    lead_minutes=profile.lead_minutes,
                    source_interval_minutes=profile.source_cadence_minutes,
                )
                forecast_runtime_ms = _elapsed_ms(forecast_started)
                stage = "truth_read"
                truth_frames = tuple(
                    frame_source.read(issue_time + timedelta(minutes=lead), case.grid)
                    for lead in profile.lead_minutes
                )
                truth_rate = np.stack([frame.rate_mm_h for frame in truth_frames])
                truth_valid = np.stack([frame.valid_mask for frame in truth_frames]) == 1
                indices = np.asarray([lead // 5 - 1 for lead in profile.lead_minutes])
                members = result.rain_rate[:, indices, crop[0], crop[1]]
                member_valid = (
                    result.member_valid_mask[:, indices, crop[0], crop[1]] == 1
                )
                steps_valid = result.output_valid_mask[indices, crop[0], crop[1]] == 1
                deterministic = result.deterministic
                forecasts = {
                    "lk": deterministic.rain_rate[0, indices, crop[0], crop[1]],
                    "persistence": deterministic.persistence_rain_rate[
                        indices, crop[0], crop[1]
                    ],
                    "phase_correlation": phase.rate_mm_h[:, crop[0], crop[1]],
                }
                forecast_masks = {
                    "steps": steps_valid,
                    "lk": (
                        deterministic.output_valid_mask[indices, crop[0], crop[1]] == 1
                    ),
                    "persistence": (
                        deterministic.persistence_valid_mask[
                            indices, crop[0], crop[1]
                        ]
                        == 1
                    ),
                    "phase_correlation": phase.valid_mask[:, crop[0], crop[1]] == 1,
                }
                common_valid = truth_valid.copy()
                for value in forecast_masks.values():
                    common_valid &= value
                stage = "scoring"
                scoring_started = time.perf_counter()
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
                    model="steps",
                    split=split,
                    case_id=case.case_id,
                    case_category=case.category,
                    issue_time=issue_time,
                )
                metric_rows.extend(decorated)
                reliability_rows.extend(reliability)
                for model, values in forecasts.items():
                    baseline_rows = score_deterministic_probability_baseline(
                        truth_rate,
                        truth_valid,
                        values,
                        common_valid,
                        lead_minutes=profile.lead_minutes,
                        thresholds_mm_h=profile.thresholds_mm_h,
                        reliability_bin_edges=profile.reliability_bin_edges,
                    )
                    decorated, reliability = _decorate_score_rows(
                        baseline_rows,
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
                        member_valid=member_valid,
                        common_valid=common_valid,
                    )
                )
                scoring_runtime_ms = _elapsed_ms(scoring_started)
                completed += 1
                ensemble_fallbacks += int(result.ensemble_fallback_used)
                motion_fallbacks += int(result.deterministic.motion_fallback_used)
                phase_fallbacks += int(phase.estimate.fallback_used)
                runtime_rows.append(
                    {
                        "split": split,
                        "case_id": case.case_id,
                        "case_category": case.category,
                        "issue_time_utc": issue_key,
                        "status": "completed",
                        "forecast_runtime_ms": forecast_runtime_ms,
                        "scoring_runtime_ms": scoring_runtime_ms,
                        "total_runtime_ms": _elapsed_ms(started),
                        "peak_rss_bytes": sampler.stop(),
                        "failed_stage": "",
                        "error": "",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - isolate each frozen issue
                runtime_rows.append(
                    {
                        "split": split,
                        "case_id": case.case_id,
                        "case_category": case.category,
                        "issue_time_utc": issue_key,
                        "status": "failed",
                        "forecast_runtime_ms": None,
                        "scoring_runtime_ms": None,
                        "total_runtime_ms": _elapsed_ms(started),
                        "peak_rss_bytes": sampler.stop(),
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
    summary = _json_safe(
        {
            "schema_version": "1.0",
            "profile_version": profile.profile_version,
            "profile_sha256": profile.profile_sha256,
            "configuration_sha256": profile.configuration_sha256,
            "split": split,
            "selection_evidence_sha256": selected_split.selection_evidence_sha256,
            "operational_eligible": False,
            "calibration_status": "raw_ensemble_relative_frequency_uncalibrated",
            "member_count": profile.member_count,
            "compute_halo_cells": profile.compute_halo_cells,
            "compute_halo_degrees": (
                profile.compute_halo_cells * cases[0].grid.longitude_interval_deg
            ),
            "completed_issue_count": completed,
            "failed_issue_count": len(errors),
            "ensemble_fallback_issue_count": ensemble_fallbacks,
            "motion_fallback_issue_count": motion_fallbacks,
            "phase_correlation_fallback_issue_count": phase_fallbacks,
            "metric_row_count": len(metric_rows),
            "coverage_row_count": len(coverage_rows),
            "reliability_row_count": len(reliability_rows),
            "errors": errors,
            "performance_summary": {
                "schema_version": "1.0",
                "rss_scope": "whole_process_resident_set_sampled_per_issue",
                "rss_sample_interval_ms": 50,
                "forecast_runtime_ms": _performance_distribution(
                    runtime_rows,
                    "forecast_runtime_ms",
                ),
                "scoring_runtime_ms": _performance_distribution(
                    runtime_rows,
                    "scoring_runtime_ms",
                ),
                "total_runtime_ms": _performance_distribution(
                    runtime_rows,
                    "total_runtime_ms",
                ),
                "peak_rss_bytes": _performance_distribution(
                    runtime_rows,
                    "peak_rss_bytes",
                ),
            },
            "lead_band_summary": {
                "near": _summarize_band(
                    metric_rows,
                    coverage_rows,
                    minimum_lead=profile.near_lead_minutes[0],
                    maximum_lead=profile.near_lead_minutes[1],
                    thresholds=profile.thresholds_mm_h,
                ),
                "far": _summarize_band(
                    metric_rows,
                    coverage_rows,
                    minimum_lead=profile.far_lead_minutes[0],
                    maximum_lead=profile.far_lead_minutes[1],
                    thresholds=profile.thresholds_mm_h,
                ),
            },
            "category_lead_band_summary": {
                category: {
                    "near": _summarize_band(
                        [
                            row
                            for row in metric_rows
                            if row["case_category"] == category
                        ],
                        [
                            row
                            for row in coverage_rows
                            if row["case_category"] == category
                        ],
                        minimum_lead=profile.near_lead_minutes[0],
                        maximum_lead=profile.near_lead_minutes[1],
                        thresholds=profile.thresholds_mm_h,
                    ),
                    "far": _summarize_band(
                        [
                            row
                            for row in metric_rows
                            if row["case_category"] == category
                        ],
                        [
                            row
                            for row in coverage_rows
                            if row["case_category"] == category
                        ],
                        minimum_lead=profile.far_lead_minutes[0],
                        maximum_lead=profile.far_lead_minutes[1],
                        thresholds=profile.thresholds_mm_h,
                    ),
                }
                for category in ("wet", "dry")
            },
            "runtime_fingerprint": _runtime_fingerprint(profile, repository_root),
            "holdout_gate_status": profile.gate.status,
            "report_files": {
                "probabilistic_metrics": "probabilistic_metrics.csv",
                "coverage": "coverage.csv",
                "reliability": "reliability.json",
                "runtime": "runtime_metrics.csv",
            },
        }
    )
    if split == "holdout":
        summary["holdout_gate_evaluation"] = evaluate_ensemble_summary(
            summary,
            expected_issue_count=selected_split.issue_count,
            criteria=profile.gate.artifact["criteria"],
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
    parser = argparse.ArgumentParser(description="Run frozen RP-024 MRMS ensemble verification")
    parser.add_argument("command", choices=("conformance", "hindcast"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "holdout"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("runtime/reports/mrms"))
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
        profile = load_mrms_ensemble_profile(
            profile_path,
            repository_root=repository_root,
        )
        source = CachedMRMSFrameSource(
            MRMSArchiveFrameSource(
                args.root,
                cadence_minutes=profile.source_cadence_minutes,
                verify_hash=not args.skip_hash,
            )
        )
        selected = set(args.case) if args.case else None
        if args.command == "conformance":
            report = conform_ensemble_split(
                profile,
                split=args.split,
                frame_source=source,
                case_ids=selected,
                maximum_issues=args.max_issues,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["complete"] else 1
        run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = args.output_root / profile.profile_version / args.split / run_id
        summary = run_mrms_ensemble_hindcast(
            profile,
            split=args.split,
            repository_root=repository_root,
            frame_source=source,
            output_directory=output,
            case_ids=selected,
            maximum_issues=args.max_issues,
        )
        print(
            json.dumps(
                {**summary, "output_directory": str(output)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        if summary["failed_issue_count"] != 0:
            return 1
        if args.split == "holdout" and not summary["holdout_gate_evaluation"]["passed"]:
            return 1
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
