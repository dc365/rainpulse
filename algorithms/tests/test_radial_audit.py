from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rainpulse_algo.radar import radial_audit
from rainpulse_algo.radar.qc import apply_basic_qc as apply_basic_qc_impl
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_worker import (
    _validate_cross_radar_context_artifact,
    _validate_temporal_context_artifact,
)
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_radar_qc import FLAG_CONFIG, RP047_QC_CONFIG, normalized_fixture


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


def test_radial_audit_manifest_mode_uses_frozen_context_and_expected_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    temporal = dict(normalized)
    temporal_attrs = json.loads(temporal[".zattrs"])
    temporal_attrs["volume_end_time_utc"] = "2026-06-15T09:00:00+00:00"
    temporal_attrs["scan_id"] = "10000000-0000-4000-8000-000000000055"
    temporal[".zattrs"] = json.dumps(temporal_attrs).encode()
    client = FakeMinio()

    current_prefix = "radar/normalized/z9598/replay/current/volume.zarr"
    temporal_prefix = "radar/normalized/z9598/replay/temporal/volume.zarr"
    _publish_artifact(client, current_prefix, normalized)
    _publish_artifact(client, temporal_prefix, temporal)

    replay_manifest = tmp_path / "replay-manifest.json"
    replay_manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "mode": "replay_manifest",
                "radar_ids": ["z9598"],
                "start_time": "2026-08-24T02:00:00+00:00",
                "end_time": "2026-08-24T04:00:00+00:00",
                "expected_scan_ids": [
                    "10000000-0000-4000-8000-000000000004",
                    "10000000-0000-4000-8000-000000000099",
                ],
                "scans": [
                    {
                        "scan_id": "10000000-0000-4000-8000-000000000004",
                        "radar_id": "z9598",
                        "volume_start_time": "2026-08-24T02:54:10+00:00",
                        "volume_end_time": "2026-08-24T03:00:20+00:00",
                        "normalized_uri": f"s3://rainpulse/{current_prefix}",
                        "artifact_sha256": artifact_sha256(normalized),
                        "temporal_context": [
                            {
                                "radar_id": "z9598",
                                "input_uri": f"s3://rainpulse/{temporal_prefix}",
                                "artifact_sha256": artifact_sha256(temporal),
                            }
                        ],
                        "cross_radar_context": [],
                    },
                    {
                        "scan_id": "10000000-0000-4000-8000-000000000099",
                        "radar_id": "z9598",
                        "volume_start_time": "2026-08-24T03:04:10+00:00",
                        "volume_end_time": "2026-08-24T03:10:20+00:00",
                        "normalized_uri": "s3://rainpulse/radar/normalized/z9598/replay/missing/volume.zarr",
                        "artifact_sha256": "a" * 64,
                        "temporal_context": [],
                        "cross_radar_context": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "audit-report.json"

    def require_context(*args: Any, **kwargs: Any):
        assert kwargs.get("radial_context") is not None
        return apply_basic_qc_impl(*args, **kwargs)

    monkeypatch.setattr(radial_audit, "apply_basic_qc", require_context)
    monkeypatch.setattr(radial_audit, "minio_client_from_environment", lambda: client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "radial_audit.py",
            "--manifest",
            str(replay_manifest),
            "--output",
            str(output),
            "--audit-mode",
            "evidence",
            "--qc-config",
            str(RP047_QC_CONFIG),
            "--flag-definitions",
            str(FLAG_CONFIG),
        ],
    )

    exit_code = radial_audit.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    records = {item["scan_id"]: item for item in report["scans"]}
    assert exit_code == 4
    assert report["source_manifest"]["mode"] == "replay_manifest"
    assert report["summary"]["expected_scan_count"] == 2
    assert report["summary"]["completed_scan_count"] == 1
    assert report["summary"]["failed_scan_count"] == 1
    assert report["summary"]["processed_scan_count"] == 2
    assert report["summary"]["skipped_scan_count"] == 0
    assert records["10000000-0000-4000-8000-000000000004"]["status"] == "completed"
    assert records["10000000-0000-4000-8000-000000000004"]["context_fingerprint"]
    assert records["10000000-0000-4000-8000-000000000099"]["status"] == "failed"


def test_load_resumable_records_reuses_only_completed_and_checks_fingerprint(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "run_fingerprint": "abc123",
                "scans": [
                    {"scan_id": "scan-completed", "status": "completed", "affected": False},
                    {"scan_id": "scan-failed", "status": "failed", "error": "boom"},
                ],
            }
        ),
        encoding="utf-8",
    )

    completed, skipped = radial_audit._load_resumable_records(report_path, "abc123")

    assert skipped == 1
    assert set(completed) == {"scan-completed"}
    with pytest.raises(RuntimeError, match="fingerprint"):
        radial_audit._load_resumable_records(report_path, "def456")


