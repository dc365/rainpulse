from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)

from .qc import apply_basic_qc, audit_long_range_saturated_radials, load_qc_profile


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit normalized radar volumes for versioned radial QC evidence."
    )
    parser.add_argument("--catalog-url", default="http://api:8080/api/v1/radar-scans")
    parser.add_argument("--radar-id", required=True)
    parser.add_argument("--start-time", required=True, help="inclusive ISO-8601 UTC time")
    parser.add_argument("--end-time", required=True, help="exclusive ISO-8601 UTC time")
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

    start = _parse_time(args.start_time)
    end = _parse_time(args.end_time)
    if end <= start:
        parser.error("--end-time must be later than --start-time")
    if args.limit <= 0 or args.limit > 200:
        parser.error("--limit must be between 1 and 200")

    profile = load_qc_profile(args.qc_config, args.flag_definitions)
    scans = _fetch_scans(args.catalog_url, args.radar_id, args.limit)
    scans = [
        scan
        for scan in scans
        if start <= _parse_time(str(scan["volume_end_time"])) < end
    ]
    scans.sort(key=lambda item: (item["volume_end_time"], item["scan_id"]))
    completed = _load_completed(args.output) if args.resume else {}
    reader = ArtifactObjectReader(minio_client_from_environment())

    for index, scan in enumerate(scans, start=1):
        scan_id = str(scan["scan_id"])
        if scan_id in completed:
            continue
        try:
            normalized = reader.load(str(scan["normalized_uri"]))
            if args.audit_mode == "saturation":
                audit = audit_long_range_saturated_radials(normalized, profile)
                affected = audit["saturated_ray_count"] > 0
            else:
                summary = apply_basic_qc(normalized, profile).summary
                type_counts = summary["interference_type_ray_counts"]
                audit = {
                    "signature_version": "configured-radial-evidence-v1",
                    "qc_profile": profile.profile_version,
                    "qc_pipeline_version": profile.pipeline_version,
                    "evidence_ray_count": int(sum(type_counts.values())),
                    "flagged_ray_count": summary["radial_interference_ray_count"],
                    "flagged_gate_count": summary["radial_interference_gate_count"],
                    "flagged_area_km2": summary["radial_interference_area_km2"],
                    "interference_type_ray_counts": type_counts,
                    "interference_type_gate_counts": summary[
                        "interference_type_gate_counts"
                    ],
                    "module_statuses": summary["module_statuses"],
                }
                affected = audit["evidence_ray_count"] > 0
            record = {
                "scan_id": scan_id,
                "radar_id": scan["radar_id"],
                "volume_start_time": scan["volume_start_time"],
                "volume_end_time": scan["volume_end_time"],
                "normalized_uri": scan["normalized_uri"],
                "affected": affected,
                **audit,
            }
        except Exception as error:  # noqa: BLE001 - retain per-scan evidence and continue
            record = {
                "scan_id": scan_id,
                "radar_id": scan.get("radar_id"),
                "volume_start_time": scan.get("volume_start_time"),
                "volume_end_time": scan.get("volume_end_time"),
                "normalized_uri": scan.get("normalized_uri"),
                "affected": None,
                "error": f"{type(error).__name__}: {error}",
            }
        completed[scan_id] = record
        manifest = _build_manifest(
            radar_id=args.radar_id,
            start=start,
            end=end,
            profile_version=profile.profile_version,
            pipeline_version=profile.pipeline_version,
            audit_mode=args.audit_mode,
            expected_count=len(scans),
            records=completed,
        )
        _write_manifest(args.output, manifest)
        print(
            json.dumps(
                {
                    "completed": len(completed),
                    "expected": len(scans),
                    "scan_id": scan_id,
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

    manifest = _build_manifest(
        radar_id=args.radar_id,
        start=start,
        end=end,
        profile_version=profile.profile_version,
        pipeline_version=profile.pipeline_version,
        audit_mode=args.audit_mode,
        expected_count=len(scans),
        records=completed,
    )
    _write_manifest(args.output, manifest)
    return 1 if manifest["summary"]["error_count"] else 0


def _fetch_scans(catalog_url: str, radar_id: str, limit: int) -> list[dict[str, Any]]:
    query = urlencode({"radar_id": radar_id, "limit": limit})
    separator = "&" if "?" in catalog_url else "?"
    with urlopen(f"{catalog_url}{separator}{query}", timeout=30) as response:  # noqa: S310
        value = json.load(response)
    items = value.get("items") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("radar scan catalog response has no items array")
    required = {
        "scan_id",
        "radar_id",
        "volume_start_time",
        "volume_end_time",
        "normalized_uri",
    }
    for item in items:
        if not isinstance(item, dict) or not required <= set(item):
            raise RuntimeError("radar scan catalog contains an invalid item")
    return items


def _build_manifest(
    *,
    radar_id: str,
    start: datetime,
    end: datetime,
    profile_version: str,
    pipeline_version: str,
    audit_mode: str,
    expected_count: int,
    records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(
        records.values(),
        key=lambda item: (str(item.get("volume_end_time")), str(item["scan_id"])),
    )
    affected = [item for item in ordered if item.get("affected") is True]
    errors = [item for item in ordered if item.get("error")]
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "radar_id": radar_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "qc_profile": profile_version,
        "qc_pipeline_version": pipeline_version,
        "audit_mode": audit_mode,
        "summary": {
            "expected_scan_count": expected_count,
            "completed_scan_count": len(ordered),
            "affected_scan_count": len(affected),
            "error_count": len(errors),
            "affected_scan_ids": [item["scan_id"] for item in affected],
        },
        "scans": ordered,
    }


def _load_completed(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    scans = value.get("scans") if isinstance(value, dict) else None
    if not isinstance(scans, list):
        raise RuntimeError("resume manifest has no scans array")
    return {str(item["scan_id"]): item for item in scans}


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("audit times must include a UTC offset")
    return parsed.astimezone(UTC)


def _environment_path(name: str) -> Path:
    value = os.getenv(name)
    return Path(value) if value else Path("")


if __name__ == "__main__":
    sys.exit(main())
