"""Deterministic files, checked inputs, no unpickling, no network/publication."""
from __future__ import annotations
from pathlib import Path
import io
import hashlib
import json
import zipfile
import numpy as np


def sha_file(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_case(root, item):
    name = item["case"]
    if not isinstance(name, str) or not name.isalnum():
        raise ValueError("unsafe case name")
    path = Path(root)/(name+'.npz')
    if path.is_symlink() or sha_file(path) != item["sha256"]:
        raise ValueError("case checksum mismatch")
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != set(item["arrays"]):
            raise ValueError("case field inventory differs from manifest")
        values = {k: data[k] for k in data.files}
    for key, v in values.items():
        info = item["arrays"][key]
        if v.dtype.kind not in "bifu" or list(v.shape) != info['shape'] or str(v.dtype) != info['dtype']:
            raise ValueError(f"invalid case array: {key}")
    return values


def write_npz(path, arrays):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, array in sorted(arrays.items()):
            if not name.replace('_','').isalnum():
                raise ValueError("unsafe array name")
            v = np.asarray(array)
            if v.dtype.kind not in 'bifu':
                raise ValueError("numeric arrays only")
            b = io.BytesIO()
            np.lib.format.write_array(b, v, allow_pickle=False)
            i = zipfile.ZipInfo(name+'.npy', (1980,1,1,0,0,0))
            i.compress_type = zipfile.ZIP_DEFLATED
            i.external_attr = 0o100644 << 16
            z.writestr(i, b.getvalue(), compresslevel=6)


def clean(v):
    if isinstance(v, dict):
        return {str(k): clean(x) for k,x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, np.generic):
        return clean(v.item())
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


def write_json(path, value):
    Path(path).write_text(json.dumps(clean(value), ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, indent=2)+'\n', encoding='utf-8')
