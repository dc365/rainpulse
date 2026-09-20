"""Physical multiscale edges and bounded links to frozen independent anchors.

Missing shoulders never count as measured contrast. They may bound an anchored
object geometrically, but cannot nominate an independent object. Output contains
observed gates only; neither gaps nor newly accepted fragments become anchors.
"""
import numpy as np
from scipy.ndimage import uniform_filter1d
from ..arrays import mask, moment, native_geometry, runs


def detect(native, blocked, source, *, beam_width=None, span_minimum_m=None,
           span_flank_rays=3, span_flank_delta_db=3., span_flank_fraction=.7,
           span_weather_snr_db=25., span_weather_fraction=.6):
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
    span, span_ids, span_evidence, span_objects = _span(
        native, z, obs, valid, blocked, beam_width,
        minimum_m=span_minimum_m, flank_rays=span_flank_rays,
        flank_delta_db=span_flank_delta_db, flank_fraction=span_flank_fraction,
        weather_snr_db=span_weather_snr_db, weather_fraction=span_weather_fraction)
    fresh_span = span & ~linked
    linked |= span
    ids[fresh_span] = span_ids[fresh_span] + object_id
    for key in evidence:
        evidence[key][fresh_span] = span_evidence[key][fresh_span]
    object_id += span_objects
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
            'RV2_RESIDUAL_SPAN_MASK': fresh_span.astype('uint8'),
            'RV2_RESIDUAL_OBJECT_ID': ids,
            **{'RV2_RESIDUAL_'+k: v for k,v in evidence.items()}}, {
                'version': 'residual-objects-v2', 'tracked_gates': int(track.sum()), 'linked_gates': int(linked.sum()),
                'direct_gates': int(direct.sum()), 'span_gates': int(fresh_span.sum()),
                'span_objects': int(span_objects), 'filled_gates': 0,
                'anchor_policy': 'frozen_independent_no_recursive_growth'}



def _span(native, z, obs, valid, blocked, beam_width, *, minimum_m=None, flank_rays=3,
          flank_delta_db=3., flank_fraction=.7, weather_snr_db=25., weather_fraction=.6):
    """Whole-ray isolation: long thin runs with missing or clearly weaker flanks.

    A radial interference line is not bounded by a short evidence window: it
    extends over tens of kilometres while both neighbouring rays stay empty or
    much weaker, so the frozen anchor reach never has to be enough on its own.
    """
    r, az, dr, good, gaps = native_geometry(native)
    hit = np.zeros(native.shape, bool)
    object_ids = np.zeros(native.shape, 'uint32')
    evidence = {k: np.full(native.shape, np.nan, 'float32') for k in
                ('LEFT_DEG', 'RIGHT_DEG', 'SCALE_M', 'ANCHOR_DISTANCE_M')}
    objects = 0
    if minimum_m is None or minimum_m <= 0:
        return hit, object_ids, evidence, objects
    minimum_gates = max(3, int(round(minimum_m/dr)))
    bridge = max(0, int(round(2000./dr)))
    snr, snr_available = moment(native, 'SNR')
    peer_delta = 3.
    for rows in np.split(np.arange(native.shape[0]), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 3:
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        if spacing <= 0:
            continue
        beam = max(spacing, beam_width or spacing)
        # The inspected corridor must stay inside the frozen eight degree width.
        reach = int(min(flank_rays, max(1, np.floor(4./beam))))
        for index in range(1, len(rows)-1):
            row = rows[index]
            line = valid[row].copy()
            if not line.any():
                continue
            if bridge:
                line = _bridge(line, bridge)
            for begin, end in runs(line):
                keep = valid[row, begin:end]
                if int(keep.sum()) < minimum_gates:
                    continue
                gates = np.arange(begin, end)
                centre = z[row, begin:end]
                # A neighbour carrying the same echo makes this a bundle, not an
                # isolated single-ray line, and bundles keep their own evidence.
                peers = False
                for offset in (-1, 1):
                    other = rows[index+offset]
                    peer = obs[other, begin:end] & (z[other, begin:end] >= centre - peer_delta)
                    if peer[keep].mean() > .5:
                        peers = True
                        break
                if peers:
                    continue
                weak = [np.zeros(end-begin, bool), np.zeros(end-begin, bool)]
                boundary = [np.nan, np.nan]
                complete = True
                for offset in range(1, reach+1):
                    for side, delta in ((0, -offset), (1, offset)):
                        other = index+delta
                        if other < 0 or other >= len(rows):
                            complete = False
                            continue
                        neighbour = rows[other]
                        weak[side] |= (~obs[neighbour, begin:end] |
                                       (centre - z[neighbour, begin:end] >= flank_delta_db))
                        boundary[side] = angles[other]
                if not complete or not np.isfinite(boundary).all():
                    continue
                if boundary[1] <= boundary[0] or boundary[1]-boundary[0] > 8.:
                    continue
                if min(weak[0][keep].mean(), weak[1][keep].mean()) < flank_fraction:
                    continue
                # Measured strong weather support keeps the echo.
                weather = snr_available[row, begin:end] & (snr[row, begin:end] >= weather_snr_db)
                if weather[keep].mean() >= weather_fraction:
                    continue
                take = gates[keep & ~hit[row, gates]]
                if not len(take):
                    continue
                objects += 1
                hit[row, take] = True
                object_ids[row, take] = objects
                evidence['LEFT_DEG'][row, take] = boundary[0]
                evidence['RIGHT_DEG'][row, take] = boundary[1]
                evidence['SCALE_M'][row, take] = 60000.
                evidence['ANCHOR_DISTANCE_M'][row, take] = 0.
    return hit, object_ids, evidence, objects


def _bridge(line, bridge):
    """Fill short range gaps so a dashed line is judged as one radial run."""
    out = np.array(line, dtype=bool, copy=True)
    for begin, end in runs(line):
        if begin-bridge >= 0 and not out[begin-bridge]:
            out[begin-bridge:begin] = True
        if end+bridge <= len(out) and not out[end]:
            out[end:end+bridge] = True
    return out


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
