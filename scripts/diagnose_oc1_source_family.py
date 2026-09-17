#!/usr/bin/env python3
"""Replay only OC1 on one saved raw-moment cut; no publication or threshold edits."""

import argparse
import json
from collections import Counter
from pathlib import Path
import numpy as np
import zarr
from zarr.storage import MemoryStore
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep, FIELD_NAMES
from rainpulse_algo.radar.qc_engine.object_consensus.adapter import raw_from_native
from rainpulse_algo.radar.qc_engine.object_consensus.config import Config
from rainpulse_algo.radar.qc_engine.object_consensus.engine import infer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qc-store", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--flags", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = zarr.open_group(args.qc_store, mode="r")
    g = source["sweep_000"]
    root = zarr.group(store=MemoryStore())
    root.attrs.update(dict(source.attrs))
    cut = root.create_group("sweep_000")
    cut.attrs.update(dict(g.attrs))
    for key in ["azimuth", "elevation", "range", "ray_time"]:
        cut.create_dataset(key, data=g[key][:])
    for key in FIELD_NAMES:
        if key + "_RAW" in g:
            cut.create_dataset(key, data=g[key + "_RAW"][:])
    profile = load_qc_profile(args.profile, args.flags)
    n = adapt_sweep(root, "sweep_000", profile)
    cfg = Config()
    ev = infer(raw_from_native(n, profile.geometry.phase_period_deg), cfg)
    reason_exact = np.array_equal(n.restore(ev.arrays["reason"]), g["OC1_REASON"][:])
    if not reason_exact:
        raise ValueError(
            "OC1 saved reason did not reproduce; do not attribute changed inference"
        )
    counts = Counter()
    target_fold = g["OC1_FOLD_ID"][:][n.original_indices]
    region = (
        (g["QPE_ELIGIBLE_MASK"][:][n.original_indices] == 1)
        & (n.fields["DBZH"] >= 45)
        & (n.ranges[None, :] >= 80000)
    )
    values = {
        k: []
        for k in [
            "snr_median_db",
            "coherent_phase_p90_deg",
            "coherent_zdr_p90_db",
            "coherent_high_rho_fraction",
        ]
    }
    for f in ev.folds:
        if f["status"] != "source_family_unresolved":
            continue
        gates = int(np.sum(region[f["ray"]] & (target_fold[f["ray"]] == f["fold_id"])))
        if not gates:
            continue
        counts["unresolved_gates"] += gates
        for key in values:
            if key in f:
                values[key].append(f[key])
        if f.get("snr_median_db", -np.inf) < cfg.coherent_snr_db:
            counts["reference_snr_below_coherent_minimum"] += gates
        if f.get("coherent_phase_p90_deg", 0) > cfg.coherent_phase_p90_deg:
            counts["reference_phase_dispersion"] += gates
        if f.get("coherent_zdr_p90_db", 0) > cfg.coherent_zdr_p90_db:
            counts["reference_zdr_dispersion"] += gates
        if f.get("coherent_high_rho_fraction", 1) < cfg.coherent_fraction:
            counts["reference_rho_fraction"] += gates
    result = {
        "reason_reproduced_exactly": reason_exact,
        "counts_overlapping": dict(counts),
        "fold_quantiles_10_50_90": {
            k: np.percentile(v, [10, 50, 90]).tolist() for k, v in values.items() if v
        },
        "scope": "lowest_cut_OC1_only_not_full_worker_or_weather_truth",
    }
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
