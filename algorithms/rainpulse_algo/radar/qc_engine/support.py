"""Source-aware support. Missing neighbours are unknown, never negative evidence."""
import numpy as np


def weather_support(vertical, cross, radial, *, split=False):
    vertical = np.asarray(vertical, dtype='float32')
    cross = np.full(vertical.shape, np.nan, 'float32') if cross is None else np.asarray(cross, dtype='float32')
    radial = np.asarray(radial, dtype=bool)
    if cross.shape != vertical.shape or radial.shape != vertical.shape:
        raise ValueError('weather support geometry mismatch')
    merged = np.fmax(vertical, cross)
    # Only existing radial candidates lose vertical-only protection. Detection
    # and independent rejection requirements remain the responsibility of decide.
    selected = np.where(radial, cross, merged) if split else merged
    return selected.astype('float32')
