"""One X moment-validity contract, shared by detection, phase and admission.

Finite values and every supplied VALID/AVAILABLE alias constrain support.
Missing telemetry is not a quiet receiver or observed no echo. These helpers
never overwrite the raw values/masks and do not apply scientific thresholds.
"""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
import numpy as np


def binary_mask(fields: Mapping, name: str, shape: tuple, *, default=False):
    if name not in fields:
        return np.full(shape, default, dtype=bool)
    value = np.asarray(fields[name])
    if value.shape != shape or value.dtype.kind not in 'bufi' or not np.isin(value, (0, 1)).all():
        raise ValueError(f'{name}: native binary mask/shape required')
    return value.astype(bool, copy=True)


@dataclass(frozen=True)
class MomentSupport:
    values: np.ndarray
    valid: np.ndarray
    source: str | None
    mask_names: tuple[str, ...]


def moment_support(fields: Mapping, name: str, shape: tuple, *, geometry=None) -> MomentSupport:
    aliases = ('SNRH', 'SNR') if name in ('SNR', 'SNRH') else (name,)
    source = next((key for key in aliases if key in fields), None)
    mask_names = tuple(key + suffix for key in aliases
                       for suffix in ('_AVAILABLE_MASK', '_VALID_MASK') if key + suffix in fields)
    # Validate supplied masks even when a moment is absent (no silent malformed input).
    declared = np.ones(shape, bool)
    for key in mask_names:
        declared &= binary_mask(fields, key, shape)
    if source is None:
        return MomentSupport(np.full(shape, np.nan, np.float32), np.zeros(shape, bool), None, mask_names)
    a = np.asarray(fields[source])
    if a.shape != shape or a.dtype.kind not in 'fiu':
        raise ValueError(f'{source}: numeric native moment/shape required')
    ok = np.isfinite(a) & declared
    if name == 'RHOHV':
        ok &= (a >= 0) & (a <= 1)
    if geometry is not None:
        good = np.asarray(geometry)
        if good.shape != (shape[0],) or not np.isin(good, (0, 1)).all():
            raise ValueError('invalid moment geometry support')
        ok &= good.astype(bool)[:, None]
    return MomentSupport(a, ok, source, mask_names)
