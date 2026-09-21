"""Measured local features. No filtered image, winner or previous deletion as input."""
from dataclasses import dataclass
import numpy as np
from scipy.ndimage import uniform_filter, maximum_filter1d
from ..data import ResourceLimit
from ..near_measurement.backends import depolarization


def ramp(x, a, b):
    return np.clip((x-a)/(b-a), 0., 1.)


def _average(a, window):
    return uniform_filter(np.asarray(a, float), window, mode=("wrap", "constant"))


def samples(mask, window):
    return np.rint(_average(mask, window) * np.prod(window)).astype("uint16")


def safe_stencil(s, window, maximum_spacing):
    bad = ~s.good | s.gap_after | np.roll(s.gap_after, 1)
    row = ~maximum_filter1d(bad.astype("uint8"), size=window[0], mode="wrap").astype(bool)
    delta = (np.roll(s.azimuth, -1) - s.azimuth) % 360
    positive = delta[(delta > .01) & ~s.gap_after]
    spacing = float(np.median(positive)) if len(positive) else 360.
    out = np.broadcast_to(row[:, None], s.shape).copy()
    half = window[1]//2
    if half:
        out[:, :half] = False
        out[:, -half:] = False
    if s.shape[0] < window[0] or s.shape[1] < window[1] or spacing > maximum_spacing:
        out[:] = False
    return out, spacing


def texture(values, valid, window, cfg, *, circular=False):
    """Sample counts are evidence support, not no-echo observations."""
    valid = np.asarray(valid, bool) & np.isfinite(values)
    count = samples(valid, window)
    den = np.where(count > 0, count, np.nan)
    if circular:
        phase = np.deg2rad(np.where(valid, values, 0.))
        x = _average(np.where(valid, np.cos(phase), 0.), window)*np.prod(window)/den
        y = _average(np.where(valid, np.sin(phase), 0.), window)*np.prod(window)/den
        value = np.rad2deg(np.sqrt(np.maximum(0., -2*np.log(np.clip(np.hypot(x, y), 1e-12, 1.)))))
    else:
        x = np.where(valid, values, 0.)
        mean = _average(x, window)*np.prod(window)/den
        value = np.sqrt(np.maximum(0., _average(x*x, window)*np.prod(window)/den-mean*mean))
    ok = count >= max(cfg.minimum_samples, cfg.minimum_support_fraction*np.prod(window))
    return np.where(ok, value, np.nan).astype("float32"), count


@dataclass(frozen=True)
class Features:
    arrays: dict
    summary: dict


