#!/usr/bin/env python3
"""Incremental generalization-stage replay on saved QC baseline; never publishes."""

import argparse
import json
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
    from rainpulse_algo.radar.qc_engine.generalization import broad_source_review
    from rainpulse_algo.radar.qc_engine.decision import Decision
    from types import SimpleNamespace
    from PIL import Image, ImageDraw
    from rainpulse_algo.diagnostics.polar_sampling import project_rgba
    from rainpulse_algo.diagnostics.renderer import REFLECTIVITY_STOPS, _scalar_rgba

    cross = g["V7_CROSS_RADAR_SUPPORT_SCORE"][:][n.original_indices]
    vertical = g["V7_VERTICAL_SUPPORT_SCORE"][:][n.original_indices]
    weather = np.fmax(cross, vertical)
    ev = infer(raw_from_native(n, profile.geometry.phase_period_deg), Config())
    reason_matches = bool(
        np.array_equal(ev.arrays["reason"], g["OC1_REASON"][:][n.original_indices])
    )
    if not reason_matches:
        raise ValueError("OC1 raw replay differs from saved baseline")
    baseline = Decision(
        {
            k: g[k][:][n.original_indices]
            for k in g.array_keys()
            if g[k].shape == n.shape
        },
        g["QC_FLAGS"][:][n.original_indices],
        g["QUALITY_INDEX"][:][n.original_indices],
    )
    outcome = SimpleNamespace(
        summary={"status": "saved_baseline_incremental_replay"},
        proposed_quarantine=np.zeros(n.shape, bool),
    )
    import time

    start = time.monotonic()
    decision, summary = broad_source_review(
        n, baseline, profile, ev, outcome, weather_support=weather
    )
    arrays = decision.arrays
    summary["oc1_reproduced"] = reason_matches
    eligible = g["QPE_ELIGIBLE_MASK"][:] == 1
    mask = n.restore(arrays["P2_ADDED_QUARANTINE_MASK"]) == 1
    high = eligible & (g["DBZH_RAW"][:] >= 45) & (g["range"][:][None, :] >= 80000)
    summary.update(
        seconds=time.monotonic() - start,
        old_eligible=int(eligible.sum()),
        new_isolated=int((mask & eligible).sum()),
        high_before=int(high.sum()),
        high_remaining=int((high & ~mask).sum()),
        scope="incremental_generalization_on_saved_lowest_cut_not_published",
        baseline_version=source.attrs.get("qc_pipeline_version", "unknown"),
        candidate_version=profile.pipeline_version,
    )
    Path(args.output).write_text(json.dumps(summary, indent=2) + "\n")
    np.save(args.output + ".mask.npy", mask)
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k
                not in ("folds", "broad_source", "measurement_routes", "range_objects")
            }
        ),
        flush=True,
    )
    canvas = Image.new("RGBA", (1920, 680), "white")
    draw = ImageDraw.Draw(canvas)
    z = g["DBZH_RAW"][:]
    for i, (label, m) in enumerate(
        [
            ("Raw", np.isfinite(z)),
            (
                str(source.attrs.get("qc_pipeline_version", "baseline")) + " eligible",
                eligible,
            ),
            (
                profile.pipeline_version + " incremental - not published",
                eligible & ~mask,
            ),
        ]
    ):
        im = Image.fromarray(
            project_rgba(
                _scalar_rgba(z, m, REFLECTIVITY_STOPS),
                g["azimuth"][:],
                g["range"][:],
                640,
            )
        )
        canvas.alpha_composite(im, (640 * i, 40))
        draw.text((640 * i + 10, 12), label, fill="black")
    canvas.convert("RGB").save(args.output + ".png")


if __name__ == "__main__":
    main()