def test_discover_catalog_manifest_uses_snapshot_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    end = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
    calls: list[tuple[str | None, str | None]] = []
    snapshot_time = "2026-08-28T03:05:00+00:00"
    pages = [
        {
            "items": [
                {
                    "scan_id": "scan-003",
                    "radar_id": "z9598",
                    "volume_start_time": "2026-08-28T02:24:00+00:00",
                    "volume_end_time": "2026-08-28T02:25:00+00:00",
                    "normalized_uri": "s3://rainpulse/z9598/scan-003",
                },
                {
                    "scan_id": "scan-002",
                    "radar_id": "z9598",
                    "volume_start_time": "2026-08-28T02:19:00+00:00",
                    "volume_end_time": "2026-08-28T02:20:00+00:00",
                    "normalized_uri": "s3://rainpulse/z9598/scan-002",
                },
            ],
            "next_cursor": "cursor-2",
            "snapshot_time": snapshot_time,
        },
        {
            "items": [
                {
                    "scan_id": "scan-001",
                    "radar_id": "z9598",
                    "volume_start_time": "2026-08-28T02:14:00+00:00",
                    "volume_end_time": "2026-08-28T02:15:00+00:00",
                    "normalized_uri": "s3://rainpulse/z9598/scan-001",
                }
            ],
            "next_cursor": None,
            "snapshot_time": snapshot_time,
        },
    ]

    def fake_fetch_page(
        catalog_url: str,
        *,
        radar_id: str,
        start_time: datetime,
        end_time: datetime,
        limit: int,
        cursor: str | None,
        snapshot_time: str | None,
    ) -> dict[str, Any]:
        assert catalog_url == "http://api:8080/api/v1/radar-scans"
        assert radar_id == "z9598"
        assert start_time == start
        assert end_time == end
        assert limit == 2
        calls.append((cursor, snapshot_time))
        return pages[len(calls) - 1]

    monkeypatch.setattr(radial_audit, "_fetch_scan_page", fake_fetch_page)

    manifest = radial_audit._discover_catalog_manifest(
        "http://api:8080/api/v1/radar-scans",
        "z9598",
        start,
        end,
        2,
    )

    assert calls == [(None, None), ("cursor-2", snapshot_time)]
    assert manifest["mode"] == "catalog"
    assert manifest["snapshot_time"] == snapshot_time
    assert manifest["expected_scan_ids"] == ["scan-001", "scan-002", "scan-003"]
    assert [item["scan_id"] for item in manifest["scans"]] == [
        "scan-001",
        "scan-002",
        "scan-003",
    ]


def test_audit_fingerprint_includes_mode_and_all_radar_code(monkeypatch):
    profile = radial_audit.load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    kwargs = dict(
        source_manifest={"scans": []},
        profile=profile,
        qc_config_path=RP047_QC_CONFIG,
        flag_definitions_path=FLAG_CONFIG,
    )
    saturation = radial_audit._build_run_metadata(**kwargs, audit_mode="saturation")
    evidence = radial_audit._build_run_metadata(**kwargs, audit_mode="evidence")
    assert saturation["run_fingerprint"] != evidence["run_fingerprint"]
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: original(path) + (b"#changed" if path.name == "qc_decision.py" else b""),
    )
    assert (
        radial_audit._build_run_metadata(**kwargs, audit_mode="evidence")["run_fingerprint"]
        != evidence["run_fingerprint"]
    )


def test_frozen_manifest_rejects_changed_input_even_before_resume():
    class Reader:
        def load(self, uri):
            return {".zattrs": b"changed"}

    manifest = {
        "scans": [
            {
                "normalized_uri": "s3://bucket/input",
                "artifact_sha256": "a" * 64,
                "temporal_context": [],
                "cross_radar_context": [],
            }
        ]
    }
    with pytest.raises(radial_audit.AuditInputError, match="checksum"):
        radial_audit._freeze_input_manifest(manifest, Reader(), require_hashes=True)


def test_future_cross_and_equal_temporal_rejected():
    p = load_qc_profile(
        Path("configs/qc/fujian-qc-evidence-v2.yaml"), Path("configs/qc/flag-definitions.yaml")
    )
    req = SimpleNamespace(payload=SimpleNamespace(radar_id="z9598"))
    root = SimpleNamespace(
        attrs={"radar_id": "z9599", "volume_end_time_utc": "2026-09-08T00:05:00Z"}
    )
    now = datetime(2026, 9, 8, tzinfo=UTC)
    assert (
        _validate_cross_radar_context_artifact(
            root, request=req, requested_radar_id="z9599", current_end_time=now, profile=p
        )
        == "future_time_disallowed"
    )
    root.attrs = {"radar_id": "z9598", "volume_end_time_utc": "2026-09-08T00:00:00Z"}
    assert (
        _validate_temporal_context_artifact(root, request=req, current_end_time=now, profile=p)
        == "non_past_temporal_context"
    )


