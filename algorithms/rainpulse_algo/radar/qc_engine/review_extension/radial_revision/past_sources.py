"""Bounded historical source qualification using current measured polar evidence.

The input receipt is made by the read-only past Stage A audit. This does not
register a new source, interpolate missing moments, or change QC actions.
"""
import numpy as np
from ..arrays import mask, moment, native_geometry, runs

PREFIX = 'RV2_PAST_SOURCE_'


def qualify(native, blocked, parent_id, references):
    r, _, dr, good, _ = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    rho, rho_ok = moment(native, 'RHOHV')
    snr, snr_ok = moment(native, 'SNR')
    blocked = mask(blocked, native.shape, 'past source barriers') | ~good[:, None]
    parent = np.asarray(parent_id)
    if parent.shape != native.shape or parent.dtype != np.dtype('uint32'):
        raise ValueError('past source requires frozen current RAW parent IDs')
    counts = np.zeros(native.shape, 'uint8')
    exact = counts.copy()
    seen = set()
    for reference in references:
        if reference.get('status') != 'evaluated' or reference.get('graph_degraded'):
            continue
        identity = (reference['scan_id'], reference['sha256'])
        if identity in seen:
            raise ValueError('duplicate physical past source')
        seen.add(identity)
        if not reference.get('strictly_past') or len(reference['sha256']) != 64:
            raise ValueError('past source lacks causal input identity')
        bounded = np.zeros(native.shape, bool)
        for source in reference.get('bounded_sources', []):
            row = source['current_row']
            gates = np.asarray(source['original_gates'], dtype=int)
            targets = np.asarray(source['target_gates'], dtype=int)
            if (not 0 <= row < native.shape[0] or len(gates) == 0 or
                    np.any(gates < 0) or np.any(gates >= len(r)) or
                    np.any(np.diff(gates) <= 0) or np.any(np.diff(r[gates]) > 60000.) or
                    np.any(targets < 0) or np.any(targets >= len(r))):
                raise ValueError('invalid frozen original source gates')
            if (len(gates)*dr < 10000. or np.ptp(r[gates])+dr < 60000. or
                    len(np.unique((r[gates]//20000.).astype(int))) < 3 or
                    not np.isclose(source['support_m'], len(gates)*dr) or
                    not np.isclose(source['start_m'], r[gates[0]]) or
                    not np.isclose(source['end_m'], r[gates[-1]])):
                raise ValueError('past source support differs from original gates')
            if len(targets):
                distance = np.min(abs(r[targets, None]-r[gates][None, :]), axis=1)
                if (np.any(r[targets] < r[gates[0]]) or np.any(r[targets] > r[gates[-1]]) or
                        np.any(distance > 120000.)):
                    raise ValueError('target extends frozen original past source')
                bounded[row, targets] = True
        claims = np.zeros(native.shape, bool)
        claims.flat[reference.get('bounded_remaining_indices', [])] = True
        if not np.array_equal(claims, bounded):
            raise ValueError('past source bounds do not reproduce receipt')
        counts += bounded.astype('uint8')
        direct = np.zeros(native.shape, bool)
        direct.flat[reference.get('strong_remaining_indices', [])] = True
        exact += direct.astype('uint8')
    if len(seen) > 3:
        raise ValueError('at most three unique physical past observations')
    candidate = observed & ~blocked & (parent > 0) & (counts > 0)
    # Current polar measurements are mandatory, even with two past sources.
    measured = observed & rho_ok & snr_ok & (snr >= 10.) & ~blocked
    anomalous = measured & (rho <= .8)
    windows = np.zeros(native.shape, 'uint8')
    for row in np.flatnonzero(candidate.any(axis=1)):
        for lo, hi in runs(~blocked[row]):
            targets = np.flatnonzero(candidate[row, lo:hi])+lo
            for gate in targets:
                for bit, width in ((1, 20000.), (2, 60000.)):
                    local = (r >= r[gate]-width/2) & (r <= r[gate]+width/2)
                    local[:lo] = False; local[hi:] = False
                    local &= parent[row] == parent[row, gate]
                    obs_count = int((local & observed[row]).sum())
                    actual = local & measured[row]
                    support = int(actual.sum())
                    if (support >= max(5, int(width/dr*.25)) and
                            support >= max(1, obs_count)*.5 and
                            int((local & anomalous[row]).sum()) >= support*.7):
                        windows[row, gate] |= bit
    qualified = candidate & anomalous & (windows == 3)
    hold = np.zeros(native.shape, 'uint8')
    hold[candidate] = 1  # Current moments missing or not reliable.
    hold[candidate & measured & ~anomalous] = 2  # Current weather-like polar measurement.
    hold[candidate & anomalous & (windows != 3)] = 3  # Both physical windows not supported.
    hold[qualified] = 0
    out = {PREFIX+'CANDIDATE_MASK':candidate.astype('uint8'),
           PREFIX+'QUALIFIED_MASK':qualified.astype('uint8'),
           PREFIX+'WINDOW_BITS':windows, PREFIX+'HOLD_REASON':hold,
           PREFIX+'BOUNDED_PAST_COUNT':counts, PREFIX+'EXACT_PAST_COUNT':exact,
           PREFIX+'RAW_PARENT_ID':np.where(candidate, parent, 0).astype('uint32')}
    return out, {'version':'past-source-joint-v1', 'candidate_gates':int(candidate.sum()),
        'qualified_gates':int(qualified.sum()), 'actions':0, 'filled_gates':0,
        'source_claim':False, 'recursive_growth':False, 'references':len(seen),
        'current_polar_mandatory':True}
