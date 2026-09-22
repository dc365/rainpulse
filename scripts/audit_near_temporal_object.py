"""Read-only replay for the bounded two-snapshot near-radar temporal rule.

This script never publishes artifacts or changes CR/QPE qualification.  It measures
wall time, peak RSS, native-sweep evidence, and coverage of the latest CR winners.
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from typing import Mapping

import numpy as np

from rainpulse_algo.diagnostics.renderer import _open_group
from rainpulse_algo.radar.qc_engine.volume_review.data import array_digest
from rainpulse_algo.radar.qc_engine.volume_review.near_temporal_object import (
    NEAR_TEMPORAL_OBJECT_POLICY,
    evaluate_near_temporal_object,
)
from rainpulse_algo.radar.qc_engine.volume_review.receipts import load_npz
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
    parse_s3_uri,
)

RANGE_BINS_M = ((0, 10_000), (10_000, 20_000), (20_000, 30_000), (30_000, 50_000), (50_000, 75_000))


def _sweep_arrays(group: Mapping) -> dict[str, np.ndarray]:
    keys = (
        "azimuth", "elevation", "range", "DBZH_RAW", "RHOHV_RAW", "SNR_RAW",
        "VALID_MASK", "REFLECTIVITY_ELIGIBLE_FOR_CR", "CF_HARD_WEATHER_MASK",
        "CF_LOCAL_WEATHER_MASK", "CF_LEGACY_PROTECTED_MASK", "CF_WEATHER_PROXY_MASK",
        "CF_MIXED_MASK", "CF_BG_ENHANCEMENT_MASK",
    )
    return {key: np.array(group[key][:], copy=True) for key in keys if key in group}


def _load_selected_bundle_files(reader: ArtifactObjectReader, uri: str, wanted: set[str]) -> dict[str, bytes]:
    """Load selected logical files without fetching all diagnostic PNG layers."""
    bucket, prefix = parse_s3_uri(uri)
    marker_key = f"{prefix.rstrip('/')}/_SUCCESS.json"
    marker = json.loads(reader._get_bytes(bucket, marker_key, max_bytes=16 * 1024**2))
    manifest = {item["key"]: item for item in marker["objects"]}
    missing = wanted - manifest.keys()
    if missing:
        raise KeyError(f"diagnostic bundle lacks selected files: {sorted(missing)}")
    data_prefix = marker.get("data_prefix", "")
    object_prefix = f"{prefix.rstrip('/')}/{data_prefix}" if data_prefix else prefix.rstrip("/")
    selected: dict[str, bytes] = {}
    for key in sorted(wanted):
        item = manifest[key]
        value = reader._get_bytes(bucket, f"{object_prefix}/{key}", max_bytes=int(item["size_bytes"]))
        if len(value) != int(item["size_bytes"]):
            raise RuntimeError(f"diagnostic object size differs for {key}")
        selected[key] = value
    return selected


def _elevation(a: Mapping) -> float:
    value = np.asarray(a["elevation"], float)
    return float(np.nanmedian(value)) if value.size else float("nan")


def _range_bucket(range_m: float) -> str:
    for low, high in RANGE_BINS_M:
        if range_m < high:
            return f"{low // 1000:g}-{high // 1000:g}km"
    return "outside"


def _empty_bucket_stats() -> dict[str, dict[str, int]]:
    return {
        f"{low // 1000:g}-{high // 1000:g}km": {
            "residual_winner_pixels": 0,
            "candidate_covered": 0,
            "object_covered": 0,
            "trusted_before": 0,
            "trusted_after_simulated": 0,
        }
        for low, high in RANGE_BINS_M
    }


def _winner_coverage(
    composite_arrays: Mapping[str, np.ndarray],
    sources: list[Mapping],
    current_root: Mapping,
    radar_id: str,
    evidence_by_sweep: Mapping[str, Mapping],
) -> dict:
    target_sources = {
        index for index, source in enumerate(sources)
        if str(source.get("radar_id", "")).lower() == radar_id.lower()
    }
    source_values = np.asarray(composite_arrays["WINNER_SOURCE"])
    ray_values = np.asarray(composite_arrays["WINNER_RAY"])
    gate_values = np.asarray(composite_arrays["WINNER_GATE"])
    trusted_values = np.asarray(composite_arrays["CR_TRUSTED"])
    selected = np.isin(source_values, list(target_sources)) if target_sources else np.zeros(source_values.shape, bool)
    selected &= np.isfinite(trusted_values) & (trusted_values >= NEAR_TEMPORAL_OBJECT_POLICY["minimum_dbz"])
    selected &= trusted_values <= NEAR_TEMPORAL_OBJECT_POLICY["maximum_dbz"]

    stats = _empty_bucket_stats()
    for row, column in zip(*np.nonzero(selected), strict=True):
        source_index = int(source_values[row, column])
        sweep = int(sources[source_index]["sweep"])
        evidence = evidence_by_sweep.get(f"sweep_{sweep:03d}")
        if evidence is None:
            continue
        ray = int(ray_values[row, column])
        gate = int(gate_values[row, column])
        ranges = np.asarray(current_root[f"sweep_{sweep:03d}"]["range"][:], float)
        if not (0 <= ray < ranges.size) or not (0 <= gate < ranges.size):
            continue
        range_m = float(ranges[gate])
        bucket = _range_bucket(range_m)
        if bucket not in stats or not (0 <= ray < evidence["NTO_OBJECT_MASK"].shape[0]):
            continue
        if not (0 <= gate < evidence["NTO_OBJECT_MASK"].shape[1]):
            continue
        item = stats[bucket]
        item["residual_winner_pixels"] += 1
        item["candidate_covered"] += int(evidence["NTO_CANDIDATE_MASK"][ray, gate] == 1)
        item["object_covered"] += int(evidence["NTO_OBJECT_MASK"][ray, gate] == 1)
    for item in stats.values():
        item["trusted_before"] = item["residual_winner_pixels"]
        item["trusted_after_simulated"] = item["residual_winner_pixels"] - item["object_covered"]
    return stats


def replay(current_uri: str, prior_uris: list[str], diagnostic_uri: str | None) -> dict:
    if len(prior_uris) != NEAR_TEMPORAL_OBJECT_POLICY["prior_snapshots"]:
        raise ValueError("exactly two prior URIs are required")
    client = minio_client_from_environment()
    reader = ArtifactObjectReader(client)
    started = time.perf_counter()
    load_started = time.perf_counter()
    current_objects = reader.load(current_uri)
    current_root = _open_group(current_objects)
    prior_roots = tuple(_open_group(reader.load(uri)) for uri in prior_uris)
    composite_arrays = None
    composite_sources = None
    if diagnostic_uri:
        files = _load_selected_bundle_files(
            reader,
            diagnostic_uri,
            {"volume_review/composite.npz", "volume_review/composite.json"},
        )
        composite_arrays = load_npz(files["volume_review/composite.npz"])
        composite_sources = json.loads(files["volume_review/composite.json"])["sources"]
    load_s = time.perf_counter() - load_started

    radar_id = str(current_root.attrs.get("radar_id", "")).lower()
    if not radar_id:
        raise ValueError("current QC volume lacks radar_id")
    evidence_by_sweep: dict[str, dict] = {}
    summaries: list[dict] = []
    evaluate_started = time.perf_counter()
    current_digest_inputs: dict[str, int] = {}
    for number in np.asarray(current_root["sweep_number"][:], int):
        key = f"sweep_{number:03d}"
        current_group = current_root[key]
        current = _sweep_arrays(current_group)
        required = {"azimuth", "range", "DBZH_RAW", "RHOHV_RAW", "SNR_RAW"}
        if not required.issubset(current):
            summaries.append({"sweep": key, "status": "SKIPPED_MISSING_CURRENT_MOMENTS"})
            continue
        priors = []
        mismatched = False
        for root in prior_roots:
            if key not in root:
                mismatched = True
                break
            past = _sweep_arrays(root[key])
            needed = {"azimuth", "range", "RHOHV_RAW", "SNR_RAW", "DBZH_RAW"}
            if not needed.issubset(past):
                priors.append(past)
                continue
            if abs(_elevation(current) - _elevation(past)) > 0.2:
                mismatched = True
                break
            priors.append(past)
        if mismatched:
            summaries.append({"sweep": key, "status": "SKIPPED_PRIOR_ELEVATION_OR_SWEEP_MISMATCH"})
            continue
        before_digest = array_digest(current)
        evidence = evaluate_near_temporal_object(current, tuple(priors), sweep=key)
        after_digest = array_digest(current)
        if before_digest != after_digest:
            raise RuntimeError("audit replay mutated current input")
        evidence_by_sweep[key] = evidence.arrays
        current_digest_inputs[key] = int(np.asarray(current["DBZH_RAW"]).size)
        summaries.append(evidence.summary)
    evaluate_s = time.perf_counter() - evaluate_started
    coverage = (
        _winner_coverage(composite_arrays, composite_sources, current_root, radar_id, evidence_by_sweep)
        if composite_arrays is not None else None
    )
    totals = {
        name: int(sum(item.get(name, 0) for item in summaries))
        for name in ("candidate_gates", "prior1_gates", "prior2_gates", "support_gates", "object_gates", "object_count")
    }
    return {
        "status": "EVALUATED",
        "mode": "audit-only",
        "radar_id": radar_id,
        "policy": NEAR_TEMPORAL_OBJECT_POLICY,
        "timing": {
            "load_s": round(load_s, 3),
            "evaluate_s": round(evaluate_s, 3),
            "total_s": round(time.perf_counter() - started, 3),
        },
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "sweeps": summaries,
        "totals": totals,
        "cr_residual_winner_coverage": coverage,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current_uri")
    parser.add_argument("prior_uris", nargs=2)
    parser.add_argument("--diagnostic-uri")
    args = parser.parse_args()
    print(json.dumps(replay(args.current_uri, args.prior_uris, args.diagnostic_uri), ensure_ascii=False))


if __name__ == "__main__":
    main()
