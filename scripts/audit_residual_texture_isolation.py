"""Read-only replay for native residual texture/isolation audit evidence.

The script measures CR residual coverage and resource use. It never changes QC,
CR/QPE qualification, or publishes an artifact.
"""

from __future__ import annotations

import argparse
import json
import resource
import time
from typing import Mapping

import numpy as np

from audit_near_temporal_object import _load_selected_bundle_files
from rainpulse_algo.diagnostics.renderer import _open_group
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.context import (
    ground,
    height,
)
from rainpulse_algo.radar.qc_engine.volume_review.data import array_digest
from rainpulse_algo.radar.qc_engine.volume_review.receipts import load_npz
from rainpulse_algo.radar.qc_engine.volume_review.sampling import polar_targets
from rainpulse_algo.radar.qc_engine.volume_review.residual_texture_isolation import (
    RESIDUAL_TEXTURE_ISOLATION_POLICY,
    evaluate_residual_texture_isolation,
)
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)

RANGE_BINS_M = (
    (0, 10_000),
    (10_000, 20_000),
    (20_000, 30_000),
    (30_000, 50_000),
    (50_000, 75_000),
)


def _sweep_arrays(group: Mapping) -> dict[str, np.ndarray]:
    keys = (
        "azimuth",
        "elevation",
        "range",
        "DBZH_RAW",
        "RHOHV_RAW",
        "SNR_RAW",
        "VALID_MASK",
        "REFLECTIVITY_ELIGIBLE_FOR_CR",
        "CF_HARD_WEATHER_MASK",
        "CF_LOCAL_WEATHER_MASK",
        "CF_LEGACY_PROTECTED_MASK",
        "CF_WEATHER_PROXY_MASK",
        "CF_POLAR_SCORE",
        "CF_NEIGHBOUR_FRACTION",
    )
    return {key: np.array(group[key][:], copy=True) for key in keys if key in group}


def _range_bucket(range_m: float) -> str:
    for low, high in RANGE_BINS_M:
        if range_m < high:
            return f"{low // 1000:g}-{high // 1000:g}km"
    return "outside"


