import numpy as np
from .arrays import mask


def validate_source_fields(group, observed):
    shape = observed.shape
    required = {
        "SRC_REVIEW_NARROW_MASK": "uint8", "SRC_REVIEW_SOURCE_AVAILABLE_MASK": "uint8",
        "SRC_REVIEW_TARGET_MATCH_MASK": "uint8", "SRC_REVIEW_SOURCE_MATCH_MASK": "uint8",
        "SRC_REVIEW_QUALIFIED_MASK": "uint8", "SRC_REVIEW_WEATHER_PROTECTED_MASK": "uint8",
        "SRC_REVIEW_CONFLICT_MASK": "uint8", "SRC_REVIEW_RESIDUAL_DB": "float32",
        "SRC_REVIEW_REFERENCE_FOLD_ID": "uint32",
    }
    for key, dtype in required.items():
        if key not in group or group[key].shape != shape or group[key].dtype != np.dtype(dtype):
            raise ValueError(f"invalid source review field {key}")
    get = lambda key: np.asarray(group[key][:])
    for key in group:
        if key.startswith("SRC_REVIEW_") and key.endswith("_MASK"):
            value = mask(get(key), shape, key)
            if np.any(value & ~observed):
                raise ValueError("source review created observations")
    qualified = get("SRC_REVIEW_QUALIFIED_MASK") == 1
    source = get("SRC_REVIEW_SOURCE_MATCH_MASK") == 1
    available = get("SRC_REVIEW_SOURCE_AVAILABLE_MASK") == 1
    target = get("SRC_REVIEW_TARGET_MATCH_MASK") == 1
    residual = get("SRC_REVIEW_RESIDUAL_DB")
    if np.isinf(residual).any() or np.any(source & (~available | ~target | ~np.isfinite(residual) | (abs(residual) > 2.5))):
        raise ValueError("source measurement match lacks bounded held-out evidence")
    if np.any(available & (get("SRC_REVIEW_REFERENCE_FOLD_ID") == 0)):
        raise ValueError("available source reference lacks fold identity")
    morphology = get("SRC_REVIEW_NARROW_MASK") == 1
    if "SRC_REVIEW_CANDIDATE_MASK" in group:
        candidate = get("SRC_REVIEW_CANDIDATE_MASK") == 1
        if not np.array_equal(get("SRC_REVIEW_OBJECT_ID") > 0, candidate):
            raise ValueError("radial identity and original candidate differ")
        if np.any((get("SRC_REVIEW_LINKED_SEGMENT_MASK") == 1) & ~candidate):
            raise ValueError("identity links filled noncandidate gates")
        morphology |= candidate
    blocked = (get("SRC_REVIEW_WEATHER_PROTECTED_MASK") == 1) | (get("SRC_REVIEW_CONFLICT_MASK") == 1)
    expected = source & morphology & ~blocked
    if "RV2_MODE_CODE" in group:
        from .radial_revision.validation import validate_revision_fields
        expected |= validate_revision_fields(group, observed, source, blocked)
    if not np.array_equal(qualified, expected):
        raise ValueError("source qualification differs from evidence and barriers")
