"""Content identities for evidence, not file mtimes or operator credentials."""

import hashlib
import json

import numpy as np


def array_digest(value):
    a = np.asarray(value)
    if a.dtype.kind not in "buifmM":
        raise ValueError("only numeric or timestamp evidence arrays can be fingerprinted")
    if a.dtype.kind in "mM":
        a = a.astype("datetime64[ns]" if a.dtype.kind == "M" else "timedelta64[ns]")
    a = np.array(a, dtype=a.dtype.newbyteorder("<"), order="C", copy=True)
    if a.dtype.kind == "f":
        a[np.isnan(a)] = np.nan  # canonicalize NaN payloads; do not fill with zero
        a[a == 0] = 0  # +0/-0 are the same measurement
    h = hashlib.sha256()
    h.update(json.dumps([a.dtype.str, a.shape], separators=(",", ":")).encode())
    h.update(a.tobytes())
    return h.hexdigest()


def context_arrays_identity(contexts):
    manifest = {
        sweep: {name: array_digest(value) for name, value in sorted(fields.items())}
        for sweep, fields in sorted(contexts.items())
    }
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "arrays": manifest}
