"""Capability-routed, multi-contour objects. Morphology never directly deletes."""
from collections import Counter
import numpy as np
from scipy.ndimage import uniform_filter1d
from skimage.measure import regionprops
from .data import ResourceLimit
from .geometry import label_native, angular_widths, wrap, xyz


def capability(sweep, cfg):
    gates = np.flatnonzero(sweep.observed.any(axis=0))
    span = float(sweep.ranges[gates[-1]]-sweep.ranges[gates[0]]+sweep.dr) if len(gates) else 0.
    scales = [int(s) for s in cfg.scales_m if span >= s and int(np.ceil(s/sweep.dr)) >= 3]
    moments = {k: int(a.sum()) for k, a in sweep.available.items()}
    source_possible = all(moments.get(k, 0) >= cfg.minimum_reference_samples
                          for k in ("DBZH", "SNR", "RHOHV", "ZDR", "PHIDP"))
    if not gates.size:
        state = "NOT_APPLICABLE_NO_DBZH"
    elif not scales:
        state = "NOT_APPLICABLE_INSUFFICIENT_SPAN"
    else:
        state = "APPLICABLE"
    return {"status": state, "observed_span_m": span, "maximum_slant_range_m": float(sweep.ranges[-1]),
            "elevation_median_deg": float(np.median(sweep.elevation)), "available_scales_m": scales,
            "moment_counts": moments, "source_moments_available": source_possible,
            "ray_time_available": sweep.ray_time_s is not None,
            "relative_height_datum": "above_radar_effective_4_3_earth"}


def extract_objects(sweep, cfg):
    cap = capability(sweep, cfg)
    fields = {"VOR_OBJECT_ID": np.zeros(sweep.shape, "uint32"),
              "VOR_CANDIDATE_MASK": np.zeros(sweep.shape, "uint8"),
              "VOR_RADIAL_GEOMETRY_MASK": np.zeros(sweep.shape, "uint8"),
              "VOR_SCALE_BITS": np.zeros(sweep.shape, "uint16"),
              "VOR_CONTOUR_COUNT": np.zeros(sweep.shape, "uint8")}
    if cap["status"] != "APPLICABLE":
        return fields, [], cap
    z = sweep.fields["DBZH"]; obs = sweep.observed
    aw = angular_widths(sweep)
    records = []
    for level in cfg.levels_dbz:
        echo = obs & (z >= level)
        # A support fraction is a topological diagnostic, NOT a clear-air fraction.
        # No reflected/wrapped range boundary and no synthesized reflectivity.
        response = np.zeros(sweep.shape, "uint16")
        for bit, scale in enumerate(cfg.scales_m):
            if scale not in cap["available_scales_m"]:
                continue
            width = int(np.ceil(scale/sweep.dr)) | 1
            if width > sweep.shape[1]:
                continue
            count = uniform_filter1d(echo.astype(float), width, axis=1, mode="constant", cval=0)
            hit = echo & (count >= cfg.minimum_window_support)
            response[hit] |= 1 << bit
        labels = label_native(echo, sweep)
        for prop in regionprops(labels, cache=False):
            if prop.area < cfg.minimum_object_gates:
                continue
            coords = prop.coords; rows, gates = coords[:, 0], coords[:, 1]
            span = float(np.ptp(sweep.ranges[gates])+sweep.dr)
            if span < cfg.minimum_object_span_m or not np.any(response[rows, gates]):
                continue
            if len(records) >= cfg.maximum_objects:
                raise ResourceLimit("object count")
            angles = sweep.azimuth[rows]
            # Stable circular centre; full rings are explicitly non-radial.
            vec = np.mean(np.exp(1j*np.deg2rad(angles)))
            centre = float(np.angle(vec, deg=True) % 360) if abs(vec) > 1e-8 else 0.
            extent = float(min(360., np.ptp(wrap(angles-centre))+np.max(aw[rows])))
            mid = float(np.median(sweep.ranges[gates]))
            aspect = span/max(sweep.dr, mid*np.deg2rad(extent))
            area = float(np.sum(sweep.ranges[gates]*sweep.dr*np.deg2rad(aw[rows]))/1e6)
            radial = extent <= cfg.maximum_source_width_deg and aspect >= cfg.minimum_source_aspect
            family = "radial" if radial else ("annular" if extent > 180 and span < mid*.5 else "patch_or_fan")
            # Per-range real left/right boundaries, not points on an assumed single ray.
            boundary_centres = []
            for b in np.unique((sweep.ranges[gates]//cfg.scales_m[0]).astype(int)):
                ii = (sweep.ranges[gates]//cfg.scales_m[0]).astype(int) == b
                if ii.sum() >= 3:
                    boundary_centres.append(float(np.median(wrap(angles[ii]-centre))))
            stability = float(np.percentile(abs(np.asarray(boundary_centres)), 90)) if boundary_centres else None
            oid = len(records)+1
            existing = fields["VOR_OBJECT_ID"][rows, gates]
            parent_ids, parent_counts = np.unique(existing[existing > 0], return_counts=True)
            parent = int(parent_ids[np.argmax(parent_counts)]) if len(parent_ids) else 0
            free = existing == 0
            fields["VOR_OBJECT_ID"][rows[free], gates[free]] = oid
            fields["VOR_CANDIDATE_MASK"][rows, gates] = 1
            if radial:
                fields["VOR_RADIAL_GEOMETRY_MASK"][rows, gates] = 1
            fields["VOR_SCALE_BITS"][rows, gates] |= response[rows, gates]
            fields["VOR_CONTOUR_COUNT"][rows, gates] += 1
            step = max(1, len(rows)//64)
            sample = xyz(sweep, rows[::step][:64], gates[::step][:64])
            records.append({"id": oid, "parent_id": parent, "level_dbz": float(level),
                            "family": family, "radial_geometry": bool(radial), "gates": int(len(rows)),
                            "area_km2": area, "range_min_m": float(sweep.ranges[gates].min()),
                            "range_max_m": float(sweep.ranges[gates].max()), "range_span_m": span,
                            "azimuth_center_deg": centre, "azimuth_width_deg": extent,
                            "radial_aspect": aspect, "boundary_spread_deg": stability,
                            "xyz_sample_m": sample.tolist(), "evidence_group": "same_raw_DBZH",
                            "time_min_s": float(sweep.ray_time_s[rows].min()) if sweep.ray_time_s is not None else None,
                            "time_max_s": float(sweep.ray_time_s[rows].max()) if sweep.ray_time_s is not None else None})
    cap["status"] = "CANDIDATES" if records else "NO_CANDIDATE"
    cap["objects"] = len(records)
    cap["families"] = dict(Counter(o["family"] for o in records))
    cap["candidate_gates"] = int(fields["VOR_CANDIDATE_MASK"].sum())
    return fields, records, cap
