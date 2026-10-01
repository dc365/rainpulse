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
    if "RV2_RAW_FAMILY_MASK" in group:
        from .raw_families import validate as validate_raw_families
        validate_raw_families(group, observed, barred | blocked)
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
    discontinuous = np.zeros(shape, bool)
    discontinuous_candidate = discontinuous.copy()
    if 'RV2_DISCONTINUOUS_MASK' in group:
        value = get('RV2_DISCONTINUOUS_MASK')
        if value.dtype != np.dtype('uint8'):
            raise ValueError('invalid discontinuous mask dtype')
        discontinuous = mask(value, shape, 'discontinuous tracks')
        discontinuous_candidate = discontinuous.copy()
        if 'RV2_DISCONTINUOUS_CANDIDATE_MASK' in group:
            value = get('RV2_DISCONTINUOUS_CANDIDATE_MASK')
            if value.dtype != np.dtype('uint8'):
                raise ValueError('invalid discontinuous candidate dtype')
            discontinuous_candidate = mask(value, shape, 'discontinuous candidates')
            measured = get('RV2_DISCONTINUOUS_MEASURED_MASK')
            windows = get('RV2_DISCONTINUOUS_WINDOW_BITS')
            if (measured.dtype != np.dtype('uint8') or windows.dtype != np.dtype('uint8') or windows.shape != shape or
                    not np.isin(windows, [0,1,2,3]).all()):
                raise ValueError('invalid measured window evidence')
            measured = mask(measured, shape, 'measured bilateral shoulders')
            if np.any(measured & ~discontinuous_candidate) or np.any(discontinuous & (~measured | (windows != 3))):
                raise ValueError('unanchored action lacks two measured window scales')
            edge = get('RV2_DISCONTINUOUS_EDGE_DB')
            if (edge.shape != shape or edge.dtype != np.dtype('float32') or np.isinf(edge).any() or
                    not np.array_equal(np.isfinite(edge), measured) or np.any(measured & (edge < 6.))):
                raise ValueError('unanchored contrast is not measured')
            for scale in (20,60):
                fraction = get('RV2_DISCONTINUOUS_WINDOW'+str(scale)+'_FRACTION')
                if (fraction.shape != shape or fraction.dtype != np.dtype('float32') or np.isinf(fraction).any() or
                        not np.array_equal(np.isfinite(fraction), discontinuous_candidate) or
                        np.any(discontinuous_candidate & ((fraction < 0.) | (fraction > 1.))) or
                        np.any(discontinuous & (fraction < .75-1e-6))):
                    raise ValueError('unanchored measured fraction lacks scale support')
        identity = get('RV2_DISCONTINUOUS_OBJECT_ID')
        if identity.shape != shape or identity.dtype != np.dtype('uint32'):
            raise ValueError('invalid discontinuous object identity')
        evidence = []
        for suffix in ('LEFT_DEG', 'RIGHT_DEG', 'SUPPORT_M', 'SPAN_M'):
            v = get('RV2_DISCONTINUOUS_'+suffix)
            if (v.shape != shape or v.dtype != np.dtype('float32') or np.isinf(v).any() or
                    not np.array_equal(np.isfinite(v), discontinuous_candidate)):
                raise ValueError('invalid discontinuous evidence')
            evidence.append(v)
        left, right, support, span = evidence
        if (not np.array_equal(identity > 0, discontinuous_candidate) or np.any(discontinuous & ~discontinuous_candidate) or
                np.any(discontinuous_candidate & (~observed | blocked | barred | (right <= left) |
                         (right-left > 3.000001) | (support < 8000.) | (span < 80000.) | (support > span)))):
            raise ValueError('discontinuous object lacks bounded geometry')
    envelope = np.zeros(shape, bool)
    seeds = np.zeros(shape, 'uint32')
    if 'RV2_ENVELOPE_MASK' in group:
        value = get('RV2_ENVELOPE_MASK')
        if value.dtype != np.dtype('uint8'):
            raise ValueError('invalid source envelope dtype')
        envelope = mask(value, shape, 'source envelope')
        parent, seeds, raw = (get('RV2_ENVELOPE_'+k) for k in ('PARENT_ID','SEED_ID','RAW_OBJECT_ID'))
        for value in (parent, seeds, raw):
            if value.shape != shape or value.dtype != np.dtype('uint32'):
                raise ValueError('invalid source envelope lineage')
        ev = []
        for suffix in ('LEFT_DEG','RIGHT_DEG','START_M','END_M','ANCHOR_DISTANCE_M'):
            value = get('RV2_ENVELOPE_'+suffix)
            if (value.shape != shape or value.dtype != np.dtype('float32') or np.isinf(value).any() or
                    not np.array_equal(np.isfinite(value), envelope)):
                raise ValueError('invalid source envelope bounds')
            ev.append(value)
        left, right, start, end, distance = ev
        if (not np.array_equal(parent > 0, envelope) or not np.isin(parent[envelope], seeds[seeds > 0]).all() or
                np.any(envelope & ((seeds > 0) | ~observed | blocked | barred | (right <= left) |
                         (right-left > 8.000001) | (start < 0.) | (end-start < 60000.) |
                         (distance < 0.) | (distance > 120000.))) or
                np.any((raw > 0) & (~observed | barred | blocked)) or
                np.any((seeds > 0) & (seeds != raw))):
            raise ValueError('source envelope lost original lineage or crossed barrier')
        if 'range' in group:
            r = get('range')[None,:]
            if np.any(envelope & ((r < start-10000.) | (r >= end+10000.))):
                raise ValueError('source envelope advanced beyond frozen bounds')
        if 'RV2_ENVELOPE_ORIGINAL_LEFT_DEG' in group:
            original_left, original_right, beam = (get('RV2_ENVELOPE_'+k) for k in
                ('ORIGINAL_LEFT_DEG','ORIGINAL_RIGHT_DEG','BEAM_PROXY_DEG'))
            for value in (original_left, original_right, beam):
                if (value.shape != shape or value.dtype != np.dtype('float32') or np.isinf(value).any() or
                        not np.array_equal(np.isfinite(value), envelope)):
                    raise ValueError('invalid original angular envelope')
            if np.any(envelope & ((beam <= 0.) | (original_right <= original_left) |
                         (abs(left-original_left) > beam+1e-4) | (abs(right-original_right) > beam+1e-4))):
                raise ValueError('source envelope advanced beyond original angular bounds')
    if 'RV2_INDEPENDENT_WEATHER_AVAILABLE_MASK' in group:
        value = get('RV2_INDEPENDENT_WEATHER_AVAILABLE_MASK')
        if value.dtype != np.dtype('uint8') or np.any(mask(value, shape, 'weather availability') & ~observed):
            raise ValueError('invalid independent weather availability')
    joint = np.zeros(shape,bool)
    if 'RV2_FAMILY_JOINT_QUALIFIED_MASK' in group:
        joint = mask(get('RV2_FAMILY_JOINT_QUALIFIED_MASK'),shape,'joint families')
    source_footprint=np.zeros(shape,bool)
    if 'RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK' in group:
        from .source_footprint import validate as validate_source_footprint
        validate_source_footprint(group,observed,barred|blocked)
        source_footprint=mask(get('RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'),shape,'source footprint')
    whole_morphology=np.zeros(shape,bool)
    if 'RV2_MORPH_OBJECT_STRONG_MASK' in group:
        from .morphology_objects import validate_serialized
        validate_serialized(group,observed,barred|blocked)
        whole_morphology=mask(get('RV2_MORPH_OBJECT_STRONG_MASK'),shape,'whole-object morphology')
    backbone=np.zeros(shape,bool)
    if 'RV2_BACKBONE_STRONG_MASK' in group:
        from .radial_backbone import validate_serialized as validate_backbone
        validate_backbone(group,observed,barred|blocked)
        backbone=mask(get('RV2_BACKBONE_STRONG_MASK'),shape,'radial backbone')
    fan_joint=np.zeros(shape,bool)
    if 'RV2_FAN_JOINT_QUALIFIED_MASK' in group:
        from .fan_joint import validate as validate_fan_joint
        validate_fan_joint(group,observed,barred|blocked)
        fan_joint=mask(get('RV2_FAN_JOINT_QUALIFIED_MASK'),shape,'fan joint qualification')
    if 'RV2_FAN_STATE_MATCH_MASK' in group:
        from .fan_states import validate as validate_fan_states
        validate_fan_states(group, observed, barred|blocked)
    source_window=np.zeros(shape,bool);window_candidate=source_window.copy()
    if 'RV2_SOURCE_WINDOW_QUALIFIED_MASK' in group:
        from .source_window import validate as validate_source_window
        validate_source_window(group,observed,barred|blocked)
        source_window=mask(get('RV2_SOURCE_WINDOW_QUALIFIED_MASK'),shape,'window source qualification')
        window_candidate=mask(get('RV2_SOURCE_WINDOW_CANDIDATE_MASK'),shape,'window source candidates')
    if not np.array_equal(candidate, (topology | bundle | line | group_candidate | residual_objects | discontinuous_candidate | envelope | joint | window_candidate | fan_joint | source_footprint | whole_morphology | backbone) & ~barred):
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
    if np.any((seeds > 0) & ~(legacy_source | line_source | morph | isolated | group_morph)):
        raise ValueError('envelope seed was not an original independent source')
    if 'RV2_SOURCE_LEDGER_SEED_MASK' in group:
        from .source_ledger import validate as validate_ledger
        original = mask(get('RV2_SOURCE_LEDGER_SEED_MASK'),shape,'complete original source')
        kinds = get('RV2_SOURCE_LEDGER_KIND')
        old_paths=(legacy_source,line_source,morph,isolated,group_morph)
        if (np.any(kinds>31) or np.any(original&~np.logical_or.reduce(old_paths)) or
            any(np.any(((kinds&bit)!=0)&~path) for bit,path in zip((1,2,4,8,16),old_paths))):
            raise ValueError('ledger kind acquired a new source path')
        if not np.array_equal(get('RV2_SOURCE_LEDGER_CANDIDATE_MASK'),get('RV2_RAW_FAMILY_MASK')):
            raise ValueError('ledger targets differ from RAW family nominations')
        validate_ledger(group,observed,barred|blocked,original)
    if 'RV2_RAW_FAN_MASK' in group:
        from .raw_fans import validate as validate_raw_fans
        validate_raw_fans(group,observed,barred|blocked,get('RV2_SOURCE_LEDGER_SEED_ID'))
    if 'RV2_FAMILY_JOINT_QUALIFIED_MASK' in group:
        from .family_joint import validate as validate_joint
        # The complete original-source mask is persisted separately; geometry
        # seeds must be old independent paths, never another joint member.
        original_joint_source = get('RV2_FAMILY_JOINT_ORIGINAL_SOURCE_MASK') == 1
        if np.any(original_joint_source & ~((legacy_source & (get('RV2_WEAK_CANDIDATE_MASK')==0)) | line_source | morph | isolated | group_morph)):
            raise ValueError('joint source was not an original independent path')
        validate_joint(group,observed,barred | blocked,original_joint_source)
    geometry = morph | isolated | group_polar | group_morph | residual_objects | discontinuous | envelope | joint | source_window | fan_joint | source_footprint | whole_morphology | backbone
    if not np.array_equal(qualified, legacy | segment | line_source | geometry):
        raise ValueError("radial qualification not equal to its source paths")
    weak_candidate = get("RV2_WEAK_CANDIDATE_MASK") == 1
    weak = get("RV2_WEAK_MATCH_MASK") == 1
    geometry_action = np.zeros(shape, bool)
    if 'RV2_GEOMETRY_ACTION_MASK' in group:
        value = get('RV2_GEOMETRY_ACTION_MASK')
        if value.dtype != np.dtype('uint8'):
            raise ValueError('invalid geometry action dtype')
        geometry_action = mask(value, shape, 'geometry action')
        if not np.array_equal(geometry_action, geometry & (mode == 1) & observed & ~barred & ~blocked):
            raise ValueError('geometry action differs from qualified geometry')
        expected = (((legacy | (segment & (allow == 1)) | line_source) & ~weak_candidate) | geometry_action) & (mode == 1)
    else:
        expected = ((legacy | (segment & (allow == 1)) | line_source | geometry) & ~weak_candidate) & (mode == 1)
    proposal = get("RV2_ACTION_PROPOSAL_MASK") == 1
    if not np.array_equal(proposal, expected):
        raise ValueError("radial action differs from explicit policy")
    if np.any(weak_candidate & segment) or np.any(proposal & weak_candidate & ~geometry_action):
        raise ValueError("weak hypothesis acquired an independent censor action")
    if np.any((get("RV2_LINKED_SEGMENT_MASK") == 1) & ~candidate):
        raise ValueError("identity links filled a noncandidate interval")
    if int(step.flat[0]) == 3 and not np.array_equal(get("RV2_OBJECT_ID") > 0, candidate):
        raise ValueError("raw fragment identity/support mismatch")
    if int(step.flat[0]) == 1 and (np.any(segment) or np.any(weak)):
        raise ValueError("step one may not use segmented references")
    return proposal
