from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from rainpulse_algo.radar import attenuation_audit
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_radar_qc import (
    ATTENUATION_PROFILE,
    PHASE_PROCESSING_PROFILE,
    _linear_phidp_deg,
    synthetic_normalized_fixture,
)


def _publish_artifact(client: FakeMinio, prefix: str, objects: dict[str, bytes]) -> None:
    manifest = []
    for key, value in objects.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(objects),
            "size_bytes": sum(map(len, objects.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()


@pytest.mark.parametrize(
    "environment_case",
    [
        "ready",
        "missing_temperature",
        "missing_blockage",
        "wrong_hash",
        "wrong_time",
        "out_of_range",
        "changed_source",
    ],
)
def test_attenuation_audit_manifest_emits_shadow_artifact_with_configured_coefficients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_case: str,
) -> None:
    range_m = (1.0 + np.arange(21, dtype="float32")) * 1000.0
    phidp = _linear_phidp_deg(range_m, phi0_deg=30.0, kdp_deg_per_km=1.2)[None, :]
    dbzh = np.full((1, 21), 35.0, dtype="float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={"PHIDP": phidp.astype("float32")},
    )
    attrs = json.loads(normalized[".zattrs"])
    attrs["scan_id"] = "10000000-0000-4000-8000-000000000301"
    attrs["radar_id"] = "z9598"
    attrs["volume_start_time_utc"] = "2026-08-28T02:54:00Z"
    attrs["volume_end_time_utc"] = "2026-08-28T03:00:00Z"
    normalized[".zattrs"] = json.dumps(attrs).encode()

    client = FakeMinio()
    prefix = "radar/normalized/z9598/attenuation-audit/current/volume.zarr"
    _publish_artifact(client, prefix, normalized)

    coefficient_table_path = tmp_path / "attenuation-coefficients.yaml"
    coefficient_table_path.write_text(
        yaml.safe_dump(
            {
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
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    manifest_path = tmp_path / "attenuation-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "mode": "replay_manifest",
                "radar_ids": ["z9598"],
                "start_time": "2026-08-28T02:54:00Z",
                "end_time": "2026-08-28T03:00:00Z",
                "expected_scan_ids": ["10000000-0000-4000-8000-000000000301"],
                "scans": [
                    {
                        "scan_id": "10000000-0000-4000-8000-000000000301",
                        "radar_id": "z9598",
                        "volume_start_time": "2026-08-28T02:54:00Z",
                        "volume_end_time": "2026-08-28T03:00:00Z",
                        "normalized_uri": f"s3://rainpulse/{prefix}",
                        "artifact_sha256": artifact_sha256(normalized),
                        "temporal_context": [],
                        "cross_radar_context": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    environment_npz = tmp_path / "environment.npz"
    fields = {}
    if environment_case != "missing_temperature":
        fields["temperature_c"] = np.asarray(-20.0 if environment_case == "out_of_range" else 20.0)
    if environment_case != "missing_blockage":
        fields["sweep_000__blockage_fraction"] = np.zeros(dbzh.shape, dtype="float32")
    np.savez(environment_npz, **fields)
    digest = hashlib.sha256(environment_npz.read_bytes()).hexdigest()
    environment_manifest = tmp_path / "environment.json"
    environment_manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "scans": [
                    {
                        "scan_id": attrs["scan_id"],
                        "radar_id": "z9598",
                        "volume_end_time_utc": "2026-08-28T04:00:00Z"
                        if environment_case == "wrong_time"
                        else attrs["volume_end_time_utc"],
                        "source_uri": "fixture://verified-temperature-and-dem",
                        "npz_path": environment_npz.name,
                        "sha256": "0" * 64 if environment_case == "wrong_hash" else digest,
                    }
                ],
            }
        )
    )
    output = tmp_path / "attenuation-audit-report.json"

    monkeypatch.setattr(attenuation_audit, "minio_client_from_environment", lambda: client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "attenuation_audit.py",
            "--manifest",
            str(manifest_path),
            "--phase-processing-profile",
            str(PHASE_PROCESSING_PROFILE),
            "--attenuation-profile",
            str(ATTENUATION_PROFILE),
            "--coefficient-table",
            str(coefficient_table_path),
            "--environment-manifest",
            str(environment_manifest),
            "--output",
            str(output),
        ],
    )

    if environment_case == "changed_source":
        normalized[".zattrs"] = json.dumps(
            {**attrs, "unexpected_source_revision": "changed"}
        ).encode()
        _publish_artifact(client, prefix, normalized)
    exit_code = attenuation_audit.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    scan = report["scans"][0]
    if environment_case != "ready":
        assert exit_code != 0 or scan.get("attenuation_available_gate_count", 0) == 0
        return
    assert scan["artifact"]["source_input"]["environment"]["sha256"] == digest
    assert exit_code == 0
    assert report["summary"]["completed_scan_count"] == 1
    assert scan["status"] == "completed"
    assert scan["phase_processing_profile_version"] == "fujian-phidp-kdp-shadow-v1"
    assert scan["attenuation_profile_version"] == "fujian-kdp-attenuation-shadow-v1"
    assert scan["attenuation_available_gate_count"] > 0
    assert (
        scan["artifact"]["source_input"]["coefficient_source"] == "frozen_shadow_coefficient_table"
    )
    assert (
        scan["artifact"]["source_input"]["coefficient_table_version"]
        == "fujian-s-band-attenuation-coefficients-v1"
    )
    assert scan["artifact"]["sweeps"]["sweep_000"]["available_gate_count"] > 0


def test_attenuation_audit_keeps_fail_closed_shadow_report_with_unconfigured_coefficients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    range_m = (1.0 + np.arange(21, dtype="float32")) * 1000.0
    phidp = _linear_phidp_deg(range_m, phi0_deg=25.0, kdp_deg_per_km=1.0)[None, :]
    dbzh = np.full((1, 21), 32.0, dtype="float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={"PHIDP": phidp.astype("float32")},
    )
    attrs = json.loads(normalized[".zattrs"])
    attrs["scan_id"] = "10000000-0000-4000-8000-000000000302"
    attrs["radar_id"] = "z9598"
    attrs["volume_start_time_utc"] = "2026-08-28T03:00:00Z"
    attrs["volume_end_time_utc"] = "2026-08-28T03:06:00Z"
    normalized[".zattrs"] = json.dumps(attrs).encode()

    client = FakeMinio()
    prefix = "radar/normalized/z9598/attenuation-audit/fail-closed/volume.zarr"
    _publish_artifact(client, prefix, normalized)

    manifest_path = tmp_path / "attenuation-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "mode": "replay_manifest",
                "radar_ids": ["z9598"],
                "start_time": "2026-08-28T03:00:00Z",
                "end_time": "2026-08-28T03:06:00Z",
                "expected_scan_ids": ["10000000-0000-4000-8000-000000000302"],
                "scans": [
                    {
                        "scan_id": "10000000-0000-4000-8000-000000000302",
                        "radar_id": "z9598",
                        "volume_start_time": "2026-08-28T03:00:00Z",
                        "volume_end_time": "2026-08-28T03:06:00Z",
                        "normalized_uri": f"s3://rainpulse/{prefix}",
                        "artifact_sha256": artifact_sha256(normalized),
                        "temporal_context": [],
                        "cross_radar_context": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "attenuation-audit-report.json"

    monkeypatch.setattr(attenuation_audit, "minio_client_from_environment", lambda: client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "attenuation_audit.py",
            "--manifest",
            str(manifest_path),
            "--phase-processing-profile",
            str(PHASE_PROCESSING_PROFILE),
            "--attenuation-profile",
            str(ATTENUATION_PROFILE),
            "--output",
            str(output),
        ],
    )

    exit_code = attenuation_audit.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    scan = report["scans"][0]
    assert exit_code == 0
    assert scan["status"] == "completed"
    assert scan["attenuation_available_gate_count"] == 0
    assert scan["skip_reason_counts"]["attenuation_coefficients_unconfigured"] > 0
    assert scan["artifact"]["sweeps"]["sweep_000"]["available_gate_count"] == 0
