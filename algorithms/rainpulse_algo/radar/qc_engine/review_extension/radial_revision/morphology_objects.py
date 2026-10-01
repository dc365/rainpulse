"""Whole-object RAW morphology with frozen angular templates.

This module exposes measured shape evidence, not an automatic QC action. It
does not fit receiver power, require an existing rejected source, fill missing
gates, or allow a surviving fragment to expand an object's original envelope.
"""
import numpy as np
from ..arrays import mask, moment, native_geometry, runs
from .geometry import ResourceLimit

PREFIX = 'RV2_MORPH_OBJECT_'
SCALES_M = (5000., 10000., 20000.)
LEVELS_DBZ = (0., 10., 20., 35., 55.)


def _shoulders(rows, a, b, columns, z, observed, snr, snr_valid, blocked):
    """Bilateral measured contrast, with unknown distinct from measured noise."""
    body = z[np.ix_(rows[a:b], columns)]
    present = observed[np.ix_(rows[a:b], columns)]
    centre = np.divide(np.where(present, body, 0.).sum(axis=0), present.sum(axis=0),
                       out=np.full(len(columns), np.nan), where=present.sum(axis=0) > 0)
    known = np.ones(len(columns), bool)
    clear = np.ones(len(columns), bool)
    for k in (a-1, b):
        if k < 0 or k >= len(rows):
            return 0., 0.
        row = rows[k]
        safe = ~blocked[row, columns]
        dbzh_known = observed[row, columns]
        # Missing DBZH is not zero. Only an actual low-SNR measurement can
        # provide a noise-side observation at that coordinate.
        noise = snr_valid[row, columns] & (snr[row, columns] <= 3.)
        known &= safe & (dbzh_known | noise)
        clear &= safe & ((dbzh_known & (z[row, columns] <= centre-6.)) |
                         (~dbzh_known & noise))
    return float(known.mean()), float(clear.mean())


