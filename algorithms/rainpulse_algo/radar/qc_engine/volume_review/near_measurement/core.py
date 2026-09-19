"""Current-volume near-site evidence, independent of winners and future history.

No measurement arrays are filled, smoothed or corrected. Boolean convolutions
count real supporting samples; zeros there mean absence of evidence, not no-rain.
"""
from dataclasses import dataclass
from enum import IntFlag
import numpy as np
from scipy.ndimage import uniform_filter
from .backends import depolarization


class Reason(IntFlag):
    NONMET_POLARIZATION = 1
    LOW_SNR_UNCERTAINTY = 2
    PREVIOUS_WEATHER = 4
    WEATHER_PROXY = 8
    STRONG_ECHO = 16
    UNSAFE_STENCIL = 32
    POLARIZATION_UNAVAILABLE = 64
    SNR_UNAVAILABLE = 128
    ZDR_TAIL_GUARDED = 256


@dataclass(frozen=True)
class Evidence:
    arrays: dict
    summary: dict


def binary(value, shape, name):
    a = np.asarray(value)
    if a.shape != shape or not np.isin(a, (0, 1)).all():
        raise ValueError("invalid binary geometry: " + name)
    return a.astype(bool)


def count(mask, window):
    return np.rint(uniform_filter(np.asarray(mask, float), window,
                                  mode=("wrap", "constant")) * np.prod(window)).astype("uint16")


def circular_std(phi, valid, window):
    den = uniform_filter(valid.astype(float), window, mode=("wrap", "constant"))
    # Avoid nan*0 in both trig numerators.
    angle = np.deg2rad(np.where(valid, phi, 0.))
    x = uniform_filter(np.where(valid, np.cos(angle), 0.), window, mode=("wrap", "constant"))
    y = uniform_filter(np.where(valid, np.sin(angle), 0.), window, mode=("wrap", "constant"))
    ratio = np.divide(np.hypot(x, y), den, out=np.full_like(x, np.nan), where=den > 0)
    result = np.degrees(np.sqrt(np.maximum(0., -2 * np.log(np.clip(ratio, 1e-8, 1.)))))
    return result, den


