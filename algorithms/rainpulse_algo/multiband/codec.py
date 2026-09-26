# ruff: noqa: E501, I001
"""Bounded, deterministic NPZ objects: no per-gate filesystem objects or pickle."""
from __future__ import annotations

from rainpulse_algo.performance import (timed as _perf_timed)

import hashlib
import io
import json
from pathlib import PurePosixPath
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

import numpy as np

from .model import Sweep, Volume, json_bytes

MAX_ARRAYS = 2048


@_perf_timed("io.npz_encoding")
def encode_arrays(arrays: dict[str, np.ndarray]) -> bytes:
    out = io.BytesIO()
    if len(arrays) > MAX_ARRAYS:
        raise ValueError("too many arrays")
    with ZipFile(out, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
        for name, arr in sorted(arrays.items()):
            if not name or PurePosixPath(name).name != name or not all(c.isalnum() or c == "_" for c in name):
                raise ValueError("invalid array key")
            arr = np.asarray(arr)
            if arr.dtype.kind not in "bui f".replace(" ", ""):
                raise ValueError("only numeric and binary arrays are supported")
            stream = io.BytesIO()
            np.lib.format.write_array(stream, arr, allow_pickle=False)
            entry = ZipInfo(name + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, stream.getvalue(), compresslevel=3)
    return out.getvalue()


@_perf_timed("io.npz_decoding")
def decode_arrays(raw: bytes, *, maximum_bytes: int) -> dict[str, np.ndarray]:
    if len(raw) > maximum_bytes:
        raise ValueError("compressed array object exceeds budget")
    shapes = {}
    with ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if not 1 <= len(entries) <= MAX_ARRAYS or len({i.filename for i in entries}) != len(entries):
            raise ValueError("duplicate/too many NPZ entries")
        if sum(i.file_size for i in entries) > maximum_bytes:
            raise ValueError("decoded array object exceeds budget")
        total = 0
        for entry in entries:
            if PurePosixPath(entry.filename).name != entry.filename or not entry.filename.endswith(".npy"):
                raise ValueError("invalid NPZ member")
            with archive.open(entry) as f:
                version = np.lib.format.read_magic(f)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(f)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(f)
                else:
                    raise ValueError("unsupported array format")
                if dtype.kind not in "buif" or len(shape) > 4 or any(type(v) is not int or v < 0 for v in shape):
                    raise ValueError("object/string dtype or invalid dimensions")
                count = 1
                for n in shape:
                    count *= n
                    if count > maximum_bytes:
                        raise ValueError("array shape exceeds budget")
                total += count * dtype.itemsize
                if total > maximum_bytes:
                    raise ValueError("array header exceeds decoded budget")
                shapes[entry.filename[:-4]] = shape
        with np.load(io.BytesIO(raw), allow_pickle=False) as data:
            return {key: data[key] for key in shapes}


def encode_volume(volume: Volume) -> dict[str, bytes]:
    arrays = {}
    sweeps = []
    for s in volume.sweeps:
        prefix = f"s{s.number}_"
        values = {"azimuth_deg": s.azimuth_deg, "range_m": s.range_m,
                  "elevation_deg": s.elevation_deg, "ray_time_epoch": s.ray_time_epoch, **s.fields}
        for key, value in values.items():
            arrays[prefix+key] = value
        sweeps.append({"number": s.number, "fields": sorted(s.fields)})
    raw = encode_arrays(arrays)
    meta = {"contract": "rainpulse.multiband.native-v1", "metadata": volume.metadata,
            "sweeps": sweeps, "arrays_sha256": hashlib.sha256(raw).hexdigest()}
    return {"volume.json": json_bytes(meta), "arrays.npz": raw}


def decode_volume(objects: dict[str, bytes], *, maximum_bytes: int, asset_sha256: str) -> Volume:
    if len(objects["volume.json"]) > 1024**2:
        raise ValueError("native metadata too large")
    m = json.loads(objects["volume.json"])
    if m.get("contract") != "rainpulse.multiband.native-v1" or hashlib.sha256(objects["arrays.npz"]).hexdigest() != m["arrays_sha256"]:
        raise ValueError("native volume contract/content differs")
    arrays = decode_arrays(objects["arrays.npz"], maximum_bytes=maximum_bytes)
    sweeps = []
    for item in m["sweeps"]:
        n = item["number"]
        fields = {key: arrays[f"s{n}_{key}"] for key in item["fields"]}
        sweeps.append(Sweep(n, *(arrays[f"s{n}_{key}"] for key in ("azimuth_deg", "range_m", "elevation_deg", "ray_time_epoch")), fields))
    metadata = dict(m["metadata"])
    # Content identity comes from the verified object reader, not an embedded claim.
    metadata["asset_sha256"] = asset_sha256
    return Volume(metadata, sweeps)
