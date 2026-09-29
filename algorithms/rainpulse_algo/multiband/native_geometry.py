"""Canonical native-row selection is a projection operation, never QC input."""
from __future__ import annotations
import numpy as np


def representative_rows(azimuth, ray_time):
    az, t = np.asarray(azimuth), np.asarray(ray_time)
    if az.ndim != 1 or t.shape != az.shape or not np.isfinite(az).all() or not np.isfinite(t).all():
        raise ValueError('projection requires finite native bearings and acquisition times')
    if np.any((az < 0) | (az >= 360)):
        raise ValueError('native bearing outside [0,360)')
    order = np.lexsort((np.arange(len(az)), -t, az))
    _, positions = np.unique(az[order], return_index=True)
    return np.sort(order[positions])


def projectable_sweep(sweep):
    from .model import Sweep
    chosen = representative_rows(sweep.azimuth_deg, sweep.ray_time_epoch)
    result = Sweep(sweep.number, sweep.azimuth_deg[chosen], sweep.range_m,
                   sweep.elevation_deg[chosen], sweep.ray_time_epoch[chosen],
                   {k: v[chosen] for k, v in sweep.fields.items()})
    return result, chosen


def qc_then_select(volume, station, release_sha256, *, qc):
    """Use exactly the single-station QC observation domain, then project.

    S behavior remains unchanged. X sees every original acquisition row,
    including ambiguous repeats; selecting representatives cannot erase QC
    geometry barriers before detection. The mapping always points to RAW rows.
    """
    from .model import Volume
    if len(volume.sweeps) != 1:
        raise ValueError('one cut required at projection boundary')
    if station.band == 'X':
        result = qc(volume, station, release_sha256)
        cut, chosen = projectable_sweep(result.sweeps[0])
        return Volume(result.metadata, [cut]), chosen
    cut, chosen = projectable_sweep(volume.sweeps[0])
    result = Volume(volume.metadata, [cut])
    result.validate(station, require_geometry=False)
    if result.metadata.get('qc_pipeline_version') not in station.allowed_s_qc_versions:
        raise ValueError('unapproved S QC version')
    return result, chosen
