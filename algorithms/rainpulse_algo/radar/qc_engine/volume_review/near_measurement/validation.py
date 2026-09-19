"""Exact new delta + unchanged parent validation on the saved pre-near view."""
from collections.abc import Mapping
import numpy as np
from .config import NearMeasurementConfig
from .core import binary
from .disposition import apply, mutable_names


class BeforeView(Mapping):
    def __init__(self, group):
        self.group = group
        self.keys_ = [k for k in group if not k.startswith("NMR_")]
    def __iter__(self): return iter(self.keys_)
    def __len__(self): return len(self.keys_)
    def __getitem__(self, key):
        if key not in self.keys_: raise KeyError(key)
        prior = "NMR_BEFORE_" + key
        return self.group[prior] if prior in self.group else self.group[key]


def validate_serialized(group, attrs, parent_validator):
    cfg = NearMeasurementConfig.model_validate(attrs.get("qc_near_measurement_config", {}))
    if (attrs.get("qc_near_measurement_version") != cfg.version or
        attrs.get("qc_near_measurement_sha256") != cfg.digest or
        attrs.get("qc_near_measurement_mode") != cfg.mode or attrs.get("operational_eligible") is not False):
        raise ValueError("near measurement config/identity mismatch")
    clean = {k: v for k, v in attrs.items() if not k.startswith("qc_near_measurement_")}
    view = BeforeView(group)
    required = mutable_names(view)
    for key in required:
        if "NMR_BEFORE_" + key not in group: raise ValueError("missing pre-near state: " + key)
    # Runs the full old VOR + NP/OC1/V7 validation, not a synthetic all-KEEP substitute.
    parent_validator(view, clean)
    baseline = {k: np.asarray(view[k][:]) for k in view}
    shape = baseline["DBZH_RAW"].shape
    evidence_names = (
        "NMR_NONMET_CANDIDATE_MASK", "NMR_LOW_SNR_UNCERTAIN_MASK", "NMR_OBSERVED_MASK",
        "NMR_VALID_NO_RAIN_MASK", "NMR_PROTECTED_MASK", "NMR_WEATHER_PROXY_MASK", "NMR_PREVIOUS_WEATHER_MASK",
        "NMR_SAFE_MASK", "NMR_DOMAIN_MASK", "NMR_DR_AVAILABLE_MASK", "NMR_REASON",
        "NMR_DR_DB", "NMR_PHIDP_CIRCSTD_DEG", "NMR_POL_SAMPLE_COUNT", "NMR_NONMET_FRACTION", "NMR_WEATHER_FRACTION",
        *["NMR_" + k + "_AVAILABLE_MASK" for k in ("DBZH", "SNR", "RHOHV", "ZDR", "PHIDP")])
    evidence = {}
    for k in evidence_names:
        if k not in group: raise ValueError("missing near diagnostic " + k)
        v = np.asarray(group[k][:])
        dt = "uint8" if k.endswith("_MASK") else "uint16" if k in ("NMR_REASON", "NMR_POL_SAMPLE_COUNT") else "float32"
        if v.shape != shape or v.dtype != np.dtype(dt): raise ValueError("near dtype/shape differs: " + k)
        if k.endswith("_MASK"): binary(v, shape, k)
        evidence[k] = v
    obs = baseline["VALID_MASK"] == 1
    z = baseline["DBZH_RAW"]
    if np.any(evidence["NMR_OBSERVED_MASK"].astype(bool) & ~obs):
        raise ValueError("near stage created observations")
    for key in ("NMR_NONMET_CANDIDATE_MASK", "NMR_LOW_SNR_UNCERTAIN_MASK"):
        m = evidence[key] == 1
        if np.any(m & ((z < cfg.no_rain_below_dbz) | (z >= cfg.protected_dbz) | ~np.isfinite(z))):
            raise ValueError("near candidate crosses no-rain/strong/raw boundary")
    nonmet = evidence["NMR_NONMET_CANDIDATE_MASK"] == 1
    uncertain = evidence["NMR_LOW_SNR_UNCERTAIN_MASK"] == 1
    if np.any(nonmet & ((evidence["NMR_DR_AVAILABLE_MASK"] != 1) |
                       ~(evidence["NMR_DR_DB"] >= cfg.dr_nonmet_db) |
                       (evidence["NMR_POL_SAMPLE_COUNT"] < cfg.minimum_pol_samples) |
                       ~(evidence["NMR_NONMET_FRACTION"] >= cfg.nonmet_fraction))):
        raise ValueError("near nonmet lacks measured polarization evidence")
    snr = baseline.get("SNR_RAW", np.full(shape, np.nan))
    if np.any(uncertain & ((evidence["NMR_SNR_AVAILABLE_MASK"] != 1) |
                          ~np.isfinite(snr) | ~(snr < cfg.uncertainty_snr_db) |
                          ~(z < cfg.uncertainty_max_dbz) |
                          ~(evidence["NMR_WEATHER_FRACTION"] < cfg.maximum_weather_fraction))):
        raise ValueError("near uncertainty is not a measured low-SNR target")
    if np.any(nonmet & (~np.isfinite(snr) | ~(snr >= cfg.reliable_pol_snr_db))):
        raise ValueError("near polarization below reliability threshold")
    out, _ = apply(baseline, evidence, cfg, low_quality_flag=attrs["qc_near_measurement_low_quality_flag"])
    # Compare every existing numerical field as well as all stage evidence and delta
    # fields. This catches unintended QPE, flag, RAW or derived-field changes.
    for key, expected in out.items():
        if key not in group or not np.array_equal(expected, np.asarray(group[key][:]), equal_nan=True):
            raise ValueError("unexplained near-stage change: " + key)
    extra = {k for k in group if k.startswith("NMR_")} - set(out)
    if extra: raise ValueError("unexpected near-stage fields: " + ",".join(sorted(extra)))
