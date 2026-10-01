"""Independent original-source power states. Matches are never QC actions."""
from types import SimpleNamespace
import numpy as np
from ..arrays import mask, moment, native_geometry, runs
from .fan_joint import PREFIX as JOINT, reference_model
from .source_ledger import PREFIX as LEDGER
from .raw_fans import PREFIX as FAN

PREFIX = 'RV2_FAN_STATE_'
FLOATS = ('INTERCEPT_DB', 'RESIDUAL_DB', 'REFERENCE_SUPPORT_M', 'REFERENCE_SPAN_M',
          'REFERENCE_BLOCK_COUNT', 'REFERENCE_P90_DB', 'REFERENCE_SPLIT_DELTA_DB',
          'SNR_LOW_DB', 'SNR_HIGH_DB')


def states(r, dr, z, snr, original, block):
    refs = original[(abs((r[original] // 20000).astype(int) - block) > 1) &
                    np.isfinite(snr[original]) & (snr[original] >= 3.)]
    power = z[refs] - 20 * np.log10(np.maximum(r[refs], 1.) / 50000.)
    order = np.argsort(power, kind='stable')
    cuts = np.flatnonzero(np.diff(power[order]) >= 6.) + 1
    # More than three modes is nonstationary, not a license for free fitting.
    if len(cuts) > 2:
        return []
    result = []
    for identity, indices in enumerate(np.split(order, cuts), 1):
        gates = refs[indices]
        model = reference_model(r, dr, z, gates, block)
        if model is None or model['REFERENCE_P90_DB'] > 2.5 or model['REFERENCE_SPLIT_DELTA_DB'] > 1.5:
            continue
        low, high = np.percentile(snr[gates], [5, 95])
        result.append((identity, {**model, 'SNR_LOW_DB': float(low), 'SNR_HIGH_DB': float(high)}))
    return result


def diagnose(native, blocked, group):
    r, _, dr, good, _ = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    snr, snr_ok = moment(native, 'SNR')
    barred = mask(blocked, native.shape, 'power state barriers') | ~good[:, None]
    seed = np.asarray(group[LEDGER + 'SEED_ID'])
    family = np.asarray(group[FAN + 'ID'])
    candidate = (family > 0) & (seed == 0) & observed & ~barred
    out = {PREFIX + k: np.zeros(native.shape, 'uint8') for k in ('CANDIDATE_MASK', 'MATCH_MASK')}
    out.update({PREFIX + k: np.zeros(native.shape, 'uint32') for k in ('SOURCE_ID', 'STATE_ID')})
    out[PREFIX + 'HOLD_REASON'] = np.where(candidate, 1, 0).astype('uint16')
    out.update({PREFIX + k: np.full(native.shape, np.nan, 'float32') for k in
                (*FLOATS, 'ORIGINAL_SOURCE_SNR', 'TARGET_SNR')})
    out[PREFIX + 'CANDIDATE_MASK'][:] = candidate
    out[PREFIX + 'ORIGINAL_SOURCE_SNR'][(seed > 0) & snr_ok] = snr[(seed > 0) & snr_ok]
    out[PREFIX + 'TARGET_SNR'][candidate & snr_ok] = snr[candidate & snr_ok]
    out[PREFIX + 'HOLD_REASON'][candidate & (~snr_ok | (snr < 3.))] = 2
    for row in np.flatnonzero(candidate.any(axis=1)):
        for lo, hi in runs(~barred[row]):
            targets = np.flatnonzero(candidate[row, lo:hi] & snr_ok[row, lo:hi] & (snr[row, lo:hi] >= 3.)) + lo
            for family_id in np.unique(family[row, targets]):
                local = targets[family[row, targets] == family_id]
                sources = np.unique(seed[row, (family[row] == family_id) & (seed[row] > 0)])
                for source in sources:
                    original = np.flatnonzero(seed[row, lo:hi] == source) + lo
                    if not len(original):
                        continue
                    # Distance uses original measured seeds, never a newly matched tail.
                    pos = np.searchsorted(original, local)
                    distance = np.minimum(abs(r[local] - r[original[np.clip(pos, 0, len(original)-1)]]),
                                          abs(r[local] - r[original[np.clip(pos-1, 0, len(original)-1)]]))
                    local_near = local[distance <= 120000.]
                    for block in np.unique((r[local_near] // 20000).astype(int)):
                        use = local_near[(r[local_near] // 20000).astype(int) == block]
                        models = states(r, dr, z[row], snr[row], original, block)
                        pending = use[(out[PREFIX+'STATE_ID'][row, use] == 0) & (out[PREFIX+'HOLD_REASON'][row, use] != 32)]
                        out[PREFIX+'HOLD_REASON'][row, pending] = 4 if models else 3
                        for identity, model in models:
                            residual = z[row, use] - 20*np.log10(np.maximum(r[use], 1.)/50000.) - model['INTERCEPT_DB']
                            matched = (abs(residual) <= 2.5) & (snr[row, use] >= model['SNR_LOW_DB']-2.) & (snr[row, use] <= model['SNR_HIGH_DB']+2.)
                            gates = use[matched]
                            ambiguous = (out[PREFIX+'STATE_ID'][row, gates] > 0) | (out[PREFIX+'HOLD_REASON'][row, gates] == 32)
                            accept = gates[~ambiguous]
                            out[PREFIX+'SOURCE_ID'][row, accept] = source
                            out[PREFIX+'STATE_ID'][row, accept] = identity
                            out[PREFIX+'HOLD_REASON'][row, accept] = 0
                            for key, value in model.items():
                                out[PREFIX+key][row, accept] = value
                            out[PREFIX+'RESIDUAL_DB'][row, accept] = residual[matched][~ambiguous]
                            conflict = gates[ambiguous]
                            out[PREFIX+'HOLD_REASON'][row, conflict] = 32
                            out[PREFIX+'SOURCE_ID'][row, conflict] = 0
                            out[PREFIX+'STATE_ID'][row, conflict] = 0
                            for key in FLOATS:
                                out[PREFIX+key][row, conflict] = np.nan
    out[PREFIX+'MATCH_MASK'][:] = out[PREFIX+'STATE_ID'] > 0
    return out, {'version': 'fan-power-states-v1', 'candidate_gates': int(candidate.sum()),
                 'matched_gates': int(out[PREFIX+'MATCH_MASK'].sum()), 'actions': 0,
                 'hold_counts': {str(i):int((candidate & (out[PREFIX+'HOLD_REASON'] == i)).sum()) for i in (1,2,3,4,32)},
                 'source_claim': False, 'recursive_growth': False}


def validate(group, observed, blocked):
    get = lambda k: np.asarray(group[k][:])
    seed = get(LEDGER+'SEED_ID')
    family = get(FAN+'ID')
    c = (family > 0) & (seed == 0) & observed & ~blocked
    source_z = get(JOINT+'ORIGINAL_SOURCE_DBZH')
    target_z = get(JOINT+'TARGET_DBZH')
    ranges = np.full(observed.shape[1], np.nan)
    coords = np.where(seed > 0, get(LEDGER+'RANGE_M'), get(JOINT+'RANGE_M'))
    for gate in np.flatnonzero(np.isfinite(coords).any(axis=0)):
        values = coords[:, gate]
        ranges[gate] = values[np.isfinite(values)][0]
    spacing = get(JOINT+'SPACING_M')[c]
    if not len(spacing):
        spacing = get(LEDGER+'SPACING_M')[seed > 0]
    dr = float(spacing[0]) if len(spacing) else 1000.
    ix = np.flatnonzero(np.isfinite(ranges))
    if len(ix):
        ranges = ranges[ix[0]] + dr*(np.arange(len(ranges))-ix[0])
    else:
        ranges = dr*(np.arange(len(ranges))+1.)
    original_snr, target_snr = get(PREFIX+'ORIGINAL_SOURCE_SNR'), get(PREFIX+'TARGET_SNR')
    for values, allowed in ((original_snr, seed > 0), (target_snr, c)):
        if values.shape != observed.shape or values.dtype != np.dtype('float32') or np.any(np.isfinite(values) & ~allowed):
            raise ValueError('power state SNR outside original measurement')
    z = np.where(seed > 0, source_z, target_z)
    snr = np.where(seed > 0, original_snr, target_snr)
    native = SimpleNamespace(shape=observed.shape, ranges=ranges,
        azimuth=np.arange(observed.shape[0], dtype=float), geometry_good=np.ones(observed.shape[0], bool),
        gap_after=np.zeros(observed.shape[0], bool), fields={'DBZH':z, 'SNR':snr},
        field_available={'DBZH':np.isfinite(z), 'SNR':np.isfinite(snr)})
    expected, _ = diagnose(native, blocked, group)
    for key, value in expected.items():
        actual = get(key)
        if actual.shape != value.shape or actual.dtype != value.dtype:
            raise ValueError('invalid power state field')
        if np.issubdtype(value.dtype, np.floating):
            same = np.allclose(actual, value, rtol=1e-5, atol=.02, equal_nan=True)
        else:
            same = np.array_equal(actual, value)
        if not same:
            raise ValueError('power state differs from guarded original references')
