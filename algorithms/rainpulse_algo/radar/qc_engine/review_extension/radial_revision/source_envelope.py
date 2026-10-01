"""Freeze raw nominated source envelopes before any weak-tail action.

Only original independent seeds confer lineage. A tail cannot advance either
the original object extent or the 120 km distance bound from those seeds.
"""
import numpy as np
from ..arrays import mask, moment, native_geometry, runs


def _corridor(rows, angles, i, beam, z, obs, blocked, r):
    row = rows[i]
    left = np.full(len(r), np.nan); right = left.copy()
    safe = ~blocked[rows[i-2:i+3]].any(axis=0)
    valid = obs[row] & ~blocked[row] & (z[row] >= 0.) & (r >= 2000.)
    for half in (beam, 2*beam, 3*beam):
        a = np.searchsorted(angles, angles[i]-half+1e-8, side='right')-1
        b = np.searchsorted(angles, angles[i]+half-1e-8, side='left')
        if a < 0 or b >= len(rows) or a >= i or b <= i or angles[b]-angles[a] > 8.:
            continue
        local_safe = ~blocked[rows[a:b+1]].any(axis=0)
        safe &= local_safe
        interior = (obs[rows[a+1:b]] & (z[rows[a+1:b]] >= z[row]-6.)).mean(axis=0) >= .6
        flanks = ((~obs[rows[a]] | (z[row]-z[rows[a]] >= 6.)) &
                  (~obs[rows[b]] | (z[row]-z[rows[b]] >= 6.)))
        accepted = valid & local_safe & interior & flanks & ~np.isfinite(left)
        left[accepted], right[accepted] = angles[a], angles[b]
    return valid & safe & np.isfinite(left), left, right, safe


def detect(native, blocked, seeds, nomination, *, beam_width=None):
    r, az, dr, good, gaps = native_geometry(native)
    z, obs = moment(native, 'DBZH')
    blocked = mask(blocked, native.shape, 'source envelope barriers') | ~good[:, None]
    seeds = mask(seeds, native.shape, 'original source seeds') & obs & ~blocked
    nomination = mask(nomination, native.shape, 'raw nominations') & obs & ~blocked
    hit = np.zeros(native.shape, bool)
    parent = np.zeros(native.shape, 'uint32')
    seed_id, raw_id = parent.copy(), parent.copy()
    evidence = {k: np.full(native.shape, np.nan, 'float32') for k in
                ('LEFT_DEG', 'RIGHT_DEG', 'START_M', 'END_M', 'ANCHOR_DISTANCE_M',
                 'ORIGINAL_LEFT_DEG', 'ORIGINAL_RIGHT_DEG', 'BEAM_PROXY_DEG')}
    object_id = 0
    for rows in np.split(np.arange(native.shape[0]), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 5:
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        if spacing <= 0:
            continue
        beam = max(spacing, beam_width or spacing)
        cache = {}
        def corridor(index):
            if index not in cache:
                cache[index] = _corridor(rows, angles, index, beam, z, obs, blocked, r)
            return cache[index]
        for i in range(2, len(rows)-2):
            row = rows[i]
            if not seeds[row].any():
                continue
            bounded, left, right, safe = corridor(i)
            frozen = (nomination[row] | seeds[row]) & bounded
            for lo, hi in runs(safe):
                fragments = [(a+lo, b+lo) for a,b in runs(frozen[lo:hi]) if (b-a)*dr >= 1000.]
                chains = []
                for a,b in fragments:
                    if (not chains or (a-chains[-1][-1][1])*dr > 60000. or
                            abs(left[a]-left[chains[-1][-1][1]-1]) > beam+1e-6 or
                            abs(right[a]-right[chains[-1][-1][1]-1]) > beam+1e-6):
                        chains.append([])
                    chains[-1].append((a,b))
                for chain in chains:
                    raw = np.concatenate([np.arange(a,b) for a,b in chain])
                    original = raw[seeds[row,raw]]
                    start, end = r[raw[0]], r[raw[-1]]+dr
                    width = np.maximum(beam, right[raw]-left[raw])
                    if (len(original)*dr < 10000. or end-start < 60000. or
                            (end-start)/max(dr, np.max(r[raw]*np.deg2rad(width))) < 8. or
                            np.ptp(left[raw]) > beam+1e-6 or np.ptp(right[raw]) > beam+1e-6):
                        continue
                    object_id += 1
                    seed_id[row,original] = object_id
                    raw_id[row,raw] = object_id
                    # These reference values are frozen once per original object.
                    ref_left, ref_right = np.median(left[raw]), np.median(right[raw])
                    # One-hop angular continuation from the ORIGINAL seed ray.
                    # Accepted neighbors never start another angular traversal.
                    neighbors = [j for j in range(2,len(rows)-2) if abs(angles[j]-angles[i]) <= beam+1e-6]
                    for j in neighbors:
                        rr = rows[j]
                        local, ll, hh, local_safe = corridor(j)
                        # A protected gap between the seed and a neighbor also
                        # blocks a link, even when the gap has no DBZH target.
                        combined_safe = safe & local_safe
                        section = next(((a,b) for a,b in runs(combined_safe) if a <= raw[0] and raw[-1] < b), None)
                        if section is None:
                            continue
                        lower, upper = section
                        gates = np.flatnonzero(local & (r >= max(r[lower], start-10000.)) &
                                               (r < min(r[upper-1]+dr, end+10000.)) & ~seeds[rr])
                        if not len(gates):
                            continue
                        pos = np.searchsorted(original, gates)
                        near1 = original[np.clip(pos, 0, len(original)-1)]
                        near2 = original[np.clip(pos-1, 0, len(original)-1)]
                        distance = np.minimum(abs(r[gates]-r[near1]), abs(r[gates]-r[near2]))
                        accept = ((distance <= 120000.) &
                                  (abs(ll[gates]-ref_left) <= beam+1e-6) &
                                  (abs(hh[gates]-ref_right) <= beam+1e-6))
                        chosen = gates[accept]
                        for fragment in np.split(chosen, np.flatnonzero(np.diff(chosen)>1)+1):
                            if len(fragment)*dr < 500.:
                                continue
                            selected = fragment[~hit[rr,fragment]]
                            hit[rr,selected] = True
                            parent[rr,selected] = object_id
                            evidence['LEFT_DEG'][rr,selected] = ll[selected]
                            evidence['RIGHT_DEG'][rr,selected] = hh[selected]
                            evidence['ORIGINAL_LEFT_DEG'][rr,selected] = ref_left
                            evidence['ORIGINAL_RIGHT_DEG'][rr,selected] = ref_right
                            evidence['BEAM_PROXY_DEG'][rr,selected] = beam
                            evidence['START_M'][rr,selected] = start
                            evidence['END_M'][rr,selected] = end
                            evidence['ANCHOR_DISTANCE_M'][rr,selected] = distance[np.searchsorted(gates,selected)]
    return {'RV2_ENVELOPE_MASK': hit.astype('uint8'),
            'RV2_ENVELOPE_PARENT_ID': parent, 'RV2_ENVELOPE_SEED_ID': seed_id,
            'RV2_ENVELOPE_RAW_OBJECT_ID': raw_id,
            **{'RV2_ENVELOPE_'+k: v for k,v in evidence.items()}}, {
                'version': 'frozen-raw-envelope-v2', 'objects': object_id,
                'gates': int(hit.sum()), 'filled_gates': 0, 'recursive_growth': False,
                'extent_padding_m': 10000., 'maximum_original_seed_distance_m': 120000.}
