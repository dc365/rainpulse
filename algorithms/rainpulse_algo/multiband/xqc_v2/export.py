"""Bounded deterministic native evidence, referenced from the existing manifest."""
from __future__ import annotations
import hashlib
import io
import json
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import numpy as np


def encode_arrays(arrays, *, maximum_decoded_bytes=256 * 1024**2, maximum_encoded_bytes=64 * 1024**2):
    if sum(np.asarray(a).nbytes for a in arrays.values()) > maximum_decoded_bytes:
        raise ValueError("X native diagnostic decoded budget exceeded")
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
        for key in sorted(arrays):
            if not key or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in key):
                raise ValueError("invalid native numeric key")
            a = np.asarray(arrays[key])
            if a.dtype.hasobject:
                raise ValueError("object diagnostic arrays are forbidden")
            info = ZipInfo(key + ".npy", (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            with archive.open(info, "w") as member:
                np.lib.format.write_array(member, a, allow_pickle=False)
            if stream.tell() > maximum_encoded_bytes:
                raise ValueError("X native diagnostic encoded budget exceeded")
    data = stream.getvalue()
    if len(data) > maximum_encoded_bytes:
        raise ValueError("X native diagnostic encoded budget exceeded")
    return data


def export_sweep(sweep, metadata, objects):
    record = getattr(sweep, "xqc_diagnostics", None)
    if record is None:
        return None  # Disabled path emits exactly the previous product.
    number = int(sweep.number)
    detail = dict(record)
    key = f"qc-v2/{number}/evidence.json"
    numerical = None
    if record.get("export_native", True):
        needed = {"DBZH_RAW", "DBZH_QC", "QC_ACTION", "MB_QC_FLAGS",
                  "REFLECTIVITY_ELIGIBLE_FOR_CR", "QPE_ELIGIBLE_MASK"}
        arrays = {k: v for k, v in sweep.fields.items() if k in needed or k.startswith("XQC_")}
        arrays.update(azimuth=sweep.azimuth_deg, range_m=sweep.range_m,
                      elevation=sweep.elevation_deg, ray_time_epoch=sweep.ray_time_epoch)
        data = encode_arrays(arrays)
        numeric_key = f"qc-v2/{number}/native.npz"
        if numeric_key in objects:
            raise ValueError("duplicate X cut diagnostics")
        objects[numeric_key] = data
        numerical = {"object_path": numeric_key, "sha256": hashlib.sha256(data).hexdigest(),
                     "index_space": "original_acquisition_ray_gate", "fields": sorted(arrays)}
    detail["native"] = numerical
    detail["qc_action_semantics"] = {"0": "MISSING", "1": "KEEP", "2": "REJECT", "3": "UNCERTAIN"}
    detail["quality_semantics"] = "candidate-heuristic-not-probability-v1"
    data = json.dumps(detail, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(data) > 8 * 1024**2 or sum(map(len, objects.values())) + len(data) > 512 * 1024**2:
        raise ValueError("X diagnostic product exceeds byte budget")
    if key in objects:
        raise ValueError("duplicate X evidence entry")
    objects[key] = data
    return {"version": record["version"], "parameter_sha256": record["parameter_sha256"],
            "mode": record["mode"], "status": record["status"],
            "evidence_path": key, "evidence_sha256": hashlib.sha256(data).hexdigest(),
            "native": numerical, "rejected_gates": record["rejected_gates"],
            "withheld_gates": record["withheld_gates"], "qpe_enabled": False}
