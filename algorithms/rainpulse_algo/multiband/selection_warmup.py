"""Prewarm actual contiguous and structured/spilled selection layouts.

Independent of stream_fusion to avoid circular imports. The structured record is
identical to its 36-byte DTYPE, tested explicitly. No parallelism or fastmath.
"""
from __future__ import annotations
import numpy as np
from rainpulse_algo.performance import measure, observe

STATE_DTYPE = np.dtype([(k, 'float64' if k=='score' else 'int32' if k in ('winner','wray','wgate') else 'float32')
                       for k in ('score','values','winner','wray','wgate','h','age','resolution')])


def prewarm(select_winners, require_numba, backend):
    if backend == "numpy":
        return
    if backend != "numba":
        raise ValueError("unknown selection backend")
    with measure("numba.startup_warmup", root=True, identity={"backend": backend}):
        kernel = require_numba()
        before = len(kernel.signatures)
        for sample_type in (np.float32, np.float64):
            for structured in (False, True):
                b = np.ones((2, 3), bool)
                f = np.ones((2, 3), np.float64)
                z = f.astype(sample_type)
                i = np.zeros((2, 3), np.int64)
                if structured:
                    record = np.zeros((2, 2, 3), dtype=STATE_DTYPE)
                    state = tuple(record[name][0] for name in STATE_DTYPE.names)
                    state[0][:] = -np.inf
                else:
                    state = (np.full((2, 3), -np.inf), np.zeros((2, 3), np.float32),
                             *[np.zeros((2, 3), np.int32) for _ in range(3)],
                             *[np.zeros((2, 3), np.float32) for _ in range(3)])
                select_winners(b, f, ~b, z, i, i, f, f, f, 0, *state, backend=backend)
        observe("numba.warmup_signatures_before", int(before), maximum=True)
        observe("numba.warmup_signatures_after", int(len(kernel.signatures)), maximum=True)
