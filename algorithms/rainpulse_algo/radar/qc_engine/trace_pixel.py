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
        if array.shape == p_source_shape(g):
            v = array[ray, gate].item()
            result["fields"][name] = None if isinstance(v, float) and not np.isfinite(v) else v
    result["source_azimuth_deg"] = float(g["azimuth"][ray])
    result["source_range_m"] = float(g["range"][gate])
    result["source_elevation_deg"] = float(g["elevation"][ray])
    result["source_ray_time"] = None
    if "ray_time" in g:
        timestamp = np.asarray(g["ray_time"][ray])
        result["source_ray_time_dtype"] = timestamp.dtype.str
        result["source_ray_time_attributes"] = dict(g["ray_time"].attrs)
        if timestamp.dtype.kind == "M":
            result["source_ray_time"] = (
                None
                if np.isnat(timestamp)
                else np.datetime_as_string(
                    timestamp.astype("datetime64[ns]"), unit="ns", timezone="UTC"
                )
            )
        elif timestamp.dtype.kind in "iuf":
            result["source_ray_time"] = timestamp.item() if np.isfinite(timestamp) else None
        else:
            raise ValueError("unsupported source ray time encoding")
    result["sampling_verified_against_png"] = False
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
    p.add_argument("--size", type=int, default=640)
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
