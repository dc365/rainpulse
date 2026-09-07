from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    artifact_sha256,
    minio_client_from_environment,
)

from .ancillary import load_source
from .config import load_radar_config
from .dem import VerifiedDEMTileStore
from .qc import load_qc_profile
from .qc_context import extract_radial_candidate_sweeps
from .qc_geometry import RadarBeamContext, radar_beam_context_from_config
from .relative_bias import (
    RelativeBiasInputError,
    RelativeBiasProfile,
    aggregate_relative_bias_artifacts,
    build_relative_bias_artifact,
    compare_scan_to_reference,
    load_relative_bias_profile,
)


class AuditInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class RelativeBiasResources:
    terrain: VerifiedDEMTileStore | None
    beam_contexts_by_radar: dict[str, RadarBeamContext]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build offline C2 inter-radar relative-bias evidence from a replay manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--qc-config", type=Path, required=True)
    parser.add_argument("--flag-definitions", type=Path, required=True)
    parser.add_argument("--radar-config-dir", type=Path)
    parser.add_argument("--ancillary-config", type=Path)
    parser.add_argument("--ancillary-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        source_manifest = _load_replay_manifest(args.manifest)
        profile = load_relative_bias_profile(args.profile)
        qc_profile = load_qc_profile(args.qc_config, args.flag_definitions)
        resources = _load_geometry_resources(args)
        run_metadata = _build_run_metadata(
            source_manifest=source_manifest,
            profile=profile,
            profile_path=args.profile,
            qc_config_path=args.qc_config,
            flag_definitions_path=args.flag_definitions,
        )
        client = minio_client_from_environment()
        reader = ArtifactObjectReader(client)
        records: dict[str, dict[str, Any]] = {}
        for scan in source_manifest["scans"]:
            scan_id = str(scan["scan_id"])
            records[scan_id] = _audit_scan(
                scan,
                reader=reader,
                resources=resources,
                profile=profile,
                qc_profile=qc_profile,
            )
        report = _build_report(
            source_manifest=source_manifest,
            run_metadata=run_metadata,
            records=records,
            profile=profile,
        )
        _write_report(args.output, report)
        return _report_exit_code(report)
    except (AuditInputError, RelativeBiasInputError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _audit_scan(
    scan: dict[str, Any],
    *,
    reader: ArtifactObjectReader,
    resources: RelativeBiasResources,
    profile: RelativeBiasProfile,
    qc_profile: Any,
) -> dict[str, Any]:
    scan_id = str(scan["scan_id"])
    radar_id = str(scan["radar_id"])
    try:
        current_objects = reader.load(str(scan["normalized_uri"]))
        if artifact_sha256(current_objects) != scan["artifact_sha256"]:
            raise AuditInputError("primary artifact SHA-256 differs from manifest")
        current_root = _normalized_root(current_objects)
        current_end_time = _required_utc_time(
            current_root.attrs.get("volume_end_time_utc") or scan.get("volume_end_time"),
            field_name="current volume end time",
        )
        current_beam_context = resources.beam_contexts_by_radar.get(radar_id.lower())
        grouping_mode = "process_id" if scan.get("process_id") else "scan_id_fallback"
        group_id = str(scan.get("process_id") or scan_id)
        comparisons: list[dict[str, Any]] = []
        comparable_gate_count = 0
        computed_comparison_count = 0
        for reference_input in scan["cross_radar_context"]:
            reference_radar_id = str(reference_input["radar_id"])
            try:
                reference_objects = reader.load(str(reference_input["input_uri"]))
                reference_root = _normalized_root(reference_objects)
            except Exception as error:
                comparisons.append(
                    {
                        "reference_radar_id": reference_radar_id,
                        "reference_input_uri": str(reference_input["input_uri"]),
                        "status": "skipped",
                        "skip_reason": f"reference_artifact_unavailable:{error}",
                        "comparable_gate_count": 0,
                    }
                )
                continue
            if artifact_sha256(reference_objects) != reference_input["artifact_sha256"]:
                raise AuditInputError("reference artifact SHA-256 differs from manifest")
            actual_reference_radar_id = str(reference_root.attrs.get("radar_id", "")).lower()
            if actual_reference_radar_id == radar_id.lower():
                comparisons.append(
                    {
                        "reference_radar_id": reference_radar_id,
                        "reference_input_uri": str(reference_input["input_uri"]),
                        "status": "skipped",
                        "skip_reason": "cross_same_radar",
                        "comparable_gate_count": 0,
                    }
                )
                continue
            if (
                actual_reference_radar_id
                and actual_reference_radar_id != reference_radar_id.lower()
            ):
                comparisons.append(
                    {
                        "reference_radar_id": reference_radar_id,
                        "reference_input_uri": str(reference_input["input_uri"]),
                        "status": "skipped",
                        "skip_reason": "cross_radar_id_mismatch",
                        "comparable_gate_count": 0,
                    }
                )
                continue
            if str(reference_root.attrs.get("radar_health", "")).upper() == "UNAVAILABLE":
                comparisons.append(
                    {
                        "reference_radar_id": reference_radar_id,
                        "reference_input_uri": str(reference_input["input_uri"]),
                        "status": "skipped",
                        "skip_reason": "radar_health_unavailable",
                        "comparable_gate_count": 0,
                    }
                )
                continue
            reference_end_time = _required_utc_time(
                reference_root.attrs.get("volume_end_time_utc"),
                field_name="reference volume end time",
            )
            time_offset_seconds = abs((reference_end_time - current_end_time).total_seconds())
            if time_offset_seconds > profile.maximum_time_offset_seconds:
                comparisons.append(
                    {
                        "reference_radar_id": reference_radar_id,
                        "reference_scan_id": str(reference_root.attrs.get("scan_id", "")),
                        "reference_input_uri": str(reference_input["input_uri"]),
                        "time_offset_seconds": float(time_offset_seconds),
                        "status": "skipped",
                        "skip_reason": "time_out_of_window",
                        "comparable_gate_count": 0,
                    }
                )
                continue
            hard_interference_by_sweep = _reference_hard_interference(
                reference_objects,
                qc_profile,
            )
            comparison = compare_scan_to_reference(
                current_root,
                reference_root,
                current_beam_context=current_beam_context,
                reference_beam_context=resources.beam_contexts_by_radar.get(
                    reference_radar_id.lower()
                ),
                terrain=resources.terrain,
                profile=profile,
                valid_range_dbz=tuple(qc_profile.echo.dbzh_valid_range_dbz),
                hard_interference_by_sweep=hard_interference_by_sweep,
            )
            comparison.update(
                {
                    "reference_radar_id": reference_radar_id,
                    "reference_scan_id": str(reference_root.attrs.get("scan_id", "")),
                    "reference_input_uri": str(reference_input["input_uri"]),
                    "time_offset_seconds": float(time_offset_seconds),
                }
            )
            comparisons.append(comparison)
            if comparison["status"] == "computed":
                computed_comparison_count += 1
                comparable_gate_count += int(comparison["comparable_gate_count"])

        artifact = build_relative_bias_artifact(
            comparisons,
            profile=profile,
            artifact_id=str(uuid5(NAMESPACE_URL, f"rainpulse:c2:relative-bias:{scan_id}")),
            created_at_utc=datetime.now(UTC),
            radar_id=radar_id,
            scan_id=scan_id,
            source_normalized_uri=str(scan["normalized_uri"]),
            source_normalized_artifact_sha256=artifact_sha256(current_objects),
            source_qc_profile_version=qc_profile.profile_version,
            grouping_mode=grouping_mode,
            group_id=group_id,
        )
        return {
            "scan_id": scan_id,
            "radar_id": radar_id,
            "status": "completed",
            "grouping_mode": grouping_mode,
            "group_id": group_id,
            "computed_comparison_count": computed_comparison_count,
            "comparable_gate_count": comparable_gate_count,
            "comparisons": comparisons,
            "artifact": artifact,
        }
    except Exception as error:
        return {
            "scan_id": scan_id,
            "radar_id": radar_id,
            "status": "failed",
            "error": str(error),
        }


def _reference_hard_interference(
    normalized_objects: dict[str, bytes],
    qc_profile: Any,
) -> dict[str, np.ndarray]:
    try:
        candidates = extract_radial_candidate_sweeps(normalized_objects, qc_profile)
    except Exception:
        return {}
    return {
        name: np.asarray(sweep.hard_flag_by_ray, dtype=bool) for name, sweep in candidates.items()
    }


def _load_geometry_resources(args: argparse.Namespace) -> RelativeBiasResources:
    beam_contexts_by_radar: dict[str, RadarBeamContext] = {}
    terrain: VerifiedDEMTileStore | None = None
    dem_versions: set[str] = set()

    radar_config_dir = args.radar_config_dir
    if radar_config_dir is not None:
        if not radar_config_dir.is_dir():
            raise AuditInputError(f"--radar-config-dir is not a directory: {radar_config_dir}")
        for path in sorted(radar_config_dir.glob("*.yaml")):
            try:
                radar_config = load_radar_config(path)
                beam_context = radar_beam_context_from_config(radar_config)
            except (OSError, ValueError):
                continue
            beam_contexts_by_radar[beam_context.radar_id.lower()] = beam_context
            dem_asset_version = radar_config.ancillary.get("dem_asset_version")
            if isinstance(dem_asset_version, str) and dem_asset_version:
                dem_versions.add(dem_asset_version)

    if (
        args.ancillary_config is not None
        and args.ancillary_root is not None
        and len(dem_versions) == 1
    ):
        if not args.ancillary_config.is_file():
            raise AuditInputError(f"--ancillary-config is not a file: {args.ancillary_config}")
        if not args.ancillary_root.is_dir():
            raise AuditInputError(f"--ancillary-root is not a directory: {args.ancillary_root}")
        ancillary_source = load_source(args.ancillary_config)
        terrain = VerifiedDEMTileStore(
            ancillary_source,
            args.ancillary_root,
            expected_asset_version=next(iter(dem_versions)),
            expected_config_version=ancillary_source.config_version,
        )
    return RelativeBiasResources(
        terrain=terrain,
        beam_contexts_by_radar=beam_contexts_by_radar,
    )


def _load_replay_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AuditInputError(f"replay manifest does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditInputError("replay manifest must be a JSON object")
    scans = value.get("scans")
    if not isinstance(scans, list):
        raise AuditInputError("replay manifest has no scans array")
    normalized = [_normalize_scan(item) for item in scans]
    normalized.sort(key=lambda item: (item["volume_end_time"], item["scan_id"]))
    expected_scan_ids = value.get("expected_scan_ids")
    if expected_scan_ids is None:
        expected = [item["scan_id"] for item in normalized]
    elif isinstance(expected_scan_ids, list) and all(
        isinstance(item, str) and item for item in expected_scan_ids
    ):
        expected = list(expected_scan_ids)
    else:
        raise AuditInputError("replay manifest expected_scan_ids is invalid")
    if {item["scan_id"] for item in normalized} != set(expected):
        raise AuditInputError("replay manifest scans must match expected_scan_ids exactly")
    return {
        "schema_version": str(value.get("schema_version", "1.0")),
        "mode": str(value.get("mode", "replay_manifest")),
        "radar_ids": list(
            value.get("radar_ids") or sorted({item["radar_id"] for item in normalized})
        ),
        "start_time": str(value.get("start_time", normalized[0]["volume_start_time"]))
        if normalized
        else None,
        "end_time": str(value.get("end_time", normalized[-1]["volume_end_time"]))
        if normalized
        else None,
        "expected_scan_ids": expected,
        "scans": normalized,
    }


def _normalize_scan(item: Any) -> dict[str, Any]:
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
        "artifact_sha256": _required_digest(item),
        "process_id": _optional_text(item.get("process_id")),
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
                "artifact_sha256": _required_digest(item),
            }
        )
    return normalized


def _required_digest(item: dict[str, Any]) -> str:
    value = item.get("artifact_sha256")
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise AuditInputError("replay input requires artifact_sha256")
    return value


def _build_run_metadata(
    *,
    source_manifest: dict[str, Any],
    profile: RelativeBiasProfile,
    profile_path: Path,
    qc_config_path: Path,
    flag_definitions_path: Path,
) -> dict[str, str]:
    payload = {
        "source_manifest_sha256": hashlib.sha256(
            _canonical_json_bytes(source_manifest)
        ).hexdigest(),
        "relative_bias_profile_version": profile.profile_version,
        "relative_bias_profile_sha256": _file_sha256(profile_path),
        "qc_config_sha256": _file_sha256(qc_config_path),
        "flag_definitions_sha256": _file_sha256(flag_definitions_path),
        "code_sha256": _code_sha256(),
    }
    return {
        **payload,
        "run_fingerprint": hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
    }


def _build_report(
    *,
    source_manifest: dict[str, Any],
    run_metadata: dict[str, str],
    records: dict[str, dict[str, Any]],
    profile: RelativeBiasProfile,
) -> dict[str, Any]:
    ordered = [
        records[scan_id] for scan_id in source_manifest["expected_scan_ids"] if scan_id in records
    ]
    completed = [item for item in ordered if item["status"] == "completed"]
    failed = [item for item in ordered if item["status"] == "failed"]
    failure_reasons: Counter[str] = Counter()
    computed_comparison_count = 0
    skipped_comparison_count = 0
    comparable_gate_count = 0
    artifacts: list[dict[str, Any]] = []
    for item in completed:
        artifacts.append(item["artifact"])
        computed_comparison_count += int(item.get("computed_comparison_count", 0))
        comparable_gate_count += int(item.get("comparable_gate_count", 0))
        skipped_comparison_count += int(
            sum(
                1 for comparison in item.get("comparisons", []) if comparison["status"] == "skipped"
            )
        )
    for item in failed:
        failure_reasons.update([str(item.get("error", "unknown_error"))])
    aggregates = aggregate_relative_bias_artifacts(artifacts, profile=profile)
    return {
        "schema_version": "1.0",
        "mode": "relative_bias_shadow_audit",
        "source_manifest": {
            "schema_version": source_manifest["schema_version"],
            "mode": source_manifest["mode"],
            "radar_ids": source_manifest["radar_ids"],
            "start_time": source_manifest["start_time"],
            "end_time": source_manifest["end_time"],
            "expected_scan_ids": source_manifest["expected_scan_ids"],
        },
        "run_metadata": run_metadata,
        "summary": {
            "expected_scan_count": len(source_manifest["expected_scan_ids"]),
            "completed_scan_count": len(completed),
            "failed_scan_count": len(failed),
            "computed_comparison_count": computed_comparison_count,
            "skipped_comparison_count": skipped_comparison_count,
            "comparable_gate_count": comparable_gate_count,
            "aggregate_count": len(aggregates),
            "failure_reasons": dict(sorted(failure_reasons.items())),
        },
        "aggregates": aggregates,
        "scans": ordered,
    }


def _normalized_root(objects: dict[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise AuditInputError("relative-bias audit input is not a normalized radar volume")
    return root


def _required_utc_time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise AuditInputError(f"{field_name} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuditInputError(f"{field_name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditInputError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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


def _write_report(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _code_sha256() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        Path(__file__).with_name("relative_bias.py"),
        Path(__file__).with_name("qc_geometry.py"),
        Path(__file__).with_name("qc_context.py"),
        Path(__file__).with_name("qc.py"),
        Path(__file__).parents[1] / "worker" / "object_store.py",
    ):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise AuditInputError(f"required file does not exist: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
