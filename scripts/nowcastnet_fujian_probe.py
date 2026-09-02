#!/usr/bin/env python3
"""Probe a fixed Fujian NowcastNet ROI without running the GPU model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from rainpulse_algo.nowcast.nowcastnet_shadow import (
    load_nowcastnet_shadow_profile,
    probe_fixed_roi,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check whether every frame completely covers the frozen Fujian ROI"
    )
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument(
        "--valid-mask",
        type=Path,
        required=True,
        help="NumPy .npy file with a binary time x latitude x longitude mask",
    )
    args = parser.parse_args()

    profile = load_nowcastnet_shadow_profile(args.profile)
    mask = np.load(args.valid_mask, allow_pickle=False)
    report = {
        "schema_version": "1.0",
        "profile_version": profile.profile_version,
        "roi": {
            "y_start": profile.roi.y_start,
            "x_start": profile.roi.x_start,
            "height": profile.roi.height,
            "width": profile.roi.width,
        },
        **probe_fixed_roi(mask, roi=profile.roi),
        "inference_enabled": profile.activation.inference_enabled,
        "spatial_shape_validated": profile.activation.spatial_shape_validated,
        "operational_eligible": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
