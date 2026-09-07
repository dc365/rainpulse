from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import replace
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

from .attenuation import (
    AttenuationCoefficientConfig,
    AttenuationInputError,
    AttenuationProfile,
    AttenuationSweepResult,
    build_attenuation_artifact,
    load_attenuation_profile,
    process_kdp_attenuation_sweep,
)
from .attenuation_environment import load_environment_manifest, read_environment
from .calibration import (
    CoefficientTable,
    load_coefficient_table,
    resolve_attenuation_coefficients,
)
from .phase_processing import (
    PhaseProcessingInputError,
    load_phase_processing_profile,
    process_phidp_sweep,
)


class AuditInputError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build offline C2 attenuation shadow artifacts from a replay manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--phase-processing-profile", type=Path, required=True)
    parser.add_argument("--attenuation-profile", type=Path, required=True)
    parser.add_argument("--coefficient-table", type=Path)
    parser.add_argument("--environment-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        source_manifest = _load_replay_manifest(args.manifest)
        environments = (
            load_environment_manifest(args.environment_manifest)
            if args.environment_manifest
            else {}
        )
        phase_profile = load_phase_processing_profile(args.phase_processing_profile)
        attenuation_profile = load_attenuation_profile(args.attenuation_profile)
        coefficient_table = (
            load_coefficient_table(args.coefficient_table)
            if args.coefficient_table is not None
            else None
        )
        if (
            attenuation_profile.source_phase_processing_profile_version
            != phase_profile.profile_version
        ):
            raise AuditInputError(
                "attenuation profile source_phase_processing_profile_version differs "
                "from the selected phase-processing profile"
            )
        run_metadata = _build_run_metadata(
            source_manifest=source_manifest,
            phase_profile_path=args.phase_processing_profile,
            attenuation_profile_path=args.attenuation_profile,
            phase_profile_version=phase_profile.profile_version,
            attenuation_profile_version=attenuation_profile.profile_version,
            coefficient_table_path=args.coefficient_table,
            coefficient_table_version=(
                None if coefficient_table is None else coefficient_table.table_version
            ),
        )
        if args.environment_manifest is not None:
            run_metadata["environment_manifest_sha256"] = _file_sha256(args.environment_manifest)
            run_metadata["run_fingerprint"] = hashlib.sha256(
                _canonical_json_bytes(run_metadata)
            ).hexdigest()
        client = minio_client_from_environment()
        reader = ArtifactObjectReader(client)
        records: dict[str, dict[str, Any]] = {}
        for scan in source_manifest["scans"]:
            scan_id = str(scan["scan_id"])
            records[scan_id] = _audit_scan(
                scan,
                reader=reader,
                phase_profile=phase_profile,
                attenuation_profile=attenuation_profile,
                coefficient_table=coefficient_table,
                environment_entry=environments.get(scan_id),
            )
        report = _build_report(
            source_manifest=source_manifest,
            run_metadata=run_metadata,
            records=records,
        )
        _write_report(args.output, report)
        return _report_exit_code(report)
    except (AuditInputError, AttenuationInputError, PhaseProcessingInputError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _audit_scan(
    scan: dict[str, Any],
    *,
    reader: ArtifactObjectReader,
    phase_profile: Any,
    attenuation_profile: AttenuationProfile,
    coefficient_table: CoefficientTable | None,
    environment_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scan_id = str(scan["scan_id"])
    radar_id = str(scan["radar_id"])
    try:
        objects = reader.load(str(scan["normalized_uri"]))
        if artifact_sha256(objects) != scan["artifact_sha256"]:
            raise AuditInputError("normalized artifact SHA-256 differs from replay manifest")
        root = _normalized_root(objects)
        if str(root.attrs.get("radar_id", radar_id)).lower() != radar_id.lower():
            raise AuditInputError("normalized artifact radar_id differs from replay manifest")
        environment = (
            read_environment(
                environment_entry,
                radar_id=radar_id,
                scan_id=scan_id,
                volume_end_time_utc=str(scan["volume_end_time"]),
            )
            if environment_entry is not None
            else None
        )
        effective_profile, coefficient_table_version = _effective_attenuation_profile(
            attenuation_profile,
            coefficient_table=coefficient_table,
            radar_id=radar_id,
            temperature_c=None if environment is None else environment.temperature_c,
        )
        results_by_sweep: dict[str, AttenuationSweepResult] = {}
        phase_available_gate_count = 0
        skip_reason_counts: Counter[str] = Counter()
        for sweep_number in root["sweep_number"][:]:
            sweep_name = f"sweep_{int(sweep_number):03d}"
            if sweep_name not in root:
                continue
            group = root[sweep_name]
            if "DBZH" not in group or "range" not in group:
                raise AuditInputError(
                    f"normalized artifact sweep {sweep_name} lacks required DBZH/range fields"
                )
            dbzh = np.asarray(group["DBZH"][:], dtype="float32")
            range_m = np.asarray(group["range"][:], dtype="float32")
            if phase_profile.phase_field_name not in group:
                result = _empty_sweep_result(
                    dbzh,
                    np.full(dbzh.shape, np.nan, dtype="float32"),
                    skip_reason="phase_field_unavailable",
                )
            else:
                phase_result = process_phidp_sweep(
                    group[phase_profile.phase_field_name][:],
                    range_m,
                    profile=phase_profile,
                )
                phase_available_gate_count += int(
                    phase_result.diagnostics.get("available_gate_count", 0)
                )
                if int(phase_result.diagnostics.get("available_gate_count", 0)) <= 0:
                    result = _empty_sweep_result(
                        dbzh,
                        phase_result.kdp_deg_per_km,
                        skip_reason="phase_processing_unavailable",
                    )
                else:
                    result = process_kdp_attenuation_sweep(
                        dbzh,
                        phase_result.kdp_deg_per_km,
                        range_m,
                        profile=effective_profile,
                        kdp_available_mask=phase_result.available_mask,
                        blockage_fraction=None
                        if environment is None
                        else environment.blockage_by_sweep.get(sweep_name),
                    )
            results_by_sweep[sweep_name] = result
            skip_reason_counts.update(
                str(item) for item in result.diagnostics.get("skip_reasons", [])
            )
        artifact = build_attenuation_artifact(
            results_by_sweep,
            profile=effective_profile,
            artifact_id=str(uuid5(NAMESPACE_URL, f"rainpulse:c2:attenuation:{scan_id}")),
            created_at_utc=datetime.now(UTC),
            radar_id=radar_id,
            scan_id=scan_id,
            source_normalized_uri=str(scan["normalized_uri"]),
            source_manifest_sha256=artifact_sha256(objects),
            kdp_input_unit="degree/km",
            coefficient_table_version=coefficient_table_version,
        )
        if environment is not None:
            artifact["source_input"]["environment"] = environment.provenance
        attenuation_available_gate_count = int(
            sum(sweep["available_gate_count"] for sweep in artifact["sweeps"].values())
        )
        return {
            "scan_id": scan_id,
            "radar_id": radar_id,
            "status": "completed",
            "phase_processing_profile_version": phase_profile.profile_version,
            "attenuation_profile_version": attenuation_profile.profile_version,
            "phase_processing_available_gate_count": phase_available_gate_count,
            "attenuation_available_gate_count": attenuation_available_gate_count,
            "skip_reason_counts": dict(sorted(skip_reason_counts.items())),
            "artifact": artifact,
        }
    except Exception as error:
        return {
            "scan_id": scan_id,
            "radar_id": radar_id,
            "status": "failed",
            "error": str(error),
        }


def _empty_sweep_result(
    dbzh: np.ndarray,
    kdp: np.ndarray,
    *,
    skip_reason: str,
) -> AttenuationSweepResult:
    values = np.asarray(dbzh, dtype="float32")
    kdp_values = np.asarray(kdp, dtype="float32")
    if values.shape != kdp_values.shape:
        raise AuditInputError("empty attenuation sweep result requires matching DBZH/KDP shapes")
    ray_count = values.shape[0] if values.ndim == 2 else 0
    return AttenuationSweepResult(
        raw_dbzh_dbz=values.copy(),
        kdp_deg_per_km=kdp_values.copy(),
        specific_attenuation_db_per_km=np.full(values.shape, np.nan, dtype="float32"),
        attenuation_correction_db=np.full(values.shape, np.nan, dtype="float32"),
        corrected_dbzh_dbz=np.full(values.shape, np.nan, dtype="float32"),
        available_mask=np.zeros(values.shape, dtype="uint8"),
        segment_index=np.full(values.shape, -1, dtype="int32"),
        diagnostics={
            "ray_count": ray_count,
            "segment_count": 0,
            "processed_segment_count": 0,
            "available_gate_count": 0,
            "capped_gate_count": 0,
            "skip_reasons": [skip_reason] * max(ray_count, 1),
            "input_kdp_unit": "degree/km",
        },
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
    digest = item.get("artifact_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise AuditInputError("replay scan requires artifact_sha256")
    normalized_uri = item.get("normalized_uri", item.get("input_uri"))
    if not isinstance(normalized_uri, str) or not normalized_uri:
        raise AuditInputError("replay manifest scan must include normalized_uri")
    return {
        "scan_id": str(item["scan_id"]),
        "radar_id": str(item["radar_id"]),
        "volume_start_time": str(item["volume_start_time"]),
        "volume_end_time": str(item["volume_end_time"]),
        "normalized_uri": normalized_uri,
        "artifact_sha256": digest,
    }


def _normalized_root(objects: dict[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise AuditInputError("attenuation audit input is not a normalized radar volume")
    return root


def _build_run_metadata(
    *,
    source_manifest: dict[str, Any],
    phase_profile_path: Path,
    attenuation_profile_path: Path,
    phase_profile_version: str,
    attenuation_profile_version: str,
    coefficient_table_path: Path | None,
    coefficient_table_version: str | None,
) -> dict[str, str]:
    payload = {
        "source_manifest_sha256": hashlib.sha256(
            _canonical_json_bytes(source_manifest)
        ).hexdigest(),
        "phase_processing_profile_version": phase_profile_version,
        "attenuation_profile_version": attenuation_profile_version,
        "phase_processing_profile_sha256": _file_sha256(phase_profile_path),
        "attenuation_profile_sha256": _file_sha256(attenuation_profile_path),
        "code_sha256": _code_sha256(),
    }
    if coefficient_table_path is not None and coefficient_table_version is not None:
        payload["coefficient_table_version"] = coefficient_table_version
        payload["coefficient_table_sha256"] = _file_sha256(coefficient_table_path)
    return {
        **payload,
        "run_fingerprint": hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
    }


def _effective_attenuation_profile(
    profile: AttenuationProfile,
    *,
    coefficient_table: CoefficientTable | None,
    radar_id: str,
    temperature_c: float | None = None,
) -> tuple[AttenuationProfile, str | None]:
    if coefficient_table is None:
        return profile, None
    entry = next(
        (item for item in coefficient_table.entries if item.radar_id.lower() == radar_id.lower()),
        None,
    )
    if entry is None:
        raise AuditInputError(f"coefficient table has no entry for radar {radar_id}")
    if temperature_c is None:
        raise AuditInputError("attenuation_temperature_unavailable")
    resolved = resolve_attenuation_coefficients(
        coefficient_table,
        radar_id=radar_id,
        radar_band=profile.radar_band,
        temperature_c=temperature_c,
    )
    effective_profile = replace(
        profile,
        coefficients=AttenuationCoefficientConfig(
            source=resolved.source,
            coefficient_a=resolved.coefficient_a,
            exponent_b=resolved.exponent_b,
            minimum_kdp_deg_per_km=profile.coefficients.minimum_kdp_deg_per_km,
            maximum_specific_attenuation_db_per_km=(
                profile.coefficients.maximum_specific_attenuation_db_per_km
            ),
            maximum_correction_db=profile.coefficients.maximum_correction_db,
        ),
    )
    return effective_profile, resolved.coefficient_table_version


def _build_report(
    *,
    source_manifest: dict[str, Any],
    run_metadata: dict[str, str],
    records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered = [
        records[scan_id] for scan_id in source_manifest["expected_scan_ids"] if scan_id in records
    ]
    completed = [item for item in ordered if item["status"] == "completed"]
    failed = [item for item in ordered if item["status"] == "failed"]
    skip_reason_counts: Counter[str] = Counter()
    failure_reasons: Counter[str] = Counter()
    for item in completed:
        skip_reason_counts.update(
            {key: int(value) for key, value in item.get("skip_reason_counts", {}).items()}
        )
    for item in failed:
        failure_reasons.update([str(item.get("error", "unknown_error"))])
    return {
        "schema_version": "1.0",
        "mode": "attenuation_shadow_audit",
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
            "available_scan_count": int(
                sum(1 for item in completed if item["attenuation_available_gate_count"] > 0)
            ),
            "total_phase_processing_available_gate_count": int(
                sum(item["phase_processing_available_gate_count"] for item in completed)
            ),
            "total_attenuation_available_gate_count": int(
                sum(item["attenuation_available_gate_count"] for item in completed)
            ),
            "skip_reason_counts": dict(sorted(skip_reason_counts.items())),
            "failure_reasons": dict(sorted(failure_reasons.items())),
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


def _write_report(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _code_sha256() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        Path(__file__).with_name("attenuation.py"),
        Path(__file__).with_name("attenuation_environment.py"),
        Path(__file__).with_name("phase_processing.py"),
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