def detect(native, blocked, *, beam_width=None, maximum_objects=10000):
    if beam_width is not None and (not np.isfinite(beam_width) or beam_width <= 0.):
        raise ValueError('positive finite antenna beam width required')
    if not isinstance(maximum_objects, int) or maximum_objects < 1:
        raise ValueError('positive integer morphology object budget required')
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    snr, snr_valid = moment(native, 'SNR')
    rho, rho_valid = moment(native, 'RHOHV')
    blocked = mask(blocked, native.shape, 'morphology object barriers') | ~good[:, None]
    raw = observed & ~blocked & (z >= 0.) & (r[None, :] >= 2000.)
    weather = raw & rho_valid & snr_valid & (rho >= .95) & (snr >= 10.)
    out = {PREFIX+'MASK': np.zeros(native.shape, 'uint8'),
           PREFIX+'STRONG_MASK': np.zeros(native.shape, 'uint8'),
           PREFIX+'ID': np.zeros(native.shape, 'uint32'),
           PREFIX+'WEATHER_VETO_MASK': weather.astype('uint8')}
    records = []
    segments = []
    for segment in np.split(np.arange(len(az)), np.flatnonzero(gaps[:-1])+1):
        for a, b in runs(good[segment]):
            if b-a >= 3:
                segments.append((segment[a:b], False))
    # Only an explicitly gap-free, physically adjacent seam can be circular.
    # This second representation handles the seam band and its shoulders only;
    # it cannot join sector endpoints or manufacture missing scanning coverage.
    step = float(np.median((np.diff(az)+360.) % 360.)) if len(az) > 2 else 0.
    seam_step = float((az[0]-az[-1]) % 360.)
    if not gaps[-1] and good[0] and good[-1] and 0. < seam_step <= 1.5*step:
        tail = np.flatnonzero((az[-1]-az+360.) % 360. <= 91.)
        head = np.flatnonzero((az-az[0]+360.) % 360. <= 91.)
        circular = np.r_[tail, head]
        if len(np.unique(circular)) == len(circular):
            for segment in np.split(circular, np.flatnonzero(gaps[circular[:-1]])+1):
                for a, b in runs(good[segment]):
                    piece = segment[a:b]
                    if len(piece) >= 3 and np.any(np.diff(piece) < 0):
                        segments.append((piece, True))
    for scale in SCALES_M:
        block_ids = (r//scale).astype(int)
        for level in LEVELS_DBZ:
            for rows, seam_only in segments:
                angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
                spacing = float(np.median(np.diff(angles)))
                if spacing <= 0. or np.any(np.diff(angles) <= 0.):
                    continue
                beam = max(spacing, beam_width or spacing)
                tracks = []
                for block in np.unique(block_ids):
                    columns = np.flatnonzero(block_ids == block)
                    signal = raw[np.ix_(rows, columns)] & (z[np.ix_(rows, columns)] >= level)
                    count = signal.sum(axis=1)
                    safe_rows = ~blocked[np.ix_(rows, columns)].any(axis=1)
                    occupied = safe_rows & (count >= 2) & (count*dr >= 500.)
                    for a, b in runs(occupied):
                        if seam_only and not np.any(np.diff(rows[max(0, a-1):min(len(rows), b+1)]) < 0):
                            continue
                        left = angles[a]-spacing/2.
                        right = angles[b-1]+spacing/2.
                        if right-left > 90.:
                            continue
                        matches = []
                        for track in tracks:
                            if not 0 < block-track['last_block'] <= 2:
                                continue
                            if max(abs(left-track['left']), abs(right-track['right'])) > .5*beam+1e-6:
                                continue
                            bridge = np.flatnonzero((r >= track['last_end']) & (r <= r[columns[-1]]))
                            stencil = rows[max(0, track['a']-1):min(len(rows), track['b']+1)]
                            if blocked[np.ix_(stencil, bridge)].any():
                                continue
                            matches.append(track)
                        if len(matches) == 1:
                            track = matches[0]
                        else:
                            if len(records)+len(tracks) >= maximum_objects:
                                raise ResourceLimit('whole-object morphology budget exceeded; no partial result')
                            track = {'a': a, 'b': b, 'left': left, 'right': right,
                                     'entries': [], 'last_block': block, 'last_end': r[columns[0]]}
                            tracks.append(track)
                        # The initial raw boundary is immutable. A later band
                        # cannot nominate gates outside it, even within tolerance.
                        domain_rows = rows[max(a, track['a']):min(b, track['b'])]
                        rr, cc = np.where(raw[np.ix_(domain_rows, columns)])
                        rr, cc = domain_rows[rr], columns[cc]
                        anchor = z[rr, cc] >= level
                        anchor_cols = np.unique(cc[anchor])
                        if not len(anchor_cols):
                            continue
                        known, clear = _shoulders(rows, track['a'], track['b'], anchor_cols,
                                                  z, observed, snr, snr_valid, blocked)
                        track['entries'].append({'rr': rr, 'cc': cc, 'anchor_cols': anchor_cols,
                                                 'known': known, 'clear': clear,
                                                 'left': left, 'right': right})
                        track['last_block'] = block
                        track['last_end'] = r[columns[-1]]+dr
                for track in tracks:
                    entries = track['entries']
                    if not entries:
                        continue
                    anchor_cols = np.unique(np.concatenate([e['anchor_cols'] for e in entries]))
                    start, end = float(r[anchor_cols[0]]), float(r[anchor_cols[-1]]+dr)
                    support, span = float(len(anchor_cols)*dr), end-start
                    width = track['right']-track['left']
                    aspect = span/max(end*np.deg2rad(width), dr)
                    kind = 'line' if width <= 2.*beam+1e-6 else 'fan'
                    known = float(np.mean([e['known'] for e in entries]))
                    clear = float(np.mean([e['clear'] for e in entries]))
                    confirmed = sum(e['known'] >= .8 and e['clear'] >= .8 for e in entries)
                    holds = []
                    if kind == 'line':
                        # Short tracks require denser support, larger aspect,
                        # and stricter repeated measured shoulders than long ones.
                        shape = (span >= 80000. and support >= 10000. and len(entries) >= 4 and aspect >= 3.)
                        short = (span >= 20000. and support >= .5*span and len(entries) >= 4 and aspect >= 6.)
                        shape |= short
                    else:
                        shape = span >= 100000. and support >= 20000. and len(entries) >= 6
                    if not shape:
                        holds.append('insufficient_object_geometry')
                    if known < .8:
                        holds.append('unknown_shoulders')
                    if clear < .8 or confirmed < .8*len(entries):
                        holds.append('insufficient_repeated_edge_contrast')
                    rr = np.concatenate([e['rr'] for e in entries])
                    cc = np.concatenate([e['cc'] for e in entries])
                    # Membership stays in the raw anchor range: weak gates in
                    # the first/last block cannot stretch the original extent.
                    inside = (r[cc] >= start) & (r[cc] < end)
                    rr, cc = rr[inside], cc[inside]
                    identity = len(records)+1
                    strong = not holds
                    prior = out[PREFIX+'STRONG_MASK'][rr, cc] == 1
                    choose = ~prior
                    out[PREFIX+'ID'][rr[choose], cc[choose]] = identity
                    out[PREFIX+'MASK'][rr, cc] = 1
                    if strong:
                        accept = ~weather[rr, cc]
                        # An object's aggregate score never authorizes an
                        # unknown/contaminated target segment. Recheck actual
                        # shoulders against EACH measured target, not the
                        # brighter band's average or another range window.
                        for k in (track['a']-1, track['b']):
                            if k < 0 or k >= len(rows):
                                accept[:] = False
                                break
                            side = rows[k]
                            dbzh_known = observed[side, cc]
                            noise = snr_valid[side, cc] & (snr[side, cc] <= 3.)
                            accept &= ~blocked[side, cc] & (
                                (dbzh_known & (z[side, cc] <= z[rr, cc]-6.)) |
                                (~dbzh_known & noise))
                        out[PREFIX+'STRONG_MASK'][rr[accept], cc[accept]] = 1
                        out[PREFIX+'ID'][rr[accept], cc[accept]] = identity
                    records.append({'id': identity, 'kind': kind, 'scale_m': scale, 'level_dbz': level,
                                    'start_m': start, 'end_m': end, 'span_m': span, 'support_m': support,
                                    'left_deg': track['left'], 'right_deg': track['right'], 'width_deg': width,
                                    'radial_aspect': float(aspect), 'windows': len(entries),
                                    'measured_shoulders_fraction': known, 'clear_shoulders_fraction': clear,
                                    'confirmed_windows': confirmed, 'strong': strong, 'holds': holds,
                                    'member_gates': len(rr), 'weather_veto_gates': int(weather[rr, cc].sum()),
                                    'edge_excursion_deg': float(max(max(abs(e['left']-track['left']),
                                                                          abs(e['right']-track['right'])) for e in entries))})
    return out, {'version': 'native-morphology-objects-v1', 'objects': records,
                 'candidate_gates': int(out[PREFIX+'MASK'].sum()),
                 'strong_evidence_gates': int(out[PREFIX+'STRONG_MASK'].sum()),
                 'action_gates': 0, 'source_claim': False, 'filled_gates': 0,
                 'recursive_growth': False, 'independent_weather_truth': False}


def validate(arrays, native, blocked, *, beam_width=None, maximum_objects=10000):
    """Validate evidence by replaying original measurements, never trusted IDs."""
    expected, _ = detect(native, blocked, beam_width=beam_width, maximum_objects=maximum_objects)
    if set(arrays) != set(expected):
        raise ValueError('whole-object evidence field set differs')
    for key, value in expected.items():
        actual = np.asarray(arrays[key])
        if actual.dtype != value.dtype or actual.shape != value.shape or not np.array_equal(actual, value):
            raise ValueError('whole-object evidence does not match original measurements: '+key)
