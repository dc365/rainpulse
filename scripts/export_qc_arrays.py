#!/usr/bin/env python3
"""Export a stored QC volume as one compressed npz per sweep.

The stored QC volume is published in a packed container whose evidence JSON
carries object-store paths and site ids.  Unpacking it to loose objects costs
one object file per zarr chunk, which the packaging host cannot afford, so the
arrays are exported sweep by sweep instead and the identities are dropped.

Usage (inside the worker image):
  python export_qc_arrays.py <volume-uri> <output-dir> <SITE> <CASE>
"""
from __future__ import annotations

import json
import os
import re
import sys

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)

COORDINATES = ("azimuth", "elevation", "range", "ray_time")
SCALAR = (str, int, float, bool, type(None))
SITE_IDS = ("z9591", "z9593", "z9598", "z9599")
# Attribute keys that name a station, a source file or an object-store location.
# scripts/package_qc_case.py applies the same rule to the volumes it stages, so
# both bundles expose the same attribute surface.  radar_id keeps its key and is
# rewritten to the site alias because the QC review tool reads it.
IDENTITY_KEYS = frozenset({
    "asset_id", "filename", "filename_time", "source_filename", "field_mapping_version",
    "radar_config_version", "raw_asset_id", "source_path", "input_path", "display_name",
})
IDENTITY_KEY_PATTERNS = (
    re.compile(r"(?i)^(site|station|radar|antenna)_"),
    re.compile(r"(?i)^(.*_)?(latitude|longitude)(_deg)?$"),
    re.compile(r"(?i)^.*(uri|path|filename|asset).*$"),
    re.compile(r"(?i)^(display_)?name$"),
)
IDENTITY_EXEMPT = frozenset({"radar_id"})


def identity_key(key: str) -> bool:
    """True when an attribute key carries station or deployment identity."""
    if key in IDENTITY_EXEMPT:
        return False
    return key in IDENTITY_KEYS or any(pattern.match(key) for pattern in IDENTITY_KEY_PATTERNS)


def scrub_text(text: str, site: str) -> str:
    """Drop site, storage and deployment identity from a provenance value."""
    for radar in SITE_IDS:
        text = re.sub(radar, site, text, flags=re.IGNORECASE)
    if any(token in text for token in ("s3://", "/home/yons", "rainpulse")):
        return "<redacted>"
    return text


def main() -> None:
    uri, out_dir, site, case = sys.argv[1:5]
    reader = ArtifactObjectReader(minio_client_from_environment())
    store = MemoryStore()
    for key, value in reader.load(uri).items():
        store[key] = value
    root = zarr.open_group(store=store, mode="r")
    os.makedirs(out_dir, exist_ok=True)
    meta = {"site": site, "case": case, "root_attrs": {}, "sweeps": {}}
    for key in sorted(root.attrs):
        value = root.attrs[key]
        if identity_key(key):
            continue
        if isinstance(value, SCALAR):
            if key == "radar_id":
                meta["root_attrs"][key] = site
            elif isinstance(value, str):
                meta["root_attrs"][key] = scrub_text(value, site)
            else:
                meta["root_attrs"][key] = value
    for name in sorted(k for k in root.group_keys() if k.startswith("sweep")):
        group = root[name]
        arrays = {}
        for field in sorted(group.array_keys()):
            value = np.asarray(group[field][:])
            if value.ndim <= 2 and value.dtype != object:
                arrays[field] = value
        for coordinate in COORDINATES:
            if coordinate in group and coordinate not in arrays:
                arrays[coordinate] = np.asarray(group[coordinate][:])
        np.savez_compressed(os.path.join(out_dir, f"{name}.npz"), **arrays)
        meta["sweeps"][name] = {
            field: {"dtype": str(value.dtype), "shape": list(value.shape)}
            for field, value in arrays.items()
        }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=1)
    print(json.dumps({"case": case, "site": site, "sweeps": len(meta["sweeps"])}))


if __name__ == "__main__":
    main()
