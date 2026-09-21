"""Numerical invariants of a shared source route, no weakening of parent checks."""
import numpy as np
from .source_family import Reason


def check_family_evidence(e, observed, cfg):
    m = lambda k: e['RDR_'+k] == 1
    ref = m('FAMILY_REFERENCE_MASK'); attempted = m('FAMILY_ATTEMPTED_MASK')
    full, partial = m('FULL_MATCH_MASK'), m('PARTIAL_MATCH_MASK')
    votes = e['RDR_FAMILY_MATCH_COUNT']; states = e['RDR_FAMILY_STATE_ID']
    reason = e['RDR_FAMILY_REASON']; fc = cfg.source_family
    has = lambda bit: (reason & int(bit)) != 0
    if np.any(ref & (m('FAMILY_PARENT_AVAILABLE_MASK') | ~attempted | ~m('MODEL_AVAILABLE_MASK'))):
        raise ValueError('family route took over an existing receiver model')
    if not np.array_equal(e['RDR_FAMILY_OBJECT_ID'] > 0, ref):
        raise ValueError('family reference/object index differs')
    if np.any(ref & (e['RDR_FAMILY_OBJECT_ID'] != e['RDR_MODEL_ID'])):
        raise ValueError('family model link differs')
    if cfg.segment_reference is not None and np.any(ref & m('SEGMENT_REFERENCE_MASK')):
        raise ValueError('family overwrote finite-state parent model')
    if (np.any(ref & ((e['RDR_FAMILY_DONOR_COUNT'] < fc.minimum_donors) |
                      (e['RDR_FAMILY_DONOR_COUNT'] > fc.maximum_donors))) or
            np.any(e['RDR_FAMILY_SAME_RANGE_DONORS'] > e['RDR_FAMILY_DONOR_COUNT'])):
        raise ValueError('family donor count invalid')
    supported = m('FAMILY_SIDE_MEASURED_MASK') & ~m('TARGET_SIDE_CONFLICT_MASK') & (e['RDR_FAMILY_SAME_RANGE_DONORS'] >= fc.minimum_donors)
    if not np.array_equal(m('FAMILY_CURRENT_SUPPORT_MASK'), ref & supported):
        raise ValueError('family current measured support differs')
    matched = ref & (full | partial)
    if (not np.array_equal(matched, ref & (votes == 1)) or np.any(matched & ~supported)
            or np.any(votes > fc.maximum_states) or np.any(states > fc.maximum_states)
            or not np.array_equal(states > 0, matched)):
        raise ValueError('family state is ambiguous or lacks measured support')
    distance = e['RDR_FAMILY_REFERENCE_DISTANCE_M']
    if np.any(matched & (~np.isfinite(distance) | (distance < 0))) or np.any(~matched & np.isfinite(distance)):
        raise ValueError('family own reference distance invalid')
    if (not np.array_equal(has(Reason.ATTEMPTED), attempted) or np.any(~attempted & (reason != 0))
            or not np.array_equal(has(Reason.FAMILY_REFERENCE), ref)
            or not np.array_equal(has(Reason.FULL_MATCH), full & ref)
            or not np.array_equal(has(Reason.PARTIAL_MATCH), partial & ref)):
        raise ValueError('family per-gate reason accounting differs')
    risk = ref & m('TARGET_POWER_MATCH_MASK') & ~m('SOURCE_MASK')
    if not np.array_equal(m('FAMILY_UNRESOLVED_MASK'), risk):
        raise ValueError('family unresolved source risk differs')
    known_bits = sum(int(v) for v in Reason)
    if np.any(reason.astype('uint64') & np.uint64(~known_bits & 0xffffffff)):
        raise ValueError('unknown source-family reason bit')
    if not np.array_equal(has(Reason.SOURCE_SUPPORTED), ref & m('SOURCE_MASK')):
        raise ValueError('family source reason missing')
    hard = m('INDEPENDENT_WEATHER_MASK') | m('UNKNOWN_PROTECTION_MASK')
    if not np.array_equal(has(Reason.INDEPENDENT_OR_UNKNOWN_WEATHER), ref & hard):
        raise ValueError('family weather reason differs')


def validate_family_records(records, s, cfg):
    """Validate native-order references before serializing acquisition-order IDs."""
    import hashlib
    from ..data import json_bytes
    from .source_family import _indices
    fc = cfg.source_family
    for record in records:
        if record.get('reference_route') != 'shared_coherent_source_family':
            continue
        content = {k: v for k,v in record.items() if k not in ('id', 'reference_sha256')}
        if hashlib.sha256(json_bytes(content)).hexdigest() != record['reference_sha256']:
            raise ValueError('source-family reference content changed')
        target = record['ray']; block = record['target_block']
        donors = record['donors']; rays = [m['ray'] for m in donors]
        if not fc.minimum_donors <= len(set(rays)) <= fc.maximum_donors or target in rays:
            raise ValueError('invalid/recursive source-family donors')
        for m in donors:
            if target in [x['ray'] for x in m['shoulders']]:
                raise ValueError('target ray leaked into donor shoulders')
            for key in ('snr_intervals', 'pair_intervals', 'polar_intervals'):
                ii = _indices(m[key])
                if np.any(abs((s.ranges[ii]//cfg.block_m).astype(int)-block) <= cfg.guard_blocks):
                    raise ValueError('target or guard leaked into donor training')
        for state in record['states']:
            ii = _indices(state['reference_intervals'])
            if np.any(abs((s.ranges[ii]//cfg.block_m).astype(int)-block) <= cfg.guard_blocks):
                raise ValueError('target or guard leaked into own amplitude training')
            if len(ii)*s.dr < fc.minimum_own_support_m or abs(state['ray_bias_db']) > fc.maximum_ray_bias_db:
                raise ValueError('invalid own-state physical support/bias')
            if len(state['cross_predictions']) != 2:
                raise ValueError('source family lacks reciprocal block prediction')
            for check in state['cross_predictions']:
                if set(check['train_blocks']) & set(check['validation_blocks']):
                    raise ValueError('own-state cross prediction leaked')
                if check['snr_prediction_p90_db'] > cfg.maximum_snr_p90_db or check['relation_prediction_p90_db'] > cfg.maximum_relation_error_db:
                    raise ValueError('own-state prediction fails contract')
