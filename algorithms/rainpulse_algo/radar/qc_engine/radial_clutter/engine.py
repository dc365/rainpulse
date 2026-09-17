"""Experimental nonmeteorological classification; no old actions are truth labels."""
import numpy as np

from ..narrow_source import narrow_source
from .features import features
from .geometry import measured, validate_native
from .rules import Reason, clutter_rules
from .shapes import propose_radials


def infer(n, cfg, *, prior=None, weather_support=None, context=None, source_reference=None, source_residual=None):
    validate_native(n, cfg.maximum_gates)
    obs = measured(n, "DBZH") & n.geometry_good[:, None]
    f = features(n, cfg)
    shape = propose_radials(n, cfg)
    rules = clutter_rules(n, cfg, f, prior, context)
    weather = f["local_weather"].copy()
    ext = np.zeros(n.shape, bool)
    if weather_support is not None:
        value = np.asarray(weather_support)
        if value.shape != n.shape:
            raise ValueError("weather support geometry differs")
        ext = np.isfinite(value) & (value >= 0.7)
        weather |= ext
    narrow = np.zeros(n.shape, bool)
    if source_reference is not None and source_residual is not None:
        narrow, _ = narrow_source(n, source_reference, source_residual, weather=weather)
    morphology = shape["RC1_RADIAL_CANDIDATE_MASK"] == 1
    radial = morphology & rules["polar"]
    radial |= narrow
    if not cfg.radial_enabled:
        radial[:] = False
    if not cfg.clutter_enabled:
        for key in ("ground", "ap", "sea", "bio"):
            rules[key] = np.zeros(n.shape, bool)
    votes = np.stack([rules["ground"], rules["ap"], rules["sea"], rules["bio"], radial]) & obs
    codes = np.array([2, 3, 4, 5, 6], "uint8")
    category = np.where(weather & obs, 1, 0).astype("uint8")
    for code, hit in zip(codes, votes, strict=True):
        category[hit] = code
    conflict = obs & ((votes.sum(axis=0) > 1) | (weather & votes.any(axis=0)))
    category[conflict] = 7
    # Biological and dynamic environmental candidates are diagnostic by default.
    propose = (rules["ground"] | radial) & obs & ~weather & ~conflict
    reason = np.zeros(n.shape, "uint32")
    pairs = [(morphology, Reason.MORPHOLOGY), (rules["polar"], Reason.RAW_POLAR_ANOMALY),
             (rules["historical"], Reason.STATIC_PRIOR), (rules["stationary"], Reason.DOPPLER_STATIONARY),
             (rules["rough"], Reason.ROUGH_TEXTURE), (rules["ap"], Reason.EXTERNAL_AP),
             (rules["sea"], Reason.MARINE_CONTEXT), (rules["bio"], Reason.BIOLOGICAL_CANDIDATE),
             (f["local_weather"], Reason.LOCAL_WEATHER), (ext, Reason.EXTERNAL_WEATHER),
             (~rules["prior_available"], Reason.MISSING_PRIOR), (~f["reliable"], Reason.INCOMPLETE_MEASUREMENT),
             (conflict, Reason.CONFLICT), (narrow, Reason.NARROW_SOURCE_MATCH)]
    for mask, bit in pairs:
        reason[mask & obs] |= int(bit)
    shape.update({"RC1_CLASS": category, "RC1_REASON": reason,
                  "RC1_PROPOSED_MASK": propose.astype("uint8"),
                  "RC1_PRIOR_AVAILABLE_MASK": rules["prior_available"].astype("uint8"),
                  "RC1_WEATHER_PROTECTED_MASK": (weather & obs).astype("uint8"),
                  "RC1_MODEL_CONFLICT_MASK": conflict.astype("uint8"),
                  "RC1_PHASE_INCREMENT_STD_DEG": f["phase_std"].astype("float32")})
    return shape
