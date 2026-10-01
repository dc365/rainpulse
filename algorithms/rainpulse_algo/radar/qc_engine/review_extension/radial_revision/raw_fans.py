"""Frozen wide RAW families. Diagnostic ownership is never action eligibility."""
import numpy as np
from ..arrays import mask, moment, native_geometry, runs

PREFIX = 'RV2_RAW_FAN_'
FLOATS = ('RANGE_M', 'SPACING_M', 'START_M', 'END_M', 'SUPPORT_M',
          'LEFT_DEG', 'RIGHT_DEG', 'REFERENCE_LEFT_DEG', 'REFERENCE_RIGHT_DEG',
          'BEAM_DEG', 'OCCUPANCY', 'LEFT_AVAILABLE_FRACTION', 'RIGHT_AVAILABLE_FRACTION')


def detect(native, blocked, original_seed_id, *, beam_width=None):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    blocked = mask(blocked, native.shape, 'RAW fan barriers') | ~good[:, None]
    seed = np.asarray(original_seed_id)
    if seed.shape != native.shape or seed.dtype != np.dtype('uint32'):
        raise ValueError('RAW fan requires original ledger identity')
    valid = observed & ~blocked & (z >= 0.) & (r[None, :] >= 2000.)
    out = {PREFIX+'MASK': np.zeros(native.shape, 'uint8'),
           PREFIX+'ID': np.zeros(native.shape, 'uint32'),
           PREFIX+'ORIGINAL_SEED_ID': np.zeros(native.shape, 'uint32'),
           PREFIX+'HOLD_REASON': np.zeros(native.shape, 'uint16')}
    out.update({PREFIX+k: np.full(native.shape, np.nan, 'float32') for k in FLOATS})
    objects = []; ambiguous = 0
    block_ids = (r//20000.).astype(int)
    for rows in np.split(np.arange(len(az)), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 2: continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        if spacing <= 0: continue
        beam = max(spacing, beam_width or spacing)
        tracks = []
        for block in np.unique(block_ids):
            cols = np.flatnonzero(block_ids == block)
            count = valid[np.ix_(rows, cols)].sum(axis=1)
            occupied = (count >= 2) & (count*dr >= 500.) & good[rows]
            # A protected angular strip in this range block splits RAW bands.
            safe_rows = ~blocked[np.ix_(rows, cols)].any(axis=1) & good[rows]
            occupied &= safe_rows
            # Preserve a short measured interior ray enclosed by ORIGINAL
            # supported neighbours. This is geometry only, never gap filling.
            original_occupied = occupied.copy()
            occupied[1:-1] |= original_occupied[:-2] & original_occupied[2:] & safe_rows[1:-1]
            for a, b in runs(occupied):
                if b-a < 2: continue
                left, right = angles[a]-spacing/2, angles[b-1]+spacing/2
                if not 2.-1e-6 <= right-left <= 90.+1e-6: continue
                candidates = []
                for track in tracks:
                    if track['last_block'] == block or block-track['last_block'] > 3: continue
                    if abs(left-track['left']) > 2*beam+1e-6 or abs(right-track['right']) > 2*beam+1e-6: continue
                    stencil = rows[min(a, track['a']):max(b, track['b'])]
                    bridge = (r >= track['last_end']) & (r < r[cols[-1]]+dr)
                    if blocked[np.ix_(stencil, np.flatnonzero(bridge))].any(): continue
                    candidates.append(track)
                if len(candidates) == 1:
                    track = candidates[0]
                else:
                    ambiguous += int(len(candidates) > 1)
                    track = {'id': len(objects)+1, 'left': left, 'right': right,
                             'a': a, 'b': b, 'entries': [], 'last_block': block,
                             'last_end': r[cols[0]]}
                    tracks.append(track); objects.append(track)
                rr, cc = np.where(valid[np.ix_(rows[a:b], cols)])
                rr, cc = rows[a:b][rr], cols[cc]
                track['entries'].append((rr, cc))
                track['last_block'], track['last_end'] = block, r[cols[-1]]+dr
                identity = track['id']
                out[PREFIX+'ID'][rr, cc] = identity
                values = {'RANGE_M': r[cc], 'SPACING_M': dr, 'LEFT_DEG': left,
                          'RIGHT_DEG': right, 'REFERENCE_LEFT_DEG': track['left'],
                          'REFERENCE_RIGHT_DEG': track['right'], 'BEAM_DEG': beam,
                          'OCCUPANCY': count[np.searchsorted(rows, rr)]/len(cols),
                          'LEFT_AVAILABLE_FRACTION': float(observed[rows[a-1], cols].mean()) if a else 0.,
                          'RIGHT_AVAILABLE_FRACTION': float(observed[rows[b], cols].mean()) if b < len(rows) else 0.}
                for key, value in values.items(): out[PREFIX+key][rr, cc] = value
    records = []
    for track in objects:
        rr = np.concatenate([x[0] for x in track['entries']])
        cc = np.concatenate([x[1] for x in track['entries']])
        start, end = float(r[cc].min()), float(r[cc].max()+dr)
        support = len(cc)*dr
        for key, value in (('START_M', start), ('END_M', end), ('SUPPORT_M', support)):
            out[PREFIX+key][rr, cc] = value
        original = np.unique(seed[rr, cc]); original = original[original > 0]
        records.append({'id': track['id'], 'start_m': start, 'end_m': end,
                        'original_source_ids': original.tolist(), 'blocks': len(track['entries'])})
    nominated = out[PREFIX+'ID'] > 0
    out[PREFIX+'MASK'][:] = nominated
    out[PREFIX+'HOLD_REASON'][nominated] = 1
    out[PREFIX+'ORIGINAL_SEED_ID'][nominated] = seed[nominated]
    return out, {'version': 'raw-fans-v1', 'objects': len(objects), 'records': records,
                 'candidate_gates': int(nominated.sum()), 'ambiguous_new_objects': ambiguous,
                 'action_gates': 0, 'filled_gates': 0, 'source_claim': False, 'recursive_growth': False}


def validate(group, observed, blocked, original_seed_id):
    get = lambda k: np.asarray(group[PREFIX+k][:])
    shape = observed.shape
    for key, dtype in (('MASK', 'uint8'), ('ID', 'uint32'), ('ORIGINAL_SEED_ID', 'uint32'), ('HOLD_REASON', 'uint16')):
        if get(key).shape != shape or get(key).dtype != np.dtype(dtype):
            raise ValueError('invalid RAW fan identity/mask')
    candidate = mask(get('MASK'), shape, 'RAW fan candidates')
    if not np.array_equal(candidate, get('ID') > 0) or np.any(candidate & (~observed | blocked)):
        raise ValueError('RAW fan observation/identity crossed barrier')
    if not np.array_equal(get('HOLD_REASON'), candidate.astype('uint16')):
        raise ValueError('RAW fan diagnostic lost independent qualification hold')
    if not np.array_equal(get('ORIGINAL_SEED_ID'), np.where(candidate, original_seed_id, 0)):
        raise ValueError('RAW fan promoted fragment to source')
    for key in FLOATS:
        v = get(key)
        if v.shape != shape or v.dtype != np.dtype('float32') or not np.array_equal(np.isfinite(v), candidate):
            raise ValueError('invalid RAW fan physical evidence')
    if np.any(candidate & ((get('SPACING_M') <= 0) | (get('BEAM_DEG') <= 0) |
            (get('RIGHT_DEG')-get('LEFT_DEG') < 2.-1e-4) | (get('RIGHT_DEG')-get('LEFT_DEG') > 90.0001) |
            (abs(get('LEFT_DEG')-get('REFERENCE_LEFT_DEG')) > 2*get('BEAM_DEG')+1e-4) |
            (abs(get('RIGHT_DEG')-get('REFERENCE_RIGHT_DEG')) > 2*get('BEAM_DEG')+1e-4))):
        raise ValueError('RAW fan expanded beyond original angular bounds')
    for key in ('OCCUPANCY', 'LEFT_AVAILABLE_FRACTION', 'RIGHT_AVAILABLE_FRACTION'):
        if np.any(candidate & ((get(key) < 0) | (get(key) > 1))):
            raise ValueError('RAW fan occupancy/availability invalid')
    for identity in np.unique(get('ID')[candidate]):
        use = get('ID') == identity
        for key in ('START_M', 'END_M', 'SUPPORT_M', 'REFERENCE_LEFT_DEG', 'REFERENCE_RIGHT_DEG', 'BEAM_DEG'):
            if np.ptp(get(key)[use]) > .01: raise ValueError('RAW fan original extent differs within parent')
        dr = get('SPACING_M')[use]
        if (not np.allclose(get('SUPPORT_M')[use], dr.sum(), atol=.1) or
            not np.allclose(get('START_M')[use], get('RANGE_M')[use].min(), atol=.01) or
            not np.allclose(get('END_M')[use], (get('RANGE_M')[use]+dr).max(), atol=.01)):
            raise ValueError('RAW fan frozen support/range differs from observed gates')
