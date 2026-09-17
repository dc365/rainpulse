"""Missing values never become clean measurements in texture calculations."""
import numpy as np
from scipy.ndimage import uniform_filter1d

from .geometry import measured, shifted_measured


def supported_mean(values, support, size):
    count = uniform_filter1d(support.astype(float), size, axis=1, mode="constant") * size
    total = uniform_filter1d(np.where(support, values, 0).astype(float), size, axis=1, mode="constant") * size
    mean = np.divide(total, count, out=np.full(values.shape, np.nan), where=count > 0)
    return mean, count


def features(n, cfg):
    shape = n.shape
    z = n.fields["DBZH"]
    obs = measured(n, "DBZH") & n.geometry_good[:, None]
    missing = np.full(shape, np.nan)
    snr = n.fields.get("SNR", missing)
    rho = n.fields.get("RHOHV", missing)
    phase = n.fields.get("PHIDP", missing)
    reliable = obs & measured(n, "SNR") & (snr >= cfg.minimum_reliable_snr_db)
    reliable &= measured(n, "RHOHV") & measured(n, "PHIDP")
    pair = np.zeros(shape, bool)
    pair[:, 1:] = reliable[:, 1:] & reliable[:, :-1]
    increment = np.zeros(shape)
    increment[:, 1:] = (np.diff(phase, axis=1) + 180) % 360 - 180
    size = max(3, int(np.ceil(cfg.feature_window_m / n.gate_spacing_m)) | 1)
    sine, count = supported_mean(np.sin(np.deg2rad(increment)), pair, size)
    cosine, _ = supported_mean(np.cos(np.deg2rad(increment)), pair, size)
    resultant = np.clip(np.hypot(sine, cosine), 1e-12, 1)
    phase_std = np.rad2deg(np.sqrt(-2 * np.log(resultant)))
    phase_available = (count >= max(3, size * 0.8)) & reliable
    phase_std[~phase_available] = np.nan
    mean, zcount = supported_mean(z, obs, size)
    square, _ = supported_mean(z * z, obs, size)
    texture = np.sqrt(np.maximum(0, square - mean * mean))
    texture[zcount < size * 0.8] = np.nan
    healthy = reliable & (rho >= cfg.healthy_rho)
    healthy &= phase_available & (phase_std <= cfg.healthy_phase_increment_deg)
    left, lok = shifted_measured(n, -1)
    right, rok = shifted_measured(n, 1)
    weather = healthy & healthy[left] & healthy[right] & (lok & rok)[:, None]
    weather &= abs(z - z[left]) <= 6
    weather &= abs(z - z[right]) <= 6
    return {"reliable": reliable, "phase_std": phase_std, "texture": texture,
            "low_pol": reliable & (rho < cfg.low_rho), "local_weather": weather,
            "phase_bad": phase_available & (phase_std >= cfg.phase_increment_std_deg)}
