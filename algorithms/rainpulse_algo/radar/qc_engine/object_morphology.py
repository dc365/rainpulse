"""RAW Py-ART object labels and descriptive morphology; never decides QC actions."""

from dataclasses import replace

import numpy as np
from scipy.ndimage import find_objects as object_slices


def object_morphology(native, *, thresholds=(20, 35, 45, 55), source_residual=None):
    import pyart

    residual = (
        np.full(native.shape, np.nan) if source_residual is None else np.asarray(source_residual)
    )
    if residual.shape != native.shape:
        raise ValueError("source residual geometry differs")
    valid = (
        native.field_available["DBZH"]
        & np.isfinite(native.fields["DBZH"])
        & native.geometry_good[:, None]
    )
    edges = [0, *[int(x) + 1 for x in np.flatnonzero(native.gap_after[:-1])], native.shape[0]]
    arrays, records = {}, []
    for threshold in thresholds:
        labels = np.zeros(native.shape, dtype="int32")
        offset = 0
        for lo, hi in zip(edges[:-1], edges[1:]):
            if not valid[lo:hi].any():
                continue
            full = native.full_ppi and len(edges) == 2
            cut = replace(
                native,
                azimuth=native.azimuth[lo:hi],
                elevation=native.elevation[lo:hi],
                ray_time=native.ray_time[lo:hi],
                fields={"DBZH": native.fields["DBZH"][lo:hi]},
                field_available={"DBZH": valid[lo:hi]},
                original_indices=np.arange(hi - lo),
                geometry_good=native.geometry_good[lo:hi],
                gap_after=native.gap_after[lo:hi],
                full_ppi=full,
            )
            radar = cut.to_pyart()
            found = pyart.correct.find_objects(
                radar, "reflectivity", threshold=threshold, smooth=None, delta=2.0 if full else 0.0
            )
            ids = np.asarray(np.ma.filled(found["data"], 0), dtype="int32")
            ids[~valid[lo:hi]] = 0
            labels[lo:hi] = np.where(ids > 0, ids + offset, 0)
            offset += int(ids.max())
        arrays[str(threshold)] = labels
        for index, box in enumerate(object_slices(labels), start=1):
            if box is None:
                continue
            yy, xx = np.nonzero(labels[box] == index)
            yy, xx = yy + box[0].start, xx + box[1].start
            ranges = native.ranges[xx]
            angles = np.unique(native.azimuth[yy] % 360)
            # Complement of largest circular gap is the smallest covering arc.
            width = float(360 - np.max(np.diff(np.r_[angles, angles[0] + 360])))
            span = float(np.ptp(ranges))
            transverse = float(np.median(ranges) * np.deg2rad(width))
            values = residual[yy, xx]
            available = np.isfinite(values)
            records.append(
                {
                    "threshold_dbz": float(threshold),
                    "object_id": index,
                    "gate_count": len(xx),
                    "ray_count": len(np.unique(yy)),
                    "minimum_range_m": float(ranges.min()),
                    "maximum_range_m": float(ranges.max()),
                    "radial_span_m": span,
                    "angular_width_deg": width,
                    "transverse_span_m": transverse,
                    "radial_aspect": span / max(transverse, native.gate_spacing_m),
                    "source_available_gates": int(available.sum()),
                    "source_match_fraction": float(np.mean(abs(values[available]) <= 2.5))
                    if available.any()
                    else None,
                    "audit_only": True,
                }
            )
    return arrays, records