def extract(s, cfg):
    if np.prod(s.shape) > cfg.maximum_sweep_gates:
        raise ResourceLimit("clutter feature sweep gate budget")
    z, obs = s.moment("DBZH")
    obs = obs & (z >= -32) & (z <= 80) & s.good[:, None]
    rho, ar = s.moment("RHOHV"); zdr, ad = s.moment("ZDR")
    snr, ass = s.moment("SNR"); phi, ap = s.moment("PHIDP")
    window = (3, max(3, int(np.ceil(cfg.neighbourhood_m/s.dr)) | 1))
    if np.prod(window) > 65535:
        raise ResourceLimit("clutter neighbourhood count budget")
    safe, spacing = safe_stencil(s, window, cfg.maximum_ray_spacing_deg)
    domain = obs & safe & (s.ranges[None, :] >= cfg.minimum_range_m) & (s.ranges[None, :] <= cfg.maximum_range_m)
    domain &= (z >= cfg.no_rain_below_dbz) & (z < cfg.protected_dbz)
    snr_ok = ass & (snr >= cfg.minimum_snr_db)
    tail = ad & (abs(zdr) >= cfg.maximum_abs_zdr_db)
    pol_ok = ar & snr_ok
    dr_ok = pol_ok & ad & ~tail
    dr, receipt = depolarization(np.where(dr_ok, zdr, np.nan), np.where(dr_ok, rho, np.nan), cfg)
    tz, nz = texture(z, obs, window, cfg)
    pstd, nphi = texture(phi, ap & snr_ok, window, cfg, circular=True)
    increments = np.full(s.shape, np.nan)
    increments[:, 1:] = (np.diff(phi, axis=1)+180.) % 360. - 180.
    pairs = np.zeros(s.shape, bool)
    pairs[:, 1:] = ap[:, 1:] & ap[:, :-1] & snr_ok[:, 1:] & snr_ok[:, :-1]
    if not cfg.minimum_phase_spacing_m <= s.dr <= cfg.maximum_phase_spacing_m:
        pairs[:] = False
    jitter, npair = texture(increments, pairs, window, cfg, circular=True)
    tz[~safe] = np.nan; pstd[~safe] = np.nan; jitter[~safe] = np.nan
    rho_mu = np.where(pol_ok, ramp(.97-rho, 0., .17), np.nan)
    dr_mu = np.where(dr_ok, ramp(dr, -20., -12.), np.nan)
    phase_mu = np.where(pol_ok & np.isfinite(jitter), ramp(jitter, 20., 60.), np.nan)
    # Three correlated expressions of one polarization family: max, never sum votes.
    polar = np.fmax(np.fmax(rho_mu, dr_mu), phase_mu)
    pcount = samples(pol_ok & obs, window)
    ppositive = samples(pol_ok & obs & (polar >= .65), window)
    fraction = np.divide(ppositive, pcount, out=np.full(s.shape, np.nan), where=pcount > 0)
    texture_mu = ramp(tz, 3., 7.)
    weather = (obs & safe & ar & ad & ap & ass & (snr >= 15.) & (rho >= .97) &
               (zdr >= -.5) & (zdr <= 3.) & dr_ok & (dr <= -20.) & np.isfinite(jitter) & (jitter <= 8.))
    bio_ready = (obs & ar & ad & ass & ~tail & (snr >= cfg.biological_minimum_snr_db) & np.isfinite(jitter))
    bio_zdr = ramp(zdr, 2., 4.)
    bio_phi = ramp(jitter, 15., 45.)
    bio = np.where(bio_ready, .45*rho_mu + .30*bio_zdr + .25*bio_phi, np.nan)
    out = {
        "CF_OBSERVED_MASK": obs, "CF_DOMAIN_MASK": domain, "CF_SAFE_MASK": safe & obs,
        "CF_STRONG_MASK": obs & (z >= cfg.protected_dbz), "CF_WEATHER_PROXY_MASK": weather,
        "CF_POLAR_AVAILABLE_MASK": pol_ok & obs, "CF_DR_AVAILABLE_MASK": dr_ok & obs,
        "CF_BIO_AVAILABLE_MASK": bio_ready, "CF_ZDR_TAIL_MASK": tail & obs,
    }
    out = {k: v.astype("uint8") for k, v in out.items()}
    for name, value in {"CF_Z_TEXTURE_DB": tz, "CF_PHI_CIRCSTD_DEG": pstd, "CF_PHI_JITTER_DEG": jitter,
            "CF_DR_DB": dr, "CF_POLAR_SCORE": polar, "CF_RHO_SCORE": rho_mu,
            "CF_TEXTURE_SCORE": texture_mu, "CF_BIO_ZDR_SCORE": bio_zdr,
            "CF_BIO_PHASE_SCORE": bio_phi, "CF_BIO_SCORE": bio,
            "CF_NEIGHBOUR_FRACTION": fraction}.items():
        out[name] = np.where(obs, value, np.nan).astype("float32")
    out.update(CF_POLAR_SAMPLE_COUNT=np.where(obs, pcount, 0).astype("uint16"),
               CF_TEXTURE_SAMPLE_COUNT=np.where(obs, nz, 0).astype("uint16"),
               CF_PHASE_PAIR_COUNT=np.where(obs, npair, 0).astype("uint16"))
    return Features(out, {"window_rays_gates": list(window), "radial_window_m": window[1]*s.dr,
        "angular_spacing_deg": spacing, "cross_range_window_m_minmax":
        [float(s.ranges[0]*np.deg2rad(3*spacing)), float(s.ranges[-1]*np.deg2rad(3*spacing))],
        "phase_increment_native_spacing_m": s.dr, "phase_spacing_applicable": bool(pairs.any()),
        "library": receipt, "scores_are_probabilities": False})
