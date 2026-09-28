"""Original X gate identity, independent moment support, no fabricated geometry."""
from dataclasses import dataclass
import numpy as np
from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep, ResourceLimit

MOMENTS = ("DBZH", "SNR", "RHOHV", "ZDR", "PHIDP", "VR", "SW")


def mask(fields, name, shape, default=False):
    if name not in fields:
        return np.full(shape, default, bool)
    a = np.asarray(fields[name])
    if a.shape != shape or not np.isin(a, (0, 1)).all():
        raise ValueError("invalid native mask " + name)
    return a.astype(bool)


@dataclass(frozen=True)
class NativeView:
    sweep: Sweep
    order: np.ndarray
    report: dict

    def restore(self, value):
        a = np.asarray(value)
        if a.shape[0] != len(self.order):
            raise ValueError("cannot restore a different ray geometry")
        out = np.empty_like(a)
        out[self.order] = a
        return out


def adapt(cut, cfg):
    shape = np.shape(cut.fields["DBZH"])
    if len(shape) != 2 or shape != (len(cut.azimuth_deg), len(cut.range_m)):
        raise ValueError("X native geometry differs from DBZH")
    if np.prod(shape) > cfg.maximum_sweep_gates:
        raise ResourceLimit("X enhancement cut budget exceeded")
    if min(shape) < 2:
        raise ResourceLimit("X enhancement needs at least two native rays/gates")
    az = np.asarray(cut.azimuth_deg, float)
    r = np.asarray(cut.range_m, float)
    el = np.asarray(cut.elevation_deg, float)
    t = np.asarray(cut.ray_time_epoch, float)
    if el.shape != az.shape or t.shape != az.shape or not np.isfinite(np.r_[az, r, el, t]).all():
        raise ValueError("invalid actual coordinates or ray timestamps")
    if np.any(np.diff(r) <= 0) or r[0] < 0:
        raise ValueError("invalid native ranges")
    if not np.allclose(np.diff(r), np.median(np.diff(r)), rtol=.001, atol=.001):
        raise ResourceLimit("nonuniform ranges: no silent resampling for shared stencil")
    order = np.argsort(az, kind="stable")
    az, el, t = az[order], el[order], t[order]
    da = (np.roll(az, -1) - az) % 360
    duplicate = da <= .01
    good = ~(duplicate | np.roll(duplicate, 1))
    # Preserve every acquisition row. Duplicate/sector boundaries are barriers.
    gaps = (da > cfg.maximum_gap_deg) | ~good | ~np.roll(good, -1)
    gaps |= abs(np.roll(t, -1) - t) > cfg.maximum_neighbor_time_s
    gaps |= abs(np.roll(el, -1) - el) > cfg.maximum_neighbor_elevation_deg
    obs = mask(cut.fields, "OBSERVED_MASK", shape)
    no = mask(cut.fields, "NO_ECHO_MASK", shape)
    if np.any(no & ~obs):
        raise ValueError("unobserved gate cannot be valid no echo")
    fields, available = {}, {}
    for k in MOMENTS:
        source = "SNRH" if k == "SNR" and "SNRH" in cut.fields else k
        if source not in cut.fields:
            continue
        a = np.asarray(cut.fields[source], np.float32)
        if a.shape != shape:
            raise ValueError("moment geometry differs: " + source)
        ok = np.isfinite(a)
        for key in (source + "_AVAILABLE_MASK", source + "_VALID_MASK", k + "_AVAILABLE_MASK"):
            if key in cut.fields:
                ok &= mask(cut.fields, key, shape)
        if k == "DBZH":
            ok &= obs & ~no & (a >= -32) & (a <= 80)
        elif k == "RHOHV":
            ok &= (a >= 0) & (a <= 1)
        elif k in ("VR", "SW"):
            # Read and preserve them, but do not infer action-grade pairing.
            ok &= cfg.doppler_verified
            if cfg.doppler_verified:
                ok &= abs(a) <= cfg.nyquist_velocity_mps if k == "VR" else a >= 0
        fields[k] = a[order]
        available[k] = ok[order] & good[:, None]
    s = Sweep(f"sweep_{cut.number:03d}", az, el, r, fields, available, good, gaps, t,
              no_echo=no[order] & good[:, None], original_indices=order)
    return NativeView(s, order, {"status": "ADAPTED", "original_rows_preserved": shape[0],
        "duplicate_or_ambiguous_rows": int((~good).sum()), "geometry_gap_count": int(gaps.sum()),
        "gate_spacing_m": s.dr, "moment_samples": {k: int(a.sum()) for k, a in s.available.items()},
        "doppler_action_verified": cfg.doppler_verified,
        "cross_cut_context": "NOT_BOUND_SINGLE_CUT", "ray_order": "sorted_with_explicit_original_mapping"})
