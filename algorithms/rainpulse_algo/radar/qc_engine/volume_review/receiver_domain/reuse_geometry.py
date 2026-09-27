"""Sweep-local non-statistical RDR preparation; no source models are cached."""
import numpy as np


class ExcludedRowMask:
    """Read-only indexing view: an excluded target ray supplies no donor SNR.

    The baseline copied the whole ray/gate mask for every target row. All current
    donor fit accesses are row or row/index accesses, so they need no global copy.
    """
    def __init__(self, mask, row):
        self.mask = mask
        self.row = int(row)
        self.shape, self.dtype, self.ndim = mask.shape, mask.dtype, mask.ndim

    def __getitem__(self, index):
        row = index[0] if isinstance(index, tuple) else index
        value = self.mask[index]
        if isinstance(row, (int, np.integer)):
            if int(row) % self.shape[0] == self.row:
                return np.zeros_like(value, dtype=bool)
            return value
        # Compatibility fallback for an unusual caller, never silently leave
        # the excluded row available. The main source-family path does not use it.
        return np.asarray(self)[index]

    def __array__(self, dtype=None, copy=None):
        if copy is False:
            raise ValueError("materializing an excluded mask requires a copy")
        result = np.array(self.mask, dtype=dtype, copy=True)
        result[self.row] = False
        return result


class FamilyGeometry:
    def __init__(self, sweep, cfg):
        self.blocks = (sweep.ranges // cfg.block_m).astype(int)
        self.fine = (sweep.ranges // cfg.source_family.local_block_m).astype(int)
        self._neighbors = {}

    def neighbors(self, row, calculate):
        if row not in self._neighbors:
            self._neighbors[row] = tuple(calculate())
        return self._neighbors[row]
