"""Physical multiscale edges and bounded links to frozen independent anchors.

Missing shoulders never count as measured contrast. They may bound an anchored
object geometrically, but cannot nominate an independent object. Output contains
observed gates only; neither gaps nor newly accepted fragments become anchors.
"""
import numpy as np
from scipy.ndimage import uniform_filter1d
from ..arrays import mask, moment, native_geometry, runs


def detect(native, blocked, source, *, beam_width=None):
    r, az, dr, good, gaps = native_geometry(native)
    z, obs = moment(native, 'DBZH')
    blocked = mask(blocked, native.shape, 'residual barriers') | ~good[:, None]
    anchors = mask(source, native.shape, 'frozen anchors') & obs & ~blocked
    valid = obs & ~blocked & (z >= 0) & (r[None, :] >= 2000)
    shape = native.shape
    linked = np.zeros(shape, bool); direct = linked.copy()
    ids = np.zeros(shape, 'uint32')
    evidence = {k: np.full(shape, np.nan, 'float32') for k in
                ('LEFT_DEG', 'RIGHT_DEG', 'SCALE_M', 'ANCHOR_DISTANCE_M')}
    object_id = 0
    # Split angular gaps explicitly; the seam is conservatively left unlinked.
    for rows in np.split(np.arange(shape[0]), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 3: continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        if spacing <= 0: continue
        beam = beam_width or spacing
        for index in range(1, len(rows)-1):
            row = rows[index]
            if not valid[row].any(): continue
            # The physical window length also determines the allowed shape ratio.
            for scale, contrast, ratio in ((12000., 9., 12.), (30000., 8., 10.), (60000., 6., 8.)):
                size = max(3, int(round(scale/dr))) | 1
                avg = lambda a: uniform_filter1d(a.astype(float), size, mode='constant')
                for half in sorted(set((beam, 2*beam, 3*beam))):
                    a = np.searchsorted(angles, angles[index]-half, side='right')-1
                    b = np.searchsorted(angles, angles[index]+half, side='left')
                    if a < 0 or b >= len(rows) or a >= index or b <= index: continue
                    lo, hi = rows[a], rows[b]
                    width = angles[b]-angles[a]
                    if width > 8 or not good[rows[a:b+1]].all(): continue
                    safe = ~np.any(blocked[rows[a:b+1]], axis=0)
                    left = obs[lo] & (z[row]-z[lo] >= contrast)
                    right = obs[hi] & (z[row]-z[hi] >= contrast)
                    # At least 80% of the window has measured bilateral contrast;
                    # a retained gate itself still needs both measured edges.
                    measured = valid[row] & safe & left & right
                    width_m = np.maximum(dr, r*np.deg2rad(width))
                    independent = measured & (avg(measured) >= .8) & (scale/width_m >= ratio)
                    fresh = independent & ~direct[row]
                    direct[row] |= independent
                    evidence['LEFT_DEG'][row, fresh] = angles[a]
                    evidence['RIGHT_DEG'][row, fresh] = angles[b]
                    evidence['SCALE_M'][row, fresh] = scale
                    # Frozen same-direction source must have >=10 km contiguous
                    # support. A barrier cuts both the source and its extensions.
                    bounded = valid[row] & safe & (left | ~obs[lo]) & (right | ~obs[hi])
                    for begin, end in runs(safe & ~blocked[row]):
                        fixed = np.zeros(end-begin, bool)
                        for x, y in runs(anchors[row, begin:end]):
                            if (y-x)*dr >= 10000.: fixed[x:y] = True
                        ai = np.flatnonzero(fixed)+begin
                        if not len(ai): continue
                        gi = np.flatnonzero(bounded[begin:end])+begin
                        if not len(gi): continue
                        pos = np.searchsorted(ai, gi)
                        distance = np.minimum(abs(r[gi]-r[ai[np.clip(pos, 0, len(ai)-1)]]),
                                              abs(r[gi]-r[ai[np.clip(pos-1, 0, len(ai)-1)]]))
                        take = gi[(distance <= min(40000., max(12000., scale))) & ~anchors[row, gi]]
                        # Do not accept arbitrary single-gate speckles.
                        for frag in np.split(take, np.flatnonzero(np.diff(take)>1)+1):
                            if len(frag)*dr < 2000.: continue
                            new = frag[~linked[row, frag]]
                            if not len(new): continue
                            object_id += 1
                            linked[row, new] = True; ids[row, new] = object_id
                            evidence['LEFT_DEG'][row, new] = angles[a]
                            evidence['RIGHT_DEG'][row, new] = angles[b]
                            evidence['SCALE_M'][row, new] = scale
                            evidence['ANCHOR_DISTANCE_M'][row, new] = distance[np.searchsorted(gi,new)]
    track, parents, anchor_ids, tracked = _track(native, z, obs, valid, blocked, anchors, beam_width)
    fresh = track & ~linked
    linked |= track
    ids[fresh] = parents[fresh] + object_id
    for key in evidence:
        evidence[key][fresh] = tracked[key][fresh]
    return {'RV2_RESIDUAL_TRACK_MASK': track.astype('uint8'),
            'RV2_RESIDUAL_TRACK_PARENT_ID': parents,
            'RV2_RESIDUAL_ANCHOR_ID': anchor_ids,
            'RV2_RESIDUAL_LINK_MASK': linked.astype('uint8'),
            'RV2_RESIDUAL_DIRECT_MASK': direct.astype('uint8'),
            'RV2_RESIDUAL_OBJECT_ID': ids,
            **{'RV2_RESIDUAL_'+k: v for k,v in evidence.items()}}, {
                'version': 'residual-objects-v2', 'tracked_gates': int(track.sum()), 'linked_gates': int(linked.sum()),
                'direct_gates': int(direct.sum()), 'filled_gates': 0,
                'anchor_policy': 'frozen_independent_no_recursive_growth'}



def _track(native, z, obs, valid, blocked, anchors, beam_width):
    """One-hop variable-width tracks from frozen native-ray anchor segments."""
    r, az, dr, good, gaps = native_geometry(native)
    hit = np.zeros(native.shape, bool)
    parents = np.zeros(native.shape, 'uint32')
    anchor_ids = parents.copy()
    evidence = {k: np.full(native.shape, np.nan, 'float32') for k in
                ('LEFT_DEG', 'RIGHT_DEG', 'SCALE_M', 'ANCHOR_DISTANCE_M')}
    parent_id = 0
    step = max(1, int(round(5000. / dr)))
    reach = int(40000. / dr)
    for rows in np.split(np.arange(native.shape[0]), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 3:
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        beam = max(spacing, beam_width or spacing)
        if spacing <= 0:
            continue
        for i, row in enumerate(rows):
            for start, stop in runs(anchors[row]):
                if (stop-start)*dr < 10000.:
                    continue
                parent_id += 1
                anchor_ids[row,start:stop] = parent_id
                for direction in (-1, 1):
                    endpoint = start if direction < 0 else stop-1
                    previous = (angles[i]-spacing/2, angles[i]+spacing/2)
                    for offset in range(1, reach+1, step):
                        if direction > 0:
                            begin, end = endpoint+offset, min(len(r), endpoint+offset+step)
                        else:
                            begin, end = max(0, endpoint-offset-step+1), endpoint-offset+1
                        if begin < 0 or end > len(r) or begin >= end:
                            break
                        occupancy = valid[rows, begin:end].mean(axis=1)
                        bands = []
                        for a, b in runs(occupancy >= .4):
                            if a == 0 or b == len(rows):
                                continue
                            left, right = angles[a]-spacing/2, angles[b-1]+spacing/2
                            center = (left+right)/2
                            if (right-left > min(8., 3*beam) or
                                abs(center-angles[i]) > beam+1e-6 or
                                max(abs(left-previous[0]), abs(right-previous[1])) > beam+1e-6):
                                continue
                            bands.append((abs(center-angles[i]), a, b, left, right))
                        if not bands:
                            # No geometry is inferred through a gap. Keep the last boundaries.
                            continue
                        _, a, b, left, right = min(bands)
                        corridor = rows[min(i,a):max(i,b-1)+1]
                        lo, hi = min(endpoint,begin), max(endpoint+1,end)
                        if blocked[corridor, lo:hi].any():
                            break
                        gates = np.arange(begin,end)
                        distance = abs(r[gates]-r[endpoint])
                        inside = valid[rows[a:b], begin:end] & ~anchors[rows[a:b], begin:end]
                        flank_l, flank_r = rows[a-1], rows[b]
                        # Missing flanks only bound a source-supported track; measured
                        # flanks must be weaker. No source means no tracking branch.
                        inside &= ((~obs[flank_l,begin:end] | (z[rows[a:b],begin:end]-z[flank_l,begin:end] >= 6.)) &
                                   (~obs[flank_r,begin:end] | (z[rows[a:b],begin:end]-z[flank_r,begin:end] >= 6.)))
                        inside &= distance[None,:] <= 40000.
                        if inside.sum()*dr < 2000.:
                            continue
                        previous = (left,right)
                        for j, rr in enumerate(rows[a:b]):
                            selected = gates[inside[j] & ~hit[rr,gates]]
                            if not len(selected):
                                continue
                            hit[rr,selected] = True
                            parents[rr,selected] = parent_id
                            evidence['LEFT_DEG'][rr,selected] = left
                            evidence['RIGHT_DEG'][rr,selected] = right
                            evidence['SCALE_M'][rr,selected] = 60000.
                            evidence['ANCHOR_DISTANCE_M'][rr,selected] = abs(r[selected]-r[endpoint])
    return hit, parents, anchor_ids, evidence