def evaluate(fields, azimuth, ranges, cfg, *, available=None, geometry_good=None, gap_after=None):
    """Return native acquisition order evidence; explicit availability wins over finite values.

    `available` keys are canonical DBZH/SNR/...; when supplied, a missing key means
    unavailable, never "fall back to finite". Geometry masks are in acquisition
    order; gap_after, when supplied, refers to the sorted-by-azimuth row successors
    and is passed in already sorted order by the adapter.
    """
    z0 = np.asarray(fields["DBZH_RAW"])
    az, r = np.asarray(azimuth, float), np.asarray(ranges, float)
    if (z0.ndim != 2 or az.ndim != 1 or r.ndim != 1 or z0.shape != (len(az), len(r)) or
        len(az) < 2 or len(r) < 2 or not np.isfinite(az).all() or not np.isfinite(r).all() or
        np.any(r < 0) or np.any(np.diff(r) <= 0) or
        not np.allclose(np.diff(r), np.median(np.diff(r)), rtol=1e-3)):
        raise ValueError("unsupported near measurement geometry")
    if z0.size > cfg.maximum_sweep_gates:
        raise ValueError("near measurement sweep resource limit")
    shape = z0.shape
    order = np.argsort(az % 360, kind="stable")
    az = az[order] % 360
    raw = {}
    for k, v in fields.items():
        a = np.asarray(v)
        if a.shape == shape:
            raw[k] = a[order]
        elif k in ("DBZH_RAW", "VALID_MASK", "SNR_RAW", "RHOHV_RAW", "ZDR_RAW", "PHIDP_RAW"):
            raise ValueError("moment geometry differs: " + k)
    gap = (np.roll(az, -1) - az) % 360
    positive = gap[gap > .01]
    spacing = float(np.median(positive)) if len(positive) else 360.
    large, duplicate = gap > 1.8 * spacing, gap <= .01
    good = ~(duplicate | np.roll(duplicate, 1))
    if geometry_good is not None:
        good &= binary(geometry_good, (shape[0],), "geometry_good")[order]
    if gap_after is not None:
        large |= binary(gap_after, (shape[0],), "sorted_gap_after")
    safe_row = good & np.roll(good, 1) & np.roll(good, -1) & ~large & ~np.roll(large, 1)
    # With two rays, a wrapped 3-ray window would double count one observation.
    if len(az) < 3 or spacing > cfg.maximum_ray_spacing_deg:
        safe_row[:] = False

    avail = {}
    def moment(name):
        values = raw.get(name + "_RAW", np.full(shape, np.nan, "float32"))
        ok = np.isfinite(values) & good[:, None]
        if available is not None:
            ok &= binary(available.get(name, np.zeros(shape, bool)), shape, name + " availability")[order]
        if name == "DBZH": ok &= (values >= -32) & (values <= 80)
        if name == "RHOHV": ok &= (values >= 0) & (values <= 1)
        avail[name] = ok
        return values, ok
    z, obs = moment("DBZH")
    if "VALID_MASK" in raw:
        obs = obs & binary(raw["VALID_MASK"], shape, "VALID_MASK")
    avail["DBZH"] = obs
    echo = obs & (z >= cfg.no_rain_below_dbz)
    rho, ar = moment("RHOHV"); zdr, ad = moment("ZDR")
    snr, ass = moment("SNR"); phi, ap = moment("PHIDP")
    tail = ad & (abs(zdr) >= cfg.maximum_abs_zdr_db)
    polar = obs & ar & ad & ass & ~tail
    dr, backend = depolarization(np.where(polar, zdr, np.nan), np.where(polar, rho, np.nan), cfg)
    dr_spacing = float(np.median(np.diff(r)))
    length = int(np.ceil(cfg.neighbourhood_m / dr_spacing)) | 1
    if length > 20001:
        raise ValueError("near measurement stencil resource limit")
    window = (3, length)
    safe = np.broadcast_to(safe_row[:, None], shape).copy()
    half = length // 2
    if half:
        safe[:, :half] = False; safe[:, -half:] = False
    if length > shape[1]: safe[:] = False
    domain = echo & safe & (r[None, :] >= cfg.near_min_m) & (r[None, :] <= cfg.near_max_m)
    reliable_weather = polar & (snr >= cfg.weather_seed_snr_db)
    phstd, coverage = circular_std(phi, reliable_weather & ap, window)
    wx = (reliable_weather & safe & (rho >= cfg.weather_seed_rho) & (zdr >= -.5) & (zdr <= 3.) &
          (dr <= cfg.weather_seed_dr_db) & ap & (phstd <= cfg.weather_phase_std_deg) &
          (coverage >= cfg.weather_phase_coverage))
    previous = np.zeros(shape, bool)
    if "VOR_STATE" in raw: previous |= np.isin(raw["VOR_STATE"], (3, 5))
    if "VOR_REASON" in raw: previous |= (raw["VOR_REASON"].astype("uint32") & (16 | 32)) != 0
    for k in ("NP_WEATHER_PROTECTED_MASK", "NP_MIXED_MASK", "NMR_EXTERNAL_WEATHER_MASK"):
        if k in raw: previous |= binary(raw[k], shape, k)
    strong = obs & (z >= cfg.protected_dbz)
    protected = (previous | wx | strong) & obs
    reliable = polar & (snr >= cfg.reliable_pol_snr_db) & echo
    measured = count(reliable, window)
    nm = reliable & (dr >= cfg.dr_nonmet_db) & (z < cfg.protected_dbz)
    number = count(nm, window)
    fraction = np.divide(number, measured, out=np.full(shape, np.nan), where=measured > 0)
    core = domain & nm & (measured >= cfg.minimum_pol_samples) & (fraction >= cfg.nonmet_fraction) & ~protected
    wx_fraction = count(wx | previous, window) / float(np.prod(window))
    uncertain = (domain & ass & (snr < cfg.uncertainty_snr_db) & (z < cfg.uncertainty_max_dbz) &
                 (wx_fraction < cfg.maximum_weather_fraction) & ~protected)
    # Numeric overlaps are possible if threshold > reliable_pol_snr_db. Disposition
    # records both evidence routes, with nonmet precedence for net removal counts.
    reason = np.zeros(shape, "uint16")
    for m, bit in ((core, Reason.NONMET_POLARIZATION), (uncertain, Reason.LOW_SNR_UNCERTAINTY),
                   (previous, Reason.PREVIOUS_WEATHER), (wx, Reason.WEATHER_PROXY), (strong, Reason.STRONG_ECHO),
                   (~safe, Reason.UNSAFE_STENCIL), (~polar, Reason.POLARIZATION_UNAVAILABLE),
                   (~ass, Reason.SNR_UNAVAILABLE), (tail, Reason.ZDR_TAIL_GUARDED)):
        reason[m & obs] |= int(bit)
    masks = {"NMR_NONMET_CANDIDATE_MASK": core, "NMR_LOW_SNR_UNCERTAIN_MASK": uncertain,
             "NMR_OBSERVED_MASK": obs, "NMR_VALID_NO_RAIN_MASK": obs & ~echo,
             "NMR_PROTECTED_MASK": protected, "NMR_WEATHER_PROXY_MASK": wx & obs,
             "NMR_PREVIOUS_WEATHER_MASK": previous & obs, "NMR_SAFE_MASK": safe & obs,
             "NMR_DOMAIN_MASK": domain, "NMR_DR_AVAILABLE_MASK": polar}
    for name, a in avail.items(): masks["NMR_" + name + "_AVAILABLE_MASK"] = a & obs
    arrays = {k: v.astype("uint8") for k, v in masks.items()}
    arrays.update(NMR_DR_DB=dr, NMR_PHIDP_CIRCSTD_DEG=phstd.astype("float32"),
                  NMR_POL_SAMPLE_COUNT=measured, NMR_NONMET_FRACTION=fraction.astype("float32"),
                  NMR_WEATHER_FRACTION=wx_fraction.astype("float32"), NMR_REASON=reason)
    for m in (core, uncertain):
        if np.any(m & (~domain | protected | ~obs)):
            raise RuntimeError("near evidence violated measured protection")
    restored = {}
    for k, v in arrays.items():
        restored[k] = np.empty_like(v); restored[k][order] = v
    return Evidence(restored, {"status": "EVALUATED", "config_sha256": cfg.digest,
        "nonmet_candidate_gates": int(core.sum()), "low_snr_uncertain_gates": int(uncertain.sum()),
        "observed_gates": int(obs.sum()), "weather_protected_gates": int(protected.sum()),
        "domain_gates": int(domain.sum()), "window_rays_gates": list(window), "library": backend,
        "availability": "explicit_native" if available is not None else "finite_bounded_snapshot_fallback",
        "confirmed_nonmet_gates": 0, "filled_gates": 0, "operational_eligible": False})
