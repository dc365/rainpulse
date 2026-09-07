from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rainpulse_algo.radar.calibration import (
    CalibrationInputError,
    load_calibration_profile,
    load_calibration_reference_manifest,
    load_coefficient_table,
    resolve_attenuation_coefficients,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "fujian-radar-calibration-shadow-v1.yaml"
)


def _reference_manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_version": "radar-calibration-reference-manifest-v1",
        "source_calibration_profile_version": "fujian-radar-calibration-shadow-v1",
        "generated_at": "2026-09-07T08:00:00Z",
        "radar_band": "S",
        "references": [
            {
                "process_id": "storm-fit-001",
                "case_id": "case-fit-001",
                "role": "coefficient_fitting",
                "truth_kind": "gauge_accumulation_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": "2026-08-28T02:00:00Z",
                "end_time_utc": "2026-08-28T03:00:00Z",
                "reference_uri": "s3://rainpulse/calibration/fit-001.parquet",
                "source_sha256": "a" * 64,
            },
            {
                "process_id": "storm-fit-002",
                "case_id": "case-fit-002",
                "role": "coefficient_fitting",
                "truth_kind": "gauge_accumulation_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": "2026-08-28T03:00:00Z",
                "end_time_utc": "2026-08-28T04:00:00Z",
                "reference_uri": "s3://rainpulse/calibration/fit-002.parquet",
                "source_sha256": "b" * 64,
            },
            {
                "process_id": "storm-fit-003",
                "case_id": "case-fit-003",
                "role": "coefficient_fitting",
                "truth_kind": "reference_qpe_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": "2026-08-28T04:00:00Z",
                "end_time_utc": "2026-08-28T05:00:00Z",
                "reference_uri": "s3://rainpulse/calibration/fit-003.parquet",
                "source_sha256": "c" * 64,
            },
            {
                "process_id": "storm-val-101",
                "case_id": "case-val-101",
                "role": "independent_validation",
                "truth_kind": "gauge_accumulation_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": "2026-08-29T02:00:00Z",
                "end_time_utc": "2026-08-29T03:00:00Z",
                "reference_uri": "s3://rainpulse/calibration/val-101.parquet",
                "source_sha256": "d" * 64,
            },
            {
                "process_id": "storm-val-102",
                "case_id": "case-val-102",
                "role": "independent_validation",
                "truth_kind": "gauge_accumulation_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": "2026-08-29T03:00:00Z",
                "end_time_utc": "2026-08-29T04:00:00Z",
                "reference_uri": "s3://rainpulse/calibration/val-102.parquet",
                "source_sha256": "e" * 64,
            },
            {
                "process_id": "storm-val-103",
                "case_id": "case-val-103",
                "role": "independent_validation",
                "truth_kind": "reference_qpe_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": "2026-08-29T04:00:00Z",
                "end_time_utc": "2026-08-29T05:00:00Z",
                "reference_uri": "s3://rainpulse/calibration/val-103.parquet",
                "source_sha256": "f" * 64,
            },
        ],
    }


def _coefficient_table() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "table_version": "fujian-s-band-attenuation-coefficients-v1",
        "artifact_contract_version": "1.0",
        "source_calibration_profile_version": "fujian-radar-calibration-shadow-v1",
        "reference_manifest_version": "fujian-radar-calibration-reference-20260907-v1",
        "radar_band": "S",
        "entries": [
            {
                "radar_id": "z9598",
                "coefficient_state": "verified_shadow_use",
                "coefficient_a": 0.04,
                "exponent_b": 1.0,
                "applicable_temperature_range_c": [5.0, 35.0],
                "fitted_process_ids": ["storm-fit-001", "storm-fit-002", "storm-fit-003"],
                "validation_process_ids": [
                    "storm-val-101",
                    "storm-val-102",
                    "storm-val-103",
                ],
            }
        ],
    }


def test_calibration_reference_manifest_rejects_split_leakage(
    tmp_path: Path,
) -> None:
    profile = load_calibration_profile(PROFILE_PATH)
    manifest = _reference_manifest()
    manifest["references"].append(
        {
            "process_id": "storm-fit-001",
            "case_id": "case-fit-001",
            "role": "independent_validation",
            "truth_kind": "gauge_accumulation_1h",
            "radar_ids": ["z9598", "z9593"],
            "start_time_utc": "2026-08-30T02:00:00Z",
            "end_time_utc": "2026-08-30T03:00:00Z",
            "reference_uri": "s3://rainpulse/calibration/leak.parquet",
            "source_sha256": "9" * 64,
        }
    )
    manifest_path = tmp_path / "reference-manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    with pytest.raises(CalibrationInputError, match="disjoint"):
        load_calibration_reference_manifest(manifest_path, profile=profile)


def test_resolve_verified_shadow_coefficients_from_table(
    tmp_path: Path,
) -> None:
    profile = load_calibration_profile(PROFILE_PATH)
    manifest_path = tmp_path / "reference-manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(_reference_manifest(), sort_keys=False),
        encoding="utf-8",
    )
    table_path = tmp_path / "coefficient-table.yaml"
    table_path.write_text(
        yaml.safe_dump(_coefficient_table(), sort_keys=False),
        encoding="utf-8",
    )

    manifest = load_calibration_reference_manifest(manifest_path, profile=profile)
    table = load_coefficient_table(table_path, profile=profile, reference_manifest=manifest)
    resolved = resolve_attenuation_coefficients(
        table,
        radar_id="z9598",
        radar_band="S",
        temperature_c=20.0,
    )

    assert resolved.source == "frozen_shadow_coefficient_table"
    assert resolved.coefficient_a == pytest.approx(0.04)
    assert resolved.exponent_b == pytest.approx(1.0)


def test_resolve_coefficients_rejects_out_of_temperature_range(
    tmp_path: Path,
) -> None:
    profile = load_calibration_profile(PROFILE_PATH)
    manifest_path = tmp_path / "reference-manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(_reference_manifest(), sort_keys=False),
        encoding="utf-8",
    )
    table_path = tmp_path / "coefficient-table.yaml"
    table_path.write_text(
        yaml.safe_dump(_coefficient_table(), sort_keys=False),
        encoding="utf-8",
    )

    manifest = load_calibration_reference_manifest(manifest_path, profile=profile)
    table = load_coefficient_table(table_path, profile=profile, reference_manifest=manifest)

    with pytest.raises(CalibrationInputError, match="temperature"):
        resolve_attenuation_coefficients(
            table,
            radar_id="z9598",
            radar_band="S",
            temperature_c=0.0,
        )