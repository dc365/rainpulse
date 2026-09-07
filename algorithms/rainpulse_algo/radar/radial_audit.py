from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import NAMESPACE_URL, UUID, uuid5

from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    artifact_sha256,
    minio_client_from_environment,
)

from .qc import QCConfigError, apply_basic_qc, audit_long_range_saturated_radials, load_qc_profile
from .qc_worker import _load_ancillary_maps, prepare_qc_inputs


class AuditInputError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit normalized radar volumes for versioned radial QC evidence."
    )
    parser.add_argument("--catalog-url", default="http://api:8080/api/v1/radar-scans")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--radar-id")
    parser.add_argument("--start-time", help="inclusive ISO-8601 UTC time")
    parser.add_argument("--end-time", help="exclusive ISO-8601 UTC time")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--qc-config",
        type=Path,
        default=_environment_path("RAINPULSE_RADAR_QC_CONFIG"),
    )
    parser.add_argument(
        "--flag-definitions",
        type=Path,
        default=_environment_path("RAINPULSE_QC_FLAG_DEFINITIONS"),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--audit-mode",
        choices=("saturation", "evidence"),
        default="saturation",
        help="retain RP-040 saturation-only output or evaluate all configured evidence",
    )
    args = parser.parse_args()

    try:
        if args.limit <= 0 or args.limit > 200:
            raise AuditInputError("--limit must be between 1 and 200")
        profile = load_qc_profile(args.qc_config, args.flag_definitions)
        source_manifest = _load_source_manifest(args)
        client = minio_client_from_environment()
        raw_reader = ArtifactObjectReader(client)
        source_manifest = _freeze_input_manifest(
            source_manifest,
            raw_reader,
            require_hashes=args.manifest is not None,
        )
        reader = FrozenArtifactReader(raw_reader, source_manifest)
        ancillary = _load_ancillary_maps(profile, client)
        run_metadata = _build_run_metadata(
            source_manifest=source_manifest,
            profile=profile,
            qc_config_path=args.qc_config,
            flag_definitions_path=args.flag_definitions,
            audit_mode=args.audit_mode,
            ancillary_maps=ancillary,
        )
        records, skipped = (
            _load_resumable_records(args.output, run_metadata["run_fingerprint"])
            if args.resume
            else ({}, 0)
        )
        # Successful records are reusable only after their input bytes have been reverified.
        for scan in source_manifest["scans"]:
            if scan["scan_id"] in records:
                for entry, uri_key in _scan_inputs(scan):
                    try:
                        reader.load(entry[uri_key])
                    except Exception as error:
                        raise AuditInputError("resume input cannot be verified") from error
        expected_scan_ids = [str(scan_id) for scan_id in source_manifest["expected_scan_ids"]]
        processed = 0
        for index, scan in enumerate(source_manifest["scans"], start=1):
            scan_id = str(scan["scan_id"])
            if scan_id in records:
                continue
            processed += 1
            record = _audit_scan(
                scan,
                profile=profile,
                audit_mode=args.audit_mode,
                reader=reader,
                client=client,
                ancillary=ancillary,
            )
            records[scan_id] = record
            report = _build_report(
                source_manifest=source_manifest,
                run_metadata=run_metadata,
                records=records,
                expected_scan_ids=expected_scan_ids,
                processed_count=processed,
                skipped_count=skipped,
            )
            _write_manifest(args.output, report)
            print(
                json.dumps(
                    {
                        "completed": report["summary"]["completed_scan_count"],
                        "failed": report["summary"]["failed_scan_count"],
                        "expected": report["summary"]["expected_scan_count"],
                        "scan_id": scan_id,
                        "status": record["status"],
                        "affected": record["affected"],
                        "saturated_ray_count": record.get("saturated_ray_count"),
                        "evidence_ray_count": record.get("evidence_ray_count"),
                        "error": record.get("error"),
                        "catalog_index": index,
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )

        report = _build_report(
            source_manifest=source_manifest,
            run_metadata=run_metadata,
            records=records,
            expected_scan_ids=expected_scan_ids,
            processed_count=processed,
            skipped_count=skipped,
        )
        _write_manifest(args.output, report)
        return _report_exit_code(report)
    except (AuditInputError, QCConfigError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _load_source_manifest(args: argparse.Namespace) -> dict[str, Any]:
    if args.manifest is not None:
        return _load_replay_manifest(args.manifest)
    if not args.radar_id or not args.start_time or not args.end_time:
        raise AuditInputError("catalog mode requires --radar-id, --start-time and --end-time")
    start = _parse_time(args.start_time)
    end = _parse_time(args.end_time)
    if end <= start:
        raise AuditInputError("--end-time must be later than --start-time")
    return _discover_catalog_manifest(
        args.catalog_url,
        args.radar_id,
        start,
        end,
        args.limit,
    )


def _discover_catalog_manifest(
    catalog_url: str,
    radar_id: str,
    start_time: datetime,
    end_time: datetime,
    limit: int,
) -> dict[str, Any]:
    scans: list[dict[str, Any]] = []
    cursor: str | None = None
    snapshot_time: str | None = None
    while True:
        page = _fetch_scan_page(
            catalog_url,
            radar_id=radar_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            cursor=cursor,
            snapshot_time=snapshot_time,
        )
        page_snapshot = page.get("snapshot_time")
        if not isinstance(page_snapshot, str) or not page_snapshot:
            raise AuditInputError("radar scan page has no snapshot_time")
        if snapshot_time is None:
            snapshot_time = page_snapshot
        elif page_snapshot != snapshot_time:
            raise AuditInputError("radar scan page snapshot_time changed during pagination")
        items = page.get("items")
        if not isinstance(items, list):
            raise AuditInputError("radar scan page has no items array")
        for item in items:
            scans.append(_normalize_source_scan(item))
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, str) or not next_cursor:
            raise AuditInputError("radar scan page next_cursor is invalid")
        cursor = next_cursor
    scans.sort(key=lambda item: (str(item["volume_end_time"]), str(item["scan_id"])))
    expected_scan_ids = [str(scan["scan_id"]) for scan in scans]
    return {
        "schema_version": "1.0",
        "mode": "catalog",
        "radar_ids": [radar_id],
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "snapshot_time": snapshot_time,
        "expected_scan_ids": expected_scan_ids,
        "scans": scans,
    }


def _fetch_scan_page(
    catalog_url: str,
    *,
    radar_id: str,
    start_time: datetime,
    end_time: datetime,
    limit: int,
    cursor: str | None,
    snapshot_time: str | None,
) -> dict[str, Any]:
    query = {
        "radar_id": radar_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "limit": str(limit),
    }
    if cursor is not None:
        query["cursor"] = cursor
    if snapshot_time is not None:
        query["snapshot_time"] = snapshot_time
    separator = "&" if "?" in catalog_url else "?"
    with urlopen(f"{catalog_url}{separator}{urlencode(query)}", timeout=30) as response:  # noqa: S310
        value = json.load(response)
    if not isinstance(value, dict):
        raise AuditInputError("radar scan catalog response must be an object")
    return value


def _load_replay_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AuditInputError(f"replay manifest does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditInputError("replay manifest must be a JSON object")
    scans = value.get("scans")
    if not isinstance(scans, list):
        raise AuditInputError("replay manifest has no scans array")
    normalized_scans = [_normalize_source_scan(item) for item in scans]
    normalized_scans.sort(key=lambda item: (str(item["volume_end_time"]), str(item["scan_id"])))
    expected_scan_ids = value.get("expected_scan_ids")
    if expected_scan_ids is None:
        expected = [str(scan["scan_id"]) for scan in normalized_scans]
    elif isinstance(expected_scan_ids, list) and all(
        isinstance(item, str) and item for item in expected_scan_ids
    ):
        expected = list(expected_scan_ids)
    else:
        raise AuditInputError("replay manifest expected_scan_ids is invalid")
    if len(set(expected)) != len(expected):
        raise AuditInputError("replay manifest expected_scan_ids must be unique")
    scan_ids = {str(scan["scan_id"]) for scan in normalized_scans}
    if len(scan_ids) != len(normalized_scans):
        raise AuditInputError("replay manifest scans must be unique")
    if scan_ids != set(expected):
        raise AuditInputError("replay manifest scans must match expected_scan_ids exactly")
    return {
        "schema_version": str(value.get("schema_version", "1.0")),
        "mode": str(value.get("mode", "replay_manifest")),
        "radar_ids": list(
            value.get("radar_ids") or sorted({scan["radar_id"] for scan in normalized_scans})
        ),
        "start_time": str(value.get("start_time", normalized_scans[0]["volume_start_time"]))
        if normalized_scans
        else None,
        "end_time": str(value.get("end_time", normalized_scans[-1]["volume_end_time"]))
        if normalized_scans
        else None,
        "expected_scan_ids": expected,
        **({"snapshot_time": value["snapshot_time"]} if "snapshot_time" in value else {}),
        "scans": normalized_scans,
    }


def _normalize_source_scan(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise AuditInputError("replay manifest scan must be an object")
    required = {"scan_id", "radar_id", "volume_start_time", "volume_end_time"}
    if not required <= set(item):
        raise AuditInputError("replay manifest scan is missing required fields")
    normalized_uri = item.get("normalized_uri", item.get("input_uri"))
    if not isinstance(normalized_uri, str) or not normalized_uri:
        raise AuditInputError("replay manifest scan must include normalized_uri")
    return {
        "scan_id": str(item["scan_id"]),
        "radar_id": str(item["radar_id"]),
        "volume_start_time": str(item["volume_start_time"]),
        "volume_end_time": str(item["volume_end_time"]),
        "normalized_uri": normalized_uri,
        **(
            {"artifact_sha256": _validated_sha(item["artifact_sha256"])}
            if "artifact_sha256" in item
            else {}
        ),
        "temporal_context": _normalize_context_inputs(item.get("temporal_context", [])),
        "cross_radar_context": _normalize_context_inputs(item.get("cross_radar_context", [])),
    }


def _normalize_context_inputs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise AuditInputError("context inputs must be a list")
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise AuditInputError("context input must be an object")
        radar_id = item.get("radar_id")
        input_uri = item.get("input_uri")
        if not isinstance(radar_id, str) or not radar_id:
            raise AuditInputError("context input radar_id is invalid")
        if not isinstance(input_uri, str) or not input_uri:
            raise AuditInputError("context input input_uri is invalid")
        normalized.append(
            {
                "radar_id": radar_id,
                "input_uri": input_uri,
                **(
                    {"artifact_sha256": _validated_sha(item["artifact_sha256"])}
                    if "artifact_sha256" in item
                    else {}
                ),
            }
        )
    return normalized


def _validated_sha(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise AuditInputError("artifact_sha256 must be a lowercase SHA-256")
    return value


def _scan_inputs(scan):
    yield scan, "normalized_uri"
    for role in ("temporal_context", "cross_radar_context"):
        for entry in scan.get(role, []):
            yield entry, "input_uri"


def _freeze_input_manifest(manifest, reader, *, require_hashes):
    # Copy metadata only. Radar arrays are never retained across scans.
    result = json.loads(json.dumps(manifest))
    for scan in result["scans"]:
        for entry, uri_key in _scan_inputs(scan):
            expected = entry.get("artifact_sha256")
            if require_hashes and expected is None:
                raise AuditInputError("replay manifest requires artifact_sha256 for every input")
            if expected is not None:
                _validated_sha(expected)
            try:
                digest = artifact_sha256(reader.load(entry[uri_key]))
            except Exception:
                if expected is not None:
                    # Keep identity; execution reports failure and resume rechecks successes.
                    continue
                raise AuditInputError("catalog input is unavailable and cannot be frozen")
            if expected is not None and expected != digest:
                raise AuditInputError("frozen input checksum differs: " + entry[uri_key])
            entry["artifact_sha256"] = digest
    return result


class FrozenInputIntegrityError(Exception):
    """Must not be downgraded to optional context unavailability."""


class FrozenArtifactReader:
    def __init__(self, reader, manifest):
        self.reader = reader
        self.expected = {}
        for scan in manifest["scans"]:
            for entry, key in _scan_inputs(scan):
                uri, digest = entry[key], entry["artifact_sha256"]
                if uri in self.expected and self.expected[uri] != digest:
                    raise AuditInputError("conflicting frozen checksums for " + uri)
                self.expected[uri] = digest

    def load(self, uri):
        objects = self.reader.load(uri)
        if uri not in self.expected or artifact_sha256(objects) != self.expected[uri]:
            raise FrozenInputIntegrityError("frozen input checksum differs: " + uri)
        return objects


def _build_run_metadata(
    *,
    source_manifest: dict[str, Any],
    profile: Any,
    qc_config_path: Path,
    flag_definitions_path: Path,
    audit_mode: str = "evidence",
    ancillary_maps=None,
) -> dict[str, Any]:
    source_manifest_sha256 = hashlib.sha256(_canonical_json_bytes(source_manifest)).hexdigest()
    qc_config_sha256 = _file_sha256(qc_config_path)
    flag_definitions_sha256 = _file_sha256(flag_definitions_path)
    code_sha256 = _code_sha256()
    ancillary_assets = _ancillary_asset_summary(profile)
    fingerprint_payload = {
        "audit_mode": audit_mode,
        "runtime_assets": _runtime_asset_hashes(),
        "source_manifest_sha256": source_manifest_sha256,
        "qc_profile": profile.profile_version,
        "qc_pipeline_version": profile.pipeline_version,
        "decision_version": profile.decision_version,
        "flag_definition_version": profile.flag_definition_version,
        "qc_config_sha256": qc_config_sha256,
        "flag_definitions_sha256": flag_definitions_sha256,
        "code_sha256": code_sha256,
        "ancillary_assets": ancillary_assets,
        "ancillary_content_sha256": artifact_sha256(
            {
                f"{sweep}/{name}": value.dtype.str.encode()
                + str(value.shape).encode()
                + value.tobytes()
                for sweep, fields in (ancillary_maps or {}).items()
                for name, value in fields.items()
            }
        ),
    }
    return {
        **fingerprint_payload,
        "run_fingerprint": hashlib.sha256(_canonical_json_bytes(fingerprint_payload)).hexdigest(),
    }


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise AuditInputError(f"required file does not exist: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    return artifact_sha256(
        {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*.py"))}
    )


def _runtime_asset_hashes() -> dict[str, str]:
    result = {}
    for name in (
        "RAINPULSE_RADAR_CONFIG_DIR",
        "RAINPULSE_ANCILLARY_CONFIG",
        "RAINPULSE_ANCILLARY_ROOT",
        "RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE",
        "RAINPULSE_RADAR_ATTENUATION_PROFILE",
    ):
        value = os.getenv(name)
        if not value:
            continue
        root = Path(value)
        paths = (
            sorted(path for path in root.rglob("*") if path.is_file()) if root.is_dir() else [root]
        )
        for path in paths:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            result[f"{name}:{path.relative_to(root) if root.is_dir() else path.name}"] = (
                digest.hexdigest()
            )
    return result


def _ancillary_asset_summary(profile: Any) -> list[dict[str, str | None]]:
    assets = [
        {
            "name": "static_ground_clutter",
            "uri": profile.static_ground_clutter.asset_uri,
            "version": profile.static_ground_clutter.asset_version,
        },
        {
            "name": "coastline",
            "uri": profile.sea_ap.coastline_asset_uri,
            "version": profile.sea_ap.asset_version,
        },
    ]
    return [item for item in assets if item["uri"] or item["version"]]


def _load_resumable_records(
    path: Path,
    expected_fingerprint: str,
) -> tuple[dict[str, dict[str, Any]], int]:
    if not path.exists():
        return {}, 0
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditInputError("resume report must be a JSON object")
    fingerprint = value.get("run_fingerprint")
    if fingerprint != expected_fingerprint:
        raise AuditInputError("resume report fingerprint differs from current run fingerprint")
    scans = value.get("scans")
    if not isinstance(scans, list):
        raise AuditInputError("resume report has no scans array")
    completed: dict[str, dict[str, Any]] = {}
    skipped = 0
    for item in scans:
        if not isinstance(item, dict):
            raise AuditInputError("resume report scan entry is invalid")
        if item.get("status") != "completed":
            continue
        scan_id = item.get("scan_id")
        if not isinstance(scan_id, str) or not scan_id:
            raise AuditInputError("resume report scan_id is invalid")
        completed[scan_id] = {**item, "resume_reused": True}
        skipped += 1
    return completed, skipped


def _audit_scan(
    scan: dict[str, Any],
    *,
    profile: Any,
    audit_mode: str,
    reader: ArtifactObjectReader,
    client: Any,
    ancillary: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    base = {
        "scan_id": str(scan["scan_id"]),
        "radar_id": str(scan["radar_id"]),
        "volume_start_time": str(scan["volume_start_time"]),
        "volume_end_time": str(scan["volume_end_time"]),
        "normalized_uri": str(scan["normalized_uri"]),
        "resume_reused": False,
    }
    try:
        normalized = reader.load(base["normalized_uri"])
        if isinstance(reader, FrozenArtifactReader):
            # A frozen experiment cannot count a missing promised context as a success.
            for entry, key in _scan_inputs(scan):
                if key == "input_uri":
                    reader.load(entry[key])
        if audit_mode == "saturation":
            audit = audit_long_range_saturated_radials(normalized, profile)
            return {
                **base,
                "status": "completed",
                "affected": audit["saturated_ray_count"] > 0,
                **audit,
            }
        request = _build_audit_request(scan, profile)
        prepared, context_provenance = prepare_qc_inputs(
            request,
            normalized,
            profile,
            client,
            reader=reader,
            ancillary_maps=ancillary,
        )
        summary = apply_basic_qc(normalized, profile, **prepared).summary
        type_counts = summary["interference_type_ray_counts"]
        return {
            **base,
            "status": "completed",
            "affected": int(sum(type_counts.values())) > 0,
            "signature_version": "configured-radial-evidence-v1",
            "qc_profile": profile.profile_version,
            "qc_pipeline_version": profile.pipeline_version,
            "decision_version": profile.decision_version,
            "evidence_ray_count": int(sum(type_counts.values())),
            "flagged_ray_count": summary["radial_interference_ray_count"],
            "flagged_gate_count": summary["radial_interference_gate_count"],
            "flagged_area_km2": summary["radial_interference_area_km2"],
            "interference_type_ray_counts": type_counts,
            "interference_type_gate_counts": summary["interference_type_gate_counts"],
            "module_statuses": summary["module_statuses"],
            "context_fingerprint": context_provenance["context_fingerprint"],
            "radial_context": context_provenance,
        }
    except Exception as error:  # noqa: BLE001 - retain per-scan evidence and continue
        return {
            **base,
            "status": "failed",
            "affected": None,
            "error_code": type(error).__name__,
            "error": f"{type(error).__name__}: {error}",
        }


def _build_audit_request(scan: dict[str, Any], profile: Any) -> RadarQCRequested:
    scan_uuid = UUID(str(scan["scan_id"]))
    return RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid5(NAMESPACE_URL, f"rainpulse:radial-audit:event:{scan_uuid}")),
            "event_type": "radar.qc.requested.v1",
            "occurred_at": str(scan["volume_end_time"]),
            "run_id": str(uuid5(NAMESPACE_URL, f"rainpulse:radial-audit:run:{scan_uuid}")),
            "job_id": str(uuid5(NAMESPACE_URL, f"rainpulse:radial-audit:job:{scan_uuid}")),
            "trace_id": str(uuid5(NAMESPACE_URL, f"rainpulse:radial-audit:trace:{scan_uuid}")),
            "payload": {
                "scan_id": str(scan_uuid),
                "radar_id": str(scan["radar_id"]),
                "input_uri": str(scan["normalized_uri"]),
                "output_prefix": f"s3://rainpulse/radial-audit/{scan_uuid}/",
                "radar_config_version": "radial-audit",
                "qc_profile": profile.profile_version,
                "qc_pipeline_version": profile.pipeline_version,
                "flag_definition_version": profile.flag_definition_version,
                "temporal_context": [
                    {key: item[key] for key in ("radar_id", "input_uri")}
                    for item in scan.get("temporal_context", [])
                ],
                "cross_radar_context": [
                    {key: item[key] for key in ("radar_id", "input_uri")}
                    for item in scan.get("cross_radar_context", [])
                ],
            },
        }
    )


def _build_report(
    *,
    source_manifest: dict[str, Any],
    run_metadata: dict[str, Any],
    records: dict[str, dict[str, Any]],
    expected_scan_ids: list[str],
    processed_count: int,
    skipped_count: int,
) -> dict[str, Any]:
    ordered = [records[scan_id] for scan_id in expected_scan_ids if scan_id in records]
    completed = [item for item in ordered if item.get("status") == "completed"]
    failed = [item for item in ordered if item.get("status") == "failed"]
    affected = [item for item in completed if item.get("affected") is True]
    failure_reasons = Counter(str(item.get("error_code") or "unknown_error") for item in failed)
    skip_reasons = {"resume_completed": skipped_count} if skipped_count else {}
    return {
        "schema_version": "2.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_manifest": source_manifest,
        **run_metadata,
        "summary": {
            "expected_scan_count": len(expected_scan_ids),
            "completed_scan_count": len(completed),
            "failed_scan_count": len(failed),
            "processed_scan_count": processed_count,
            "skipped_scan_count": skipped_count,
            "affected_scan_count": len(affected),
            "affected_scan_ids": [str(item["scan_id"]) for item in affected],
            "failure_reasons": dict(failure_reasons),
            "skip_reasons": skip_reasons,
        },
        "scans": ordered,
    }


def _report_exit_code(report: dict[str, Any]) -> int:
    summary = report["summary"]
    expected = int(summary["expected_scan_count"])
    completed = int(summary["completed_scan_count"])
    failed = int(summary["failed_scan_count"])
    if expected == 0:
        return 3
    if failed > 0:
        return 4
    if completed == expected:
        return 0
    return 3


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AuditInputError("audit times must include a UTC offset")
    return parsed.astimezone(UTC)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _environment_path(name: str) -> Path:
    value = os.getenv(name)
    return Path(value) if value else Path("")


if __name__ == "__main__":
    sys.exit(main())
