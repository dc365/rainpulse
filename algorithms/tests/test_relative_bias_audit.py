from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar import relative_bias_audit
from rainpulse_algo.radar.qc_geometry import RadarBeamContext
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_radar_qc import EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG, synthetic_normalized_fixture


class FlatTerrain:
    def sample(self, longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
        return np.zeros(np.broadcast(longitude, latitude).shape, dtype="float32")


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


def _beam_context(radar_id: str, *, longitude_deg: float) -> RadarBeamContext:
    return RadarBeamContext(
        radar_id=radar_id,
        longitude_deg=longitude_deg,
        latitude_deg=26.08,
        antenna_altitude_m=100.0,
        beam_width_vertical_deg=1.0,
        altitude_datum_status="verified_egm2008",
        radar_config_version=f"{radar_id}-test-v1",
    )


@pytest.mark.parametrize("changed_reference", [False, True])
def test_relative_bias_audit_reports_signed_primary_minus_reference_bias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_reference: bool,
) -> None:
    current = synthetic_normalized_fixture(np.full((10, 12), 20.0, dtype="float32"))
    reference = synthetic_normalized_fixture(np.full((10, 12), 18.0, dtype="float32"))
    current_attrs = json.loads(current[".zattrs"])
    current_attrs["scan_id"] = "10000000-0000-4000-8000-000000000401"
    current_attrs["radar_id"] = "z9598"
    current_attrs["volume_start_time_utc"] = "2026-08-28T02:54:00Z"
    current_attrs["volume_end_time_utc"] = "2026-08-28T03:00:00Z"
    current_attrs["radar_health"] = "HEALTHY"
    current[".zattrs"] = json.dumps(current_attrs).encode()
    reference_attrs = json.loads(reference[".zattrs"])
    reference_attrs["scan_id"] = "10000000-0000-4000-8000-000000000402"
    reference_attrs["radar_id"] = "z9593"
    reference_attrs["volume_start_time_utc"] = "2026-08-28T02:54:30Z"
    reference_attrs["volume_end_time_utc"] = "2026-08-28T03:00:20Z"
    reference_attrs["radar_health"] = "HEALTHY"
    reference[".zattrs"] = json.dumps(reference_attrs).encode()

    client = FakeMinio()
    current_prefix = "radar/normalized/z9598/relative-bias/current/volume.zarr"
    reference_prefix = "radar/normalized/z9593/relative-bias/reference/volume.zarr"
    _publish_artifact(client, current_prefix, current)
    _publish_artifact(client, reference_prefix, reference)

    manifest_path = tmp_path / "relative-bias-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "mode": "replay_manifest",
                "radar_ids": ["z9598", "z9593"],
                "start_time": "2026-08-28T02:54:00Z",
                "end_time": "2026-08-28T03:00:20Z",
                "expected_scan_ids": ["10000000-0000-4000-8000-000000000401"],
                "scans": [
                    {
                        "scan_id": "10000000-0000-4000-8000-000000000401",
                        "radar_id": "z9598",
                        "volume_start_time": "2026-08-28T02:54:00Z",
                        "volume_end_time": "2026-08-28T03:00:00Z",
                        "normalized_uri": f"s3://rainpulse/{current_prefix}",
                        "artifact_sha256": artifact_sha256(current),
                        "temporal_context": [],
                        "cross_radar_context": [
                            {
                                "radar_id": "z9593",
                                "input_uri": f"s3://rainpulse/{reference_prefix}",
                                "artifact_sha256": artifact_sha256(reference),
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "relative-bias-report.json"

    monkeypatch.setattr(relative_bias_audit, "minio_client_from_environment", lambda: client)
    monkeypatch.setattr(
        relative_bias_audit,
        "_load_geometry_resources",
        lambda args: relative_bias_audit.RelativeBiasResources(
            terrain=FlatTerrain(),
            beam_contexts_by_radar={
                "z9598": _beam_context("z9598", longitude_deg=119.30),
                "z9593": _beam_context("z9593", longitude_deg=119.30),
            },
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "relative_bias_audit.py",
            "--manifest",
            str(manifest_path),
            "--profile",
            str(
                Path(__file__).resolve().parents[2]
                / "configs"
                / "verification"
                / "fujian-radar-relative-bias-shadow-v1.yaml"
            ),
            "--qc-config",
            str(EVIDENCE_V2_QC_CONFIG),
            "--flag-definitions",
            str(FLAG_CONFIG),
            "--output",
            str(output),
        ],
    )

    if changed_reference:
        reference[".zattrs"] = json.dumps(
            {**json.loads(reference[".zattrs"]), "new_revision": "changed"}
        ).encode()
        _publish_artifact(client, reference_prefix, reference)
    exit_code = relative_bias_audit.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    scan = report["scans"][0]
    if changed_reference:
        assert scan["status"] == "failed"
        assert "SHA-256" in scan["error"]
        return
    comparison = scan["comparisons"][0]
    aggregate = report["aggregates"][0]
    assert exit_code == 0
    assert scan["status"] == "completed"
    assert comparison["status"] == "computed"
    assert comparison["reference_radar_id"] == "z9593"
    assert comparison["comparable_gate_count"] > 0
    assert comparison["median_relative_bias_dbz"] == pytest.approx(2.0)
    assert aggregate["grouping_mode"] == "scan_id_fallback"
    assert aggregate["status"] == "engineering_only"
    assert aggregate["truth_designation_enabled"] is False


def test_relative_bias_audit_reports_skip_reason_without_geometry_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = synthetic_normalized_fixture(np.full((10, 12), 20.0, dtype="float32"))
    reference = synthetic_normalized_fixture(np.full((10, 12), 18.0, dtype="float32"))
    current_attrs = json.loads(current[".zattrs"])
    current_attrs["scan_id"] = "10000000-0000-4000-8000-000000000411"
    current_attrs["radar_id"] = "z9598"
    current_attrs["volume_start_time_utc"] = "2026-08-28T03:00:00Z"
    current_attrs["volume_end_time_utc"] = "2026-08-28T03:06:00Z"
    current[".zattrs"] = json.dumps(current_attrs).encode()
    reference_attrs = json.loads(reference[".zattrs"])
    reference_attrs["scan_id"] = "10000000-0000-4000-8000-000000000412"
    reference_attrs["radar_id"] = "z9593"
    reference_attrs["volume_start_time_utc"] = "2026-08-28T03:00:00Z"
    reference_attrs["volume_end_time_utc"] = "2026-08-28T03:06:00Z"
    reference[".zattrs"] = json.dumps(reference_attrs).encode()

    client = FakeMinio()
    current_prefix = "radar/normalized/z9598/relative-bias/no-geometry/current/volume.zarr"
    reference_prefix = "radar/normalized/z9593/relative-bias/no-geometry/reference/volume.zarr"
    _publish_artifact(client, current_prefix, current)
    _publish_artifact(client, reference_prefix, reference)

    manifest_path = tmp_path / "relative-bias-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "mode": "replay_manifest",
                "radar_ids": ["z9598", "z9593"],
                "start_time": "2026-08-28T03:00:00Z",
                "end_time": "2026-08-28T03:06:00Z",
                "expected_scan_ids": ["10000000-0000-4000-8000-000000000411"],
                "scans": [
                    {
                        "scan_id": "10000000-0000-4000-8000-000000000411",
                        "radar_id": "z9598",
                        "volume_start_time": "2026-08-28T03:00:00Z",
                        "volume_end_time": "2026-08-28T03:06:00Z",
                        "normalized_uri": f"s3://rainpulse/{current_prefix}",
                        "artifact_sha256": artifact_sha256(current),
                        "temporal_context": [],
                        "cross_radar_context": [
                            {
                                "radar_id": "z9593",
                                "input_uri": f"s3://rainpulse/{reference_prefix}",
                                "artifact_sha256": artifact_sha256(reference),
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "relative-bias-report.json"

    monkeypatch.setattr(relative_bias_audit, "minio_client_from_environment", lambda: client)
    monkeypatch.setattr(
        relative_bias_audit,
        "_load_geometry_resources",
        lambda args: relative_bias_audit.RelativeBiasResources(
            terrain=None,
            beam_contexts_by_radar={},
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "relative_bias_audit.py",
            "--manifest",
            str(manifest_path),
            "--profile",
            str(
                Path(__file__).resolve().parents[2]
                / "configs"
                / "verification"
                / "fujian-radar-relative-bias-shadow-v1.yaml"
            ),
            "--qc-config",
            str(EVIDENCE_V2_QC_CONFIG),
            "--flag-definitions",
            str(FLAG_CONFIG),
            "--output",
            str(output),
        ],
    )

    exit_code = relative_bias_audit.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    scan = report["scans"][0]
    comparison = scan["comparisons"][0]
    assert exit_code == 0
    assert scan["status"] == "completed"
    assert comparison["status"] == "skipped"
    assert comparison["skip_reason"] == "geometry_resources_unavailable"
    assert report["aggregates"] == []
