"""Multiple evidence families, with explicit abstention and weather conflict."""
from dataclasses import dataclass
from enum import IntFlag

import numpy as np

from .geometry import measured


class Reason(IntFlag):
    MORPHOLOGY = 1
    RAW_POLAR_ANOMALY = 2
    STATIC_PRIOR = 4
    DOPPLER_STATIONARY = 8
    ROUGH_TEXTURE = 16
    EXTERNAL_AP = 32
    MARINE_CONTEXT = 64
    BIOLOGICAL_CANDIDATE = 128
    LOCAL_WEATHER = 256
    EXTERNAL_WEATHER = 512
    MISSING_PRIOR = 1024
    INCOMPLETE_MEASUREMENT = 2048
    CONFLICT = 4096
    EXPERIMENTAL_QUARANTINE = 8192
    SMALL_OBJECT = 16384
    NARROW_SOURCE_MATCH = 32768


@dataclass(frozen=True)
class Context:
    """Optional co-registered facts; never infer them from ocean/height alone."""
    values: dict
    available: dict
    receipt_sha256: str

    def evidence(self, name, shape):
        if len(self.receipt_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.receipt_sha256):
            raise ValueError("context content receipt required")
        if name not in self.values:
            return np.zeros(shape, bool)
        v, a = np.asarray(self.values[name]), np.asarray(self.available[name], bool)
        if v.shape != shape or a.shape != shape or np.any(a & ~np.isfinite(v)):
            raise ValueError("invalid comparable context")
        return a & (v >= 0.7)


def clutter_rules(n, cfg, f, prior=None, context=None):
    empty = np.zeros(n.shape, bool)
    z = n.fields["DBZH"]
    vr = n.fields.get("VR", np.full(n.shape, np.nan))
    sw = n.fields.get("SW", np.full(n.shape, np.nan))
    stationary = measured(n, "VR") & measured(n, "SW") & f["reliable"]
    stationary &= (abs(vr) <= cfg.ground_velocity_mps) & (sw >= 0) & (sw <= cfg.ground_width_mps)
    rough = np.isfinite(f["texture"]) & (f["texture"] >= 6)
    pol = f["low_pol"] & f["phase_bad"]
    prior_good = empty if prior is None else np.isfinite(prior["lower"])
    historical = empty if prior is None else prior_good & (prior["lower"] >= cfg.strong_prior_lower_bound)
    ground = historical & ((stationary & rough) | pol)
    ap = empty if context is None else context.evidence("ap_observability", n.shape) & stationary & rough & pol
    sea = empty if context is None else context.evidence("marine_low_beam", n.shape) & pol & rough
    zdr = n.fields.get("ZDR", np.full(n.shape, np.nan))
    bio = f["low_pol"] & measured(n, "ZDR") & (zdr >= 2) & (z < 30)
    return {"ground": ground, "ap": ap, "sea": sea, "bio": bio,
            "historical": historical, "prior_available": prior_good,
            "stationary": stationary, "rough": rough, "polar": pol}
