#!/usr/bin/env python3
"""Read-only audit of library evidence on a cached lowest-cut QC result."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import wradlib
import zarr


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qc-store", required=True)
    p.add_argument("--candidate-mask", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    g = zarr.open_group(a.qc_store, mode="r")["sweep_000"]
    z = g["DBZH_RAW"][:]
    r = g["range"][:]
    az = g["azimuth"][:]
    eligible = (g["QPE_ELIGIBLE_MASK"][:] == 1) & ~np.load(a.candidate_mask)
    high = eligible & (z >= 45) & (r[None, :] >= 80000)
    result = {
        "scope": "read_only_unlabelled_diagnostic",
        "remaining_high": int(high.sum()),
        "remaining_eligible": int(eligible.sum()),
        "wradlib_version": wradlib.__version__,
        "evidence": {},
    }

    def stats(mask):
        return {
            "high_overlap": int((mask & high).sum()),
            "all_eligible_overlap": int((mask & eligible).sum()),
        }

    for k in [
        "OS_GABELLA_CANDIDATE_MASK",
        "OS_SMALL_OBJECT_CANDIDATE_MASK",
        "OS_PHIDP_TEXTURE_CANDIDATE_MASK",
        "OS_ZDR_TEXTURE_CANDIDATE_MASK",
        "OS_RHOHV_TEXTURE_CANDIDATE_MASK",
    ]:
        if k in g:
            result["evidence"][k] = stats(g[k][:] == 1)
    spacing = float(np.median(np.diff(r)))
    da = np.mod(np.roll(az, -1) - az, 360)
    regular = (
        np.allclose(np.diff(r), spacing)
        and np.isclose(r[0], spacing / 2)
        and np.allclose(da, 360 / len(az), atol=0.05)
    )
    result["window_geometry"] = {
        "regular_full_ppi": bool(regular),
        "first_range_m": float(r[0]),
        "spacing_m": spacing,
    }
    work = z
    reverse = None
    if (
        not regular
        and np.allclose(np.diff(r), spacing)
        and np.isclose(r[0], spacing / 2)
    ):
        # Diagnostic-only nearest 1-degree assignment. Collisions/large offsets
        # abstain; no averaging, interpolation or filling unmeasured cells.
        bins = np.rint(az).astype(int) % 360
        error = abs((az - bins + 180) % 360 - 180)
        count = np.bincount(bins, minlength=360)
        keep = (count[bins] == 1) & (error <= 0.45)
        work = np.full((360, z.shape[1]), np.nan)
        work[bins[keep]] = z[keep]
        reverse = (bins, keep)
        result["window_geometry"]["experimental_mapping"] = {
            "resolution_deg": 1,
            "accepted_rays": int(keep.sum()),
            "excluded_rays": int((~keep).sum()),
            "not_for_disposition": True,
        }
    if regular or reverse is not None:
        for size in (1500, 5000):
            start = time.monotonic()
            try:
                score = wradlib.classify.filter_window_distance(
                    work, spacing, fsize=size, tr1=7
                )
                if reverse is not None:
                    bins, keep = reverse
                    restored = np.full(z.shape, np.nan)
                    restored[keep] = score[bins[keep]]
                    score = restored
                result["evidence"]["window_distance_" + str(size)] = {
                    "seconds": time.monotonic() - start,
                    "thresholds_exploratory": {
                        str(t): stats(np.isfinite(score) & (score < t))
                        for t in (0.1, 0.25, 0.5)
                    },
                    "high_quantiles": np.nanpercentile(
                        score[high], [5, 50, 95]
                    ).tolist()
                    if high.any()
                    else None,
                }
            except (ValueError, IndexError) as exc:
                result["evidence"]["window_distance_" + str(size)] = {
                    "unavailable": str(exc)
                }
    Path(a.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
