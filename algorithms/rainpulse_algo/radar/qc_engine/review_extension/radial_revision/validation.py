"""Validate persisted new diagnostics before they can support an action."""
import numpy as np
from ..arrays import mask
from .engine import DTYPES


def validate_revision_fields(group, observed, legacy_source, blocked):
    shape = observed.shape
    for key, dtype in DTYPES.items():
        if key not in group or group[key].shape != shape or group[key].dtype != np.dtype(dtype):
            raise ValueError(f"invalid radial revision field {key}")
    get = lambda key: np.asarray(group[key][:])
    for key, dtype in DTYPES.items():
        a = get(key)
        if key.endswith("_MASK"):
            m = mask(a, shape, key)
            if np.any(m & ~observed):
                raise ValueError("radial revision created observations")
        if dtype == "float32" and np.isinf(a).any():
            raise ValueError("infinite radial diagnostic")
    mode = get("RV2_MODE_CODE")
    step = get("RV2_STEP_CODE")
    allow = get("RV2_SEGMENT_ACTION_ENABLED")
    if any(np.unique(x).size != 1 for x in (mode, step, allow)) or np.any(mode > 1) or np.any(allow > 1) or not np.isin(step, [1, 2, 3]).all():
        raise ValueError("inconsistent radial revision mode/step")
    candidate = get("RV2_CANDIDATE_MASK") == 1
    barred = get("RV2_BARRED_MASK") == 1
    topology = get("RV2_TOPOLOGY_MASK") == 1
    bundle = get("RV2_BUNDLE_MASK") == 1
    if not np.array_equal(candidate, (topology | bundle) & ~barred):
        raise ValueError("radial candidate differs from raw evidence")
    if np.any(candidate & blocked) or np.any((get("RV2_PLATEAU_MASK") == 1) & candidate):
        raise ValueError("radial candidate crossed a barrier")
    legacy = get("RV2_LEGACY_MATCH_MASK") == 1
    segment = get("RV2_SEGMENT_MATCH_MASK") == 1
    if not np.array_equal(legacy, candidate & legacy_source & ~blocked & ~barred):
        # A complete resource abstention is all-zero even if legacy references exist.
        if np.any(candidate) or np.any(legacy):
            raise ValueError("radial legacy support is not the held-out intersection")
    family = get("RV2_STATE_FAMILY")
    residual = get("RV2_SEGMENT_RESIDUAL_DB")
    if np.any(family > 3):
        raise ValueError("unknown radial source family")
    if np.any(segment & (~candidate | (family != 1) | (get("RV2_MODEL_ID") == 0) |
                         (get("RV2_SEGMENT_FOLD_ID") == 0) | (get("RV2_FIT_AVAILABLE_MASK") == 0) |
                         ~np.isfinite(residual) | (abs(residual) > 2.5))):
        raise ValueError("segmented action lacks strict held-out source measurement")
    span = get("RV2_REFERENCE_MAX_M")-get("RV2_REFERENCE_MIN_M")
    if np.any(segment & (~np.isfinite(span) | (span < 100000))):
        raise ValueError("segmented reference span is unsupported")
    if np.any(segment & (get("RV2_RANGE_TERM_MEASURED_MASK") == 0)):
        raise ValueError("segmented action lacks measured processing calibration")
    if np.any(segment & (get("RV2_AMBIGUOUS_STATE_MASK") == 1)):
        raise ValueError("ambiguous target state used as a source")
    qualified = get("RV2_QUALIFIED_MASK") == 1
    if not np.array_equal(qualified, legacy | segment):
        raise ValueError("radial qualification not equal to its source paths")
    expected = (legacy | (segment & (allow == 1))) & (mode == 1)
    proposal = get("RV2_ACTION_PROPOSAL_MASK") == 1
    if not np.array_equal(proposal, expected):
        raise ValueError("radial action differs from explicit policy")
    weak = get("RV2_WEAK_MATCH_MASK") == 1
    if np.any(weak & segment) or np.any(proposal & weak & ~legacy):
        raise ValueError("weak hypothesis acquired an independent censor action")
    if np.any((get("RV2_LINKED_SEGMENT_MASK") == 1) & ~candidate):
        raise ValueError("identity links filled a noncandidate interval")
    if int(step.flat[0]) == 3 and not np.array_equal(get("RV2_OBJECT_ID") > 0, candidate):
        raise ValueError("raw fragment identity/support mismatch")
    if int(step.flat[0]) == 1 and (np.any(segment) or np.any(weak)):
        raise ValueError("step one may not use segmented references")
    return proposal
