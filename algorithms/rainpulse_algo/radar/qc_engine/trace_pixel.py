"""Trace a native PPI pixel to its exact source gate without network or publication."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import zarr

from ...diagnostics.polar_sampling import SAMPLING_VERSION, polar_pixels


def trace(root, *, sweep, row, column, size):
    if root.attrs.get("contract_name") != "rainpulse.qc-radar-volume":
        raise ValueError("trace requires a QC radar volume, not a raw or rendered product")
    if sweep not in root or not 0 <= row < size or not 0 <= column < size:
        raise ValueError("unknown sweep or pixel outside image")
    g = root[sweep]
    p = polar_pixels(g["azimuth"][:], g["range"][:], size)
    ray, gate = int(p.ray[row, column]), int(p.gate[row, column])
    result = dict(
        schema_version="rainpulse.qc-pixel-trace.v1",
        sampling=SAMPLING_VERSION,
        sweep=sweep,
        pixel=dict(row=row, column=column, size=size),
        measured_footprint=bool(p.available[row, column]),
        ray_index=ray,
        gate_index=gate,
        profile=root.attrs.get("qc_profile"),
        pipeline=root.attrs.get("qc_pipeline_version"),
        input_asset_ids=root.attrs.get("input_asset_ids"),
        fields={},
    )
    if ray < 0:
        result["reason"] = "outside_native_observation_footprint"
        return result
    for name in sorted(g.array_keys()):
        array = g[name]
        if array.shape == p_source_shape(g) and (
            name
            in {
                "DBZH_RAW",
                "DBZH_QC",
                "DBZH_USABLE",
                "QC_ACTION",
                "QC_FLAGS",
                "QUALITY_INDEX",
                "QPE_ELIGIBLE_MASK",
                "RFI_QUARANTINE_MASK",
            }
            or name.startswith(("V5_", "V6_", "PAPER_", "RFI_"))
        ):
            v = array[ray, gate].item()
            result["fields"][name] = None if isinstance(v, float) and not np.isfinite(v) else v
    result["source_azimuth_deg"] = float(g["azimuth"][ray])
    result["source_range_m"] = float(g["range"][gate])
    result["eligible"] = bool(g["QPE_ELIGIBLE_MASK"][ray, gate])
    result["reason"] = "source_gate_traced; compare PNG sampling/version and configured mask"
    return result


def p_source_shape(group):
    return (len(group["azimuth"]), len(group["range"]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qc-zarr", type=Path, required=True)
    p.add_argument("--sweep", default="sweep_000")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--column", type=int, required=True)
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if not a.qc_zarr.is_dir() or a.output.exists():
        raise ValueError("local QC directory required; output must not exist")
    root = zarr.open_group(str(a.qc_zarr), mode="r")
    data = trace(root, sweep=a.sweep, row=a.row, column=a.column, size=a.size)
    data["root_metadata_sha256"] = hashlib.sha256((a.qc_zarr / ".zattrs").read_bytes()).hexdigest()
    with a.output.open("x") as file:
        json.dump(data, file, ensure_ascii=False, allow_nan=False, indent=2)


if __name__ == "__main__":
    main()