def _empty_stats() -> dict[str, dict[str, int]]:
    return {
        f"{low // 1000:g}-{high // 1000:g}km": {
            "residual_winner_pixels": 0,
            "candidate_covered": 0,
            "object_covered": 0,
            "blob_covered": 0,
            "isolated_covered": 0,
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
        index
        for index, source in enumerate(sources)
        if str(source.get("radar_id", "")).lower() == radar_id.lower()
    }
    source_values = np.asarray(composite_arrays["WINNER_SOURCE"])
    ray_values = np.asarray(composite_arrays["WINNER_RAY"])
    gate_values = np.asarray(composite_arrays["WINNER_GATE"])
    trusted_values = np.asarray(composite_arrays["CR_TRUSTED"])
    selected = np.zeros(source_values.shape, dtype=bool)
    if target_sources:
        selected = np.isin(source_values, list(target_sources))
    selected &= np.isfinite(trusted_values)
    selected &= trusted_values >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_dbz"]
    selected &= trusted_values <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_dbz"]

    stats = _empty_stats()
    for row, column in zip(*np.nonzero(selected), strict=True):
        source_index = int(source_values[row, column])
        sweep = int(sources[source_index]["sweep"])
        evidence = evidence_by_sweep.get(f"sweep_{sweep:03d}")
        if evidence is None:
            continue
        ray = int(ray_values[row, column])
        gate = int(gate_values[row, column])
        ranges = np.asarray(current_root[f"sweep_{sweep:03d}"]["range"][:], float)
        if not (0 <= gate < ranges.size) or not (
            0 <= ray < evidence["RTI_OBJECT_MASK"].shape[0]
        ):
            continue
        range_m = float(ranges[gate])
        bucket = _range_bucket(range_m)
        if bucket not in stats:
            continue
        item = stats[bucket]
        item["residual_winner_pixels"] += 1
        item["candidate_covered"] += int(evidence["RTI_CANDIDATE_MASK"][ray, gate] == 1)
        item["object_covered"] += int(evidence["RTI_OBJECT_MASK"][ray, gate] == 1)
        item["blob_covered"] += int(evidence["RTI_BLOB_MASK"][ray, gate] == 1)
        item["isolated_covered"] += int(evidence["RTI_ISOLATED_MASK"][ray, gate] == 1)
    for item in stats.values():
        item["trusted_after_simulated"] = (
            item["residual_winner_pixels"] - item["object_covered"]
        )
    return stats


def _quantiles(values: list[float]) -> dict[str, float | None]:
    array = np.asarray(values, float)
    array = array[np.isfinite(array)]
    if not array.size:
        return {"p10": None, "p25": None, "p50": None, "p75": None, "p90": None}
    return {
        key: round(float(value), 3)
        for key, value in zip(
            ("p10", "p25", "p50", "p75", "p90"),
            np.quantile(array, (0.10, 0.25, 0.50, 0.75, 0.90)),
            strict=True,
        )
    }


def _winner_features(
    composite_arrays: Mapping[str, np.ndarray],
    sources: list[Mapping],
    current_root: Mapping,
    radar_id: str,
    evidence_by_sweep: Mapping[str, Mapping],
) -> dict:
    target_sources = {
        index
        for index, source in enumerate(sources)
        if str(source.get("radar_id", "")).lower() == radar_id.lower()
    }
    source_values = np.asarray(composite_arrays["WINNER_SOURCE"])
    ray_values = np.asarray(composite_arrays["WINNER_RAY"])
    gate_values = np.asarray(composite_arrays["WINNER_GATE"])
    trusted_values = np.asarray(composite_arrays["CR_TRUSTED"])
    selected = np.zeros(source_values.shape, dtype=bool)
    if target_sources:
        selected = np.isin(source_values, list(target_sources))
    selected &= np.isfinite(trusted_values)
    selected &= trusted_values >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_dbz"]
    selected &= trusted_values <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_dbz"]
    features: dict[str, list[float]] = {
        "texture_score": [],
        "edge_score": [],
        "isolation_score": [],
        "polar_score": [],
        "nonmet_fraction": [],
        "object_size": [],
        "object_area_m2": [],
        "object_kind": [],
    }
    for row, column in zip(*np.nonzero(selected), strict=True):
        source_index = int(source_values[row, column])
        sweep = int(sources[source_index]["sweep"])
        evidence = evidence_by_sweep.get(f"sweep_{sweep:03d}")
        if evidence is None:
            continue
        ray = int(ray_values[row, column])
        gate = int(gate_values[row, column])
        for name, key in (
            ("texture_score", "RTI_TEXTURE_SCORE"),
            ("edge_score", "RTI_EDGE_SCORE"),
            ("isolation_score", "RTI_ISOLATION_SCORE"),
            ("polar_score", "RTI_POLAR_SCORE"),
            ("object_size", "RTI_OBJECT_SIZE"),
            ("object_area_m2", "RTI_OBJECT_AREA_M2"),
            ("object_kind", "RTI_OBJECT_KIND"),
        ):
            value = np.asarray(evidence[key])[ray, gate]
            if np.isfinite(value):
                features[name].append(float(value))
        current = _sweep_arrays(current_root[f"sweep_{sweep:03d}"])
        if "CF_NEIGHBOUR_FRACTION" in current:
            value = np.asarray(current["CF_NEIGHBOUR_FRACTION"])[ray, gate]
            if np.isfinite(value):
                features["nonmet_fraction"].append(float(value))
    return {name: _quantiles(values) for name, values in features.items()} | {
        "winner_pixels": int(selected.sum())
    }


def _vertical_context(
    current: Mapping[str, np.ndarray],
    donors: tuple[Mapping[str, np.ndarray], ...],
) -> tuple[np.ndarray, np.ndarray]:
    shape = np.asarray(current["DBZH_RAW"]).shape
    available = np.zeros(shape, dtype=bool)
    support = np.zeros(shape, dtype=bool)
    target_az = np.asarray(current["azimuth"], float)[:, None]
    target_el = np.asarray(current["elevation"], float)[:, None]
    target_range = np.asarray(current["range"], float)[None, :]
    target_height = height(target_range, target_el)
    target_ground = ground(target_range, target_el)
    ranked = sorted(
        donors,
        key=lambda donor: abs(
            float(np.median(donor["elevation"]))
            - float(np.median(current["elevation"]))
        ),
    )
    for donor in ranked[: RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_vertical_donors"]]:
        elevation_delta = abs(
            float(np.median(donor["elevation"]))
            - float(np.median(current["elevation"]))
        )
        if elevation_delta <= 0.2:
            continue
        ray, gate, footprint = polar_targets(
            np.asarray(donor["azimuth"], float),
            np.asarray(donor["range"], float),
            target_ground,
            target_az,
        )
        donor_z = np.asarray(donor["DBZH_RAW"], float)[ray, gate]
        donor_shape = np.asarray(donor["DBZH_RAW"]).shape
        donor_valid = _binary_array(donor.get("VALID_MASK"), donor_shape)[ray, gate]
        donor_height = height(
            np.asarray(donor["range"], float)[gate],
            np.asarray(donor["elevation"], float)[ray],
        )
        delta = np.abs(donor_height - target_height)
        measured = (
            footprint
            & donor_valid
            & np.isfinite(donor_z)
            & (delta >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_vertical_delta_m"])
            & (delta <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_vertical_delta_m"])
        )
        available |= measured
        support |= measured & (
            donor_z >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_dbz"]
        )
    return available, support


def _binary_array(value, shape: tuple[int, int]) -> np.ndarray:
    if value is None:
        return np.zeros(shape, dtype=bool)
    array = np.asarray(value)
    if array.shape != shape or not np.isin(array, (0, 1)).all():
        raise ValueError("invalid vertical context validity mask")
    return array.astype(bool, copy=False)


def replay(current_uri: str, diagnostic_uri: str | None) -> dict:
    client = minio_client_from_environment()
    reader = ArtifactObjectReader(client)
    started = time.perf_counter()
    load_started = time.perf_counter()
    current_root = _open_group(reader.load(current_uri))
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
    all_sweeps = {
        f"sweep_{number:03d}": _sweep_arrays(current_root[f"sweep_{number:03d}"])
        for number in np.asarray(current_root["sweep_number"][:], int)
    }
    evaluate_started = time.perf_counter()
    for key, current in all_sweeps.items():
        if not {"azimuth", "range", "DBZH_RAW", "RHOHV_RAW", "SNR_RAW"}.issubset(
            current
        ):
            summaries.append(
                {"sweep": key, "status": "SKIPPED_MISSING_CURRENT_MOMENTS"}
            )
            continue
        before = array_digest(current)
        donors = tuple(item for name, item in all_sweeps.items() if name != key)
        vertical_available, vertical_support = _vertical_context(current, donors)
        evidence = evaluate_residual_texture_isolation(
            current,
            vertical_support=vertical_support,
            vertical_available=vertical_available,
        )
        after = array_digest(current)
        if before != after:
            raise RuntimeError("audit replay mutated current input")
        evidence_by_sweep[key] = evidence.arrays
        summaries.append(evidence.summary)
    evaluate_s = time.perf_counter() - evaluate_started
    coverage = (
        _winner_coverage(
            composite_arrays,
            composite_sources,
            current_root,
            radar_id,
            evidence_by_sweep,
        )
        if composite_arrays is not None
        else None
    )
    totals = {
        name: int(sum(item.get(name, 0) for item in summaries))
        for name in ("candidate_gates", "object_count", "blob_gates", "isolated_gates")
    }
    return {
        "status": "EVALUATED",
        "mode": "audit-only",
        "radar_id": radar_id,
        "policy": RESIDUAL_TEXTURE_ISOLATION_POLICY,
        "timing": {
            "load_s": round(load_s, 3),
            "evaluate_s": round(evaluate_s, 3),
            "total_s": round(time.perf_counter() - started, 3),
        },
        "peak_rss_mib": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
        ),
        "sweeps": summaries,
        "totals": totals,
        "cr_residual_winner_coverage": coverage,
        "cr_residual_winner_features": (
            _winner_features(
                composite_arrays,
                composite_sources,
                current_root,
                radar_id,
                evidence_by_sweep,
            )
            if composite_arrays is not None
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current_uri")
    parser.add_argument("--diagnostic-uri")
    args = parser.parse_args()
    print(json.dumps(replay(args.current_uri, args.diagnostic_uri), ensure_ascii=False))


if __name__ == "__main__":
    main()