def test_resume_rejects_changed_mode_and_input_bytes(tmp_path, monkeypatch):
    normalized = normalized_fixture(tmp_path)
    client = FakeMinio()
    prefix = "radar/resume/current"
    _publish_artifact(client, prefix, normalized)
    manifest_path = tmp_path / "frozen.json"
    manifest_path.write_text(
        json.dumps(
            {
                "scans": [
                    {
                        "scan_id": "10000000-0000-4000-8000-000000000004",
                        "radar_id": "z9598",
                        "volume_start_time": "2026-06-15T09:00:00Z",
                        "volume_end_time": "2026-06-15T09:04:29.004Z",
                        "normalized_uri": f"s3://rainpulse/{prefix}",
                        "artifact_sha256": artifact_sha256(normalized),
                        "temporal_context": [],
                        "cross_radar_context": [],
                    }
                ]
            }
        )
    )
    output = tmp_path / "report.json"
    base = [
        "audit",
        "--manifest",
        str(manifest_path),
        "--output",
        str(output),
        "--qc-config",
        str(RP047_QC_CONFIG),
        "--flag-definitions",
        str(FLAG_CONFIG),
    ]
    monkeypatch.setattr(radial_audit, "minio_client_from_environment", lambda: client)
    monkeypatch.setattr(sys, "argv", base + ["--audit-mode", "saturation"])
    assert radial_audit.main() == 0
    previous = output.read_bytes()
    monkeypatch.setattr(sys, "argv", base + ["--audit-mode", "evidence", "--resume"])
    assert radial_audit.main() == 2
    assert output.read_bytes() == previous
    changed = dict(normalized)
    attrs = json.loads(changed[".zattrs"])
    attrs["radar_health"] = "UNAVAILABLE"
    changed[".zattrs"] = json.dumps(attrs).encode()
    _publish_artifact(client, prefix, changed)
    monkeypatch.setattr(sys, "argv", base + ["--audit-mode", "saturation", "--resume"])
    assert radial_audit.main() == 2
    assert output.read_bytes() == previous


def test_manifest_requires_input_hash_and_catalog_freezes_it():
    objects = {".zattrs": b"{}"}
    reader = SimpleNamespace(load=lambda uri: objects)
    manifest = {
        "scans": [
            {
                "normalized_uri": "s3://bucket/input",
                "temporal_context": [],
                "cross_radar_context": [],
            }
        ]
    }
    with pytest.raises(radial_audit.AuditInputError, match="artifact_sha256"):
        radial_audit._freeze_input_manifest(manifest, reader, require_hashes=True)
    frozen = radial_audit._freeze_input_manifest(manifest, reader, require_hashes=False)
    assert frozen["scans"][0]["artifact_sha256"] == artifact_sha256(objects)


def test_context_checksum_and_geometry_asset_changes_invalidate_replay(tmp_path, monkeypatch):
    objects = {"current": {".zattrs": b"current"}, "context": {".zattrs": b"context"}}
    manifest = {
        "scans": [
            {
                "normalized_uri": "current",
                "temporal_context": [{"radar_id": "z9598", "input_uri": "context"}],
                "cross_radar_context": [],
            }
        ]
    }
    reader = SimpleNamespace(load=lambda uri: objects[uri])
    frozen = radial_audit._freeze_input_manifest(manifest, reader, require_hashes=False)
    objects["context"] = {".zattrs": b"changed context"}
    with pytest.raises(radial_audit.AuditInputError, match="checksum"):
        radial_audit._freeze_input_manifest(frozen, reader, require_hashes=True)
    config = tmp_path / "z9598.yaml"
    config.write_text("beam_width: 1.0")
    monkeypatch.setenv("RAINPULSE_RADAR_CONFIG_DIR", str(tmp_path))
    kwargs = dict(
        source_manifest=frozen,
        profile=radial_audit.load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG),
        qc_config_path=RP047_QC_CONFIG,
        flag_definitions_path=FLAG_CONFIG,
    )
    before = radial_audit._build_run_metadata(**kwargs)["run_fingerprint"]
    config.write_text("beam_width: 2.0")
    assert radial_audit._build_run_metadata(**kwargs)["run_fingerprint"] != before


def test_duplicate_temporal_uri_cannot_supply_two_context_observations(tmp_path):
    from rainpulse_algo.radar.qc_worker import _load_radial_context

    normalized = normalized_fixture(tmp_path)
    temporal = dict(normalized)
    attrs = json.loads(temporal[".zattrs"])
    attrs["scan_id"] = "10000000-0000-4000-8000-000000000005"
    attrs["volume_end_time_utc"] = "2026-06-15T09:00:00Z"
    temporal[".zattrs"] = json.dumps(attrs).encode()
    client = FakeMinio()
    _publish_artifact(client, "context", temporal)
    profile = radial_audit.load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    request = radial_audit._build_audit_request(
        {
            "scan_id": "10000000-0000-4000-8000-000000000004",
            "radar_id": "z9598",
            "volume_end_time": "2026-06-15T09:04:29.004Z",
            "normalized_uri": "s3://rainpulse/current",
            "temporal_context": [{"radar_id": "z9598", "input_uri": "s3://rainpulse/context"}] * 2,
        },
        profile,
    )
    _, provenance = _load_radial_context(request, normalized, profile, client)
    assert provenance["temporal_available_count"] == 1
    assert provenance["artifacts"][-1]["skip_reason"] == "duplicate_temporal_input"
