#!/usr/bin/env python3
"""Read-only object audit on cached native QC; no products are published."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import zarr
from zarr.storage import MemoryStore
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep, FIELD_NAMES
from rainpulse_algo.radar.qc_engine.object_morphology import object_morphology


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", required=True)
    p.add_argument("--boundaries", action="store_true")
    a = p.parse_args()
    source = zarr.open_group("/tmp/morph-" + a.case + ".zarr", mode="r")
    g = source["sweep_000"]
    root = zarr.group(store=MemoryStore())
    root.attrs.update(dict(source.attrs))
    cut = root.create_group("sweep_000")
    cut.attrs.update(dict(g.attrs))
    for k in ["range", "azimuth", "elevation", "ray_time"]:
        cut.create_dataset(k, data=g[k][:])
    for k in FIELD_NAMES:
        if k + "_RAW" in g:
            cut.create_dataset(k, data=g[k + "_RAW"][:])
    profile = load_qc_profile(
        "/tmp/qc732/configs/qc/fujian-qc-distance-polar-v1.yaml",
        "/opt/rainpulse/configs/qc/flag-definitions-v2.yaml",
    )
    n = adapt_sweep(root, "sweep_000", profile)
    residual = g["BWS_RANGE_RESIDUAL_DB"][:][n.original_indices]
    start = time.monotonic()
    labels, records = object_morphology(n, source_residual=residual)
    elapsed = time.monotonic() - start
    eligible = (
        (g["QPE_ELIGIBLE_MASK"][:] == 1)
        & ~np.load("/tmp/qc732/case-" + a.case + ".json.mask.npy")
    )[n.original_indices]
    high = eligible & (n.fields["DBZH"] >= 45) & (n.ranges[None, :] >= 80000)
    counts = {
        k: np.bincount(v[high], minlength=int(v.max()) + 1) for k, v in labels.items()
    }
    for record in records:
        threshold = str(int(record["threshold_dbz"]))
        record["remaining_high_gates"] = int(counts[threshold][record["object_id"]])
    if a.boundaries:
        from rainpulse_algo.radar.qc_engine.source_boundary import source_boundary

        import yaml
        import hashlib

        station = "z9591" if a.case in {"1", "2"} else "z9598"
        station_path = Path("/tmp/" + station + ".yaml")
        station_bytes = station_path.read_bytes()
        station_config = yaml.safe_load(station_bytes)
        selected = sorted(
            (x for x in records if x["threshold_dbz"] == 45),
            key=lambda x: x["remaining_high_gates"],
            reverse=True,
        )[:8]
        results = []
        for record in selected:
            result = source_boundary(
                n,
                labels["45"],
                record["object_id"],
                site_altitude_m=station_config["site"]["antenna_altitude_m"],
            )
            result["station_config_sha256"] = hashlib.sha256(station_bytes).hexdigest()
            results.append({"object": record, "geometry": result})
        Path("/tmp/qc732/boundaries-" + a.case + ".json").write_text(
            json.dumps(results, indent=2)
        )
        from rainpulse_algo.radar.qc_engine.broad_source import infer_broad_source
        from rainpulse_algo.radar.qc_engine.object_gate_review import (
            review_object_gates,
            GateReason,
        )
        from rainpulse_algo.radar.qc_engine.object_consensus.engine import (
            Reason as OCReason,
        )

        cross = g["V7_CROSS_RADAR_SUPPORT_SCORE"][:][n.original_indices]
        vertical = g["V7_VERTICAL_SUPPORT_SCORE"][:][n.original_indices]
        weather = np.fmax(cross, vertical) >= profile.context.strong_support
        oc = g["OC1_REASON"][:][n.original_indices]
        conflicts = (
            oc
            & int(
                OCReason.LOCAL_POWER_ENHANCEMENT
                | OCReason.EXTERNAL_WEATHER_SUPPORT
                | OCReason.GEOMETRY_UNAVAILABLE
            )
        ) != 0
        bws, diag = infer_broad_source(
            n, profile.generalization.broad_source, weather=weather, conflicts=conflicts
        )
        paired = {
            x["object"]["object_id"]
            for x in results
            if x["geometry"]["status"] == "paired_radial_geometry"
        }
        reason, proposal = review_object_gates(
            labels["45"], paired, bws, n.fields["DBZH"]
        )
        counts = {
            bit.name: int((high & ((reason & int(bit)) != 0)).sum())
            for bit in GateReason
        }
        # Overlapping reasons, not exclusive causal percentages.
        report = {
            "high_remaining": int(high.sum()),
            "reason_counts_overlap": counts,
            "additional_eligible_proposals": int((eligible & proposal).sum()),
            "paired_object_ids": sorted(paired),
            "audit_only": True,
        }
        within = high & np.isin(labels["45"], sorted(paired))
        fold_counts = np.bincount(bws["BWS_FOLD_ID"][high].astype(int))
        statuses = {}
        for fold in diag.get("folds", []):
            fid = fold["fold_id"]
            if fid < len(fold_counts) and fold_counts[fid]:
                status = fold.get("distance_polar", {}).get("status", "unavailable")
                statuses[status] = statuses.get(status, 0) + int(fold_counts[fid])
        report["high_distance_reference_status"] = statuses
        report["high_external_conflict"] = int((high & conflicts).sum())
        report["within_paired_object"] = {
            bit.name: int((within & ((reason & int(bit)) != 0)).sum())
            for bit in GateReason
        }
        Path("/tmp/qc732/gate-review-" + a.case + ".json").write_text(
            json.dumps(report, indent=2)
        )
    path = Path("/tmp/qc732/objects-" + a.case)
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "seconds": elapsed,
                "objects": records,
                "high_gates": int(high.sum()),
                "scope": "raw_objects_audit_only",
            },
            indent=2,
        )
    )
    np.savez_compressed(str(path) + ".npz", **labels)
    ranked = sorted(records, key=lambda x: x["remaining_high_gates"], reverse=True)
    print(
        json.dumps(
            {
                "case": a.case,
                "seconds": elapsed,
                "count": len(records),
                "top": ranked[:6],
            }
        )
    )


if __name__ == "__main__":
    main()
