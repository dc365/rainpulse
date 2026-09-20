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
    line = np.zeros(shape, bool)
    if "RV2_LINE_MASK" in group:
        value = get("RV2_LINE_MASK")
        if value.dtype != np.dtype('uint8'):
            raise ValueError("invalid fragment line dtype")
        line = mask(value, shape, "fragment line")
        if np.any(line & (~observed | barred | blocked)):
            raise ValueError("fragment line crossed an observation/barrier")
    group_candidate = np.zeros(shape, bool)
    group_polar = np.zeros(shape, bool)
    group_morph = np.zeros(shape, bool)
    if 'RV2_GROUP_MASK' in group:
        values = []
        for key in ('RV2_GROUP_MASK', 'RV2_GROUP_POLAR_MASK'):
            value = get(key)
            if value.dtype != np.dtype('uint8'):
                raise ValueError('invalid group evidence dtype')
            values.append(mask(value, shape, key))
        group_candidate, group_polar = values
        if np.any(group_candidate & (~observed | barred | blocked)) or np.any(group_polar & ~group_candidate):
            raise ValueError('group evidence crossed barrier or candidate')
    if 'RV2_GROUP_MORPH_MASK' in group:
        value = get('RV2_GROUP_MORPH_MASK')
        if value.dtype != np.dtype('uint8'):
            raise ValueError('invalid group morphology dtype')
        group_morph = mask(value, shape, 'group morphology')
        if np.any(group_morph & ~group_candidate):
            raise ValueError('group morphology lacks candidate')
    if 'RV2_POWER_FAN_MASK' in group:
        value = get('RV2_POWER_FAN_MASK')
        residual = get('RV2_POWER_FAN_RESIDUAL_DB')
        if value.dtype != np.dtype('uint8') or residual.dtype != np.dtype('float32') or residual.shape != shape:
            raise ValueError('invalid power fan evidence dtype/shape')
        fan = mask(value, shape, 'power fan')
        if (np.any(fan & ~group_morph) or np.isinf(residual).any() or
                np.any(fan & (~np.isfinite(residual) | (abs(residual) > 6.)))):
            raise ValueError('power fan lacks morphology or measured residual')
    residual_objects = np.zeros(shape, bool)
    if 'RV2_RESIDUAL_LINK_MASK' in group:
        link = mask(get('RV2_RESIDUAL_LINK_MASK'), shape, 'residual links')
        direct = mask(get('RV2_RESIDUAL_DIRECT_MASK'), shape, 'residual direct')
        residual_objects = link | direct
        for key in ('RV2_RESIDUAL_LINK_MASK', 'RV2_RESIDUAL_DIRECT_MASK'):
            if get(key).dtype != np.dtype('uint8'): raise ValueError('invalid residual mask dtype')
        for suffix in ('LEFT_DEG', 'RIGHT_DEG', 'SCALE_M', 'ANCHOR_DISTANCE_M'):
            value = get('RV2_RESIDUAL_'+suffix)
            if value.shape != shape or value.dtype != np.dtype('float32') or np.isinf(value).any():
                raise ValueError('invalid residual diagnostics')
        left, right = get('RV2_RESIDUAL_LEFT_DEG'), get('RV2_RESIDUAL_RIGHT_DEG')
        distance = get('RV2_RESIDUAL_ANCHOR_DISTANCE_M')
        identity = get('RV2_RESIDUAL_OBJECT_ID')
        if identity.shape != shape or identity.dtype != np.dtype('uint32'):
            raise ValueError('invalid residual object identity')
        if (np.any(residual_objects & (~observed | blocked | barred | ~np.isfinite(left) | ~np.isfinite(right) |
                              (right <= left) | (right-left > 8.00001))) or
            np.any(link & ((identity == 0) | ~np.isfinite(distance) | (distance < 0) | (distance > 40000.))) or
            np.any(residual_objects & ~np.isin(get('RV2_RESIDUAL_SCALE_M'), [12000.,30000.,60000.]))):
            raise ValueError('residual object crossed barrier or lacks bounded evidence')
        if 'RV2_RESIDUAL_TRACK_MASK' in group:
            track = mask(get('RV2_RESIDUAL_TRACK_MASK'), shape, 'residual track')
            parent = get('RV2_RESIDUAL_TRACK_PARENT_ID')
            anchors = get('RV2_RESIDUAL_ANCHOR_ID')
            if get('RV2_RESIDUAL_TRACK_MASK').dtype != np.dtype('uint8'):
                raise ValueError('invalid track mask dtype')
            for value in (parent, anchors):
                if value.shape != shape or value.dtype != np.dtype('uint32'):
                    raise ValueError('invalid track lineage dtype')
            if (np.any(track & ~link) or np.any(track & (parent == 0)) or
                np.any((anchors > 0) & (~observed | blocked | barred)) or
                not np.isin(parent[track], anchors[anchors > 0]).all()):
                raise ValueError('track lacks frozen anchor lineage')
    if not np.array_equal(candidate, (topology | bundle | line | group_candidate | residual_objects) & ~barred):
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
    line_source = np.zeros(shape, bool)
    if 'RV2_LINE_SOURCE_MASK' in group:
        value = get('RV2_LINE_SOURCE_MASK')
        if value.dtype != np.dtype('uint8'):
            raise ValueError('invalid line source dtype')
        line_source = mask(value, shape, 'line source')
        span_line = get('RV2_LINE_REFERENCE_SPAN_M')
        fold_line = get('RV2_LINE_FOLD_ID')
        if (span_line.shape != shape or span_line.dtype != np.dtype('float32') or
                fold_line.shape != shape or fold_line.dtype != np.dtype('uint32') or
                np.isinf(span_line).any()):
            raise ValueError('invalid line reference fields')
        if np.any(line_source & (~line | blocked | ~observed | ~np.isfinite(span_line) | (span_line < 60000) | (fold_line == 0))):
            raise ValueError('line source lacks held-out measured support')
    morph = np.zeros(shape, bool)
    if 'RV2_LINE_MORPH_MASK' in group:
        value = get('RV2_LINE_MORPH_MASK')
        if value.dtype != np.dtype('uint8'):
            raise ValueError('invalid line morphology dtype')
        morph = mask(value, shape, 'line morphology')
        edge = get('RV2_LINE_EDGE_DB')
        if edge.shape != shape or edge.dtype != np.dtype('float32') or np.isinf(edge).any():
            raise ValueError('invalid measured line edge')
        if np.any(morph & (~line | blocked | barred | ~observed | ~np.isfinite(edge) | (edge < 6.))):
            raise ValueError('direct morphology lacks measured bilateral edge')
    isolated = np.zeros(shape, bool)
    if 'RV2_LINE_ISOLATED_MASK' in group:
        masks = []
        for key in ('RV2_LINE_ISOLATED_MASK', 'RV2_LINE_EMPTY_FLANK_MASK'):
            value = get(key)
            if value.dtype != np.dtype('uint8'):
                raise ValueError('invalid isolated line dtype')
            masks.append(mask(value, shape, key))
        isolated, support = masks
        if np.any(support & (~line | blocked | barred | ~observed)) or np.any(isolated & ~support):
            raise ValueError('isolated line lacks raw geometric support')
    for prefix in ('RV2_WINDOW_', 'RV2_TRACK_'):
        left_key, right_key = prefix+'LEFT_DEG', prefix+'RIGHT_DEG'
        if left_key not in group:
            continue
        left, right = get(left_key), get(right_key)
        for value in (left, right):
            if value.shape != shape or value.dtype != np.dtype('float32') or np.isinf(value).any():
                raise ValueError('invalid track boundary')
        support = np.isfinite(left)
        if (not np.array_equal(support, np.isfinite(right)) or
                np.any(support & (~group_morph | (right <= left) | (right-left > 8.000001)))):
            raise ValueError('track boundaries lack morphology support')
        if prefix == 'RV2_WINDOW_':
            for suffix in ('SCALE_M','LEFT_MISSING_FRACTION','RIGHT_MISSING_FRACTION','BEAM_PROXY_DEG'):
                value=get(prefix+suffix)
                if (value.shape != shape or value.dtype != np.dtype('float32') or
                        np.isinf(value).any() or not np.array_equal(np.isfinite(value),support)):
                    raise ValueError('invalid window evidence')
                if suffix.endswith('FRACTION') and np.any(support & ((value < -1e-6)|(value > 1.000001))):
                    raise ValueError('invalid missing fraction')
    if not np.array_equal(qualified, legacy | segment | line_source | morph | isolated | group_polar | group_morph | residual_objects):
        raise ValueError("radial qualification not equal to its source paths")
    expected = (legacy | (segment & (allow == 1)) | line_source | morph | isolated | group_polar | group_morph | residual_objects) & (mode == 1)
    proposal = get("RV2_ACTION_PROPOSAL_MASK") == 1
    if not np.array_equal(proposal, expected):
        raise ValueError("radial action differs from explicit policy")
    weak = get("RV2_WEAK_MATCH_MASK") == 1
    if np.any(weak & segment) or np.any(proposal & weak & ~legacy & ~line_source & ~morph & ~isolated & ~group_polar & ~group_morph):
        raise ValueError("weak hypothesis acquired an independent censor action")
    if np.any((get("RV2_LINKED_SEGMENT_MASK") == 1) & ~candidate):
        raise ValueError("identity links filled a noncandidate interval")
    if int(step.flat[0]) == 3 and not np.array_equal(get("RV2_OBJECT_ID") > 0, candidate):
        raise ValueError("raw fragment identity/support mismatch")
    if int(step.flat[0]) == 1 and (np.any(segment) or np.any(weak)):
        raise ValueError("step one may not use segmented references")
    return proposal
