"""Same-ray fragment completion around confirmed radial anchors.

Port of the S fragment phase's association contract (qc_engine/fragment_radials.py):
identity never confers pollution. A target gate joins a spoke only when the ray
already carries enough confirmed anchors, the gate sits within a bounded range
distance of one, its reflectivity follows the anchor's 20log10(r) source law,
and the gate itself still shows local polarimetric badness (the X conjunction
or the S PHIDP circular-variance test). RHOHV above the radial cap can never
be associated, so rain stays structurally excluded.
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import convolve1d


def _local_polar_bad(s, cfg, jitter):
    """X conjunction evidence OR the S windowed PHIDP circular-variance test."""
    sn, sa = s.moment("SNR")
    rho, ar = s.moment("RHOHV")
    zdr, ad = s.moment("ZDR")
    phi, ap = s.moment("PHIDP")
    zdr_abnormal = ad & ((zdr < -1.) | (zdr > 4.))
    conjunction = (ar & sa & (rho <= cfg.radial_maximum_rhohv) &
                   (sn >= cfg.radial_minimum_snr_db) &
                   (zdr_abnormal | (ap & np.isfinite(jitter) &
                                    (jitter >= cfg.radial_phase_jitter_deg))))
    pair = (ar & ap & sa & np.isfinite(rho) & np.isfinite(phi) &
            (sn >= cfg.radial_minimum_snr_db))
    w = cfg.fragment_phase_window_gates
    ones = np.ones(w)
    n = convolve1d(pair.astype(float), ones, axis=1, mode="constant", cval=0)
    angle = np.where(pair, phi, 0.) * (2 * np.pi / 360.)
    c = convolve1d(np.where(pair, np.cos(angle), 0.), ones, axis=1, mode="constant", cval=0)
    si = convolve1d(np.where(pair, np.sin(angle), 0.), ones, axis=1, mode="constant", cval=0)
    variance = np.clip(1 - np.hypot(c, si) / np.maximum(n, 1), 0, 1)
    phase_ok = pair & (n >= np.ceil(w * cfg.fragment_phase_minimum_fraction))
    rho_mean = (convolve1d(np.where(pair, rho, 0.), ones, axis=1, mode="constant", cval=0)
                / np.maximum(n, 1))
    circular = (phase_ok & (rho <= cfg.radial_maximum_rhohv) &
                (rho_mean <= cfg.radial_maximum_rhohv) &
                (variance >= cfg.fragment_phase_variance_minimum))
    return conjunction | circular


def associate(s, anchors, cfg, *, hard, local, jitter):
    """Complete confirmed spoke evidence along its own rays; never across rays."""
    linked = np.zeros(s.shape, bool)
    z, za = s.moment("DBZH")
    bad = _local_polar_bad(s, cfg, jitter)
    echo = (za & np.isfinite(z) & (z >= cfg.fragment_minimum_echo_dbz) &
            (z < cfg.radial_maximum_dbzh) & ~hard & ~local & bad)
    r = np.asarray(s.ranges, float)
    anchor_rays = 0
    for ray in np.flatnonzero(s.good):
        seeds = np.flatnonzero(anchors[ray])
        if len(seeds) < cfg.fragment_minimum_anchor_gates:
            continue
        anchor_rays += 1
        targets = np.flatnonzero(echo[ray] & ~anchors[ray])
        if not len(targets):
            continue
        pos = np.searchsorted(seeds, targets)
        left = seeds[np.maximum(0, pos - 1)]
        right = seeds[np.minimum(len(seeds) - 1, pos)]
        origin = np.where(abs(targets - left) <= abs(targets - right), left, right)
        dist = abs(r[targets] - r[origin])
        # An external source obeys the 20log10(r) power law; a rain core does not.
        correction = 20 * np.log10(np.maximum(r[targets], s.dr / 2) /
                                   np.maximum(r[origin], s.dr / 2))
        delta = abs(z[ray, targets] - z[ray, origin] - correction)
        linked[ray, targets[(dist <= cfg.fragment_maximum_distance_m) &
                            (delta <= cfg.fragment_association_difference_db)]] = True
    return linked, {
        "anchor_rays": anchor_rays,
        "associated_gates": int(linked.sum()),
        "maximum_distance_m": cfg.fragment_maximum_distance_m,
        "association_difference_db": cfg.fragment_association_difference_db,
    }
