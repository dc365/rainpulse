"""Raw contour/Hough nominations and opt-in measured-edge morphology policy."""
import numpy as np
from skimage.transform import probabilistic_hough_line
from skimage.measure import LineModelND, ransac
from ..arrays import native_geometry, moment, mask


def isolated_support(native, cfg, blocked):
    """Empty *observed topology*, not a measurement of clear air."""
    r, az, dr, good, gaps = native_geometry(native)
    z, obs = moment(native, 'DBZH')
    valid = obs & good[:, None]
    nr, ng = native.shape
    left = np.zeros(native.shape, 'uint16'); right = np.zeros_like(left)
    for i in range(nr):
        left[i] = np.where(valid[i], 1+(left[i-1] if i and not gaps[i-1] else 0), 0)
    for i in range(nr-1, -1, -1):
        right[i] = np.where(valid[i], 1+(right[i+1] if i+1 < nr and not gaps[i] else 0), 0)
    support = np.zeros(native.shape, bool)
    width_m = np.full(native.shape, np.nan, 'float32')
    delta = (np.diff(az)+360)%360
    spacing = float(np.median(delta[(delta > 0) & ~gaps[:-1]])) if np.any((delta > 0) & ~gaps[:-1]) else 360.
    columns = np.arange(ng)
    for i in range(nr):
        lo = i-left[i].astype(int)+1; hi = i+right[i].astype(int)-1
        a = np.clip(lo-2, 0, nr-1); b = np.clip(lo-1, 0, nr-1)
        c = np.clip(hi+1, 0, nr-1); d = np.clip(hi+2, 0, nr-1)
        safe = (lo >= 2) & (hi < nr-2)
        for rows in (a, b, c, d):
            safe &= good[rows] & ~np.isfinite(z[rows, columns]) & ~blocked[rows, columns]
        for rows in (a, b, np.clip(hi, 0, nr-1), c):
            safe &= ~gaps[rows]
        angular = ((az[np.clip(hi, 0, nr-1)]-az[np.clip(lo, 0, nr-1)])%360)+spacing
        support[i] = (valid[i] & safe & ~blocked[i] &
                      (left[i].astype(int)+right[i].astype(int)-1 <= cfg.maximum_width_rays) &
                      (angular <= cfg.maximum_width_deg) & (z[i] >= (min(0., cfg.levels_dbz[0]) if cfg.sparse_isolated_enabled else cfg.levels_dbz[0])) &
                      (r >= cfg.minimum_range_m))
        width_m[i] = np.where(support[i], r*np.deg2rad(angular), np.nan)
    return support, width_m


def detect(native, cfg, blocked, *, source=None):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    blocked = mask(blocked, native.shape, 'line barriers')
    valid = observed & good[:, None]
    seeds = np.zeros(native.shape, bool)
    edge = np.full(native.shape, np.nan, 'float32')
    nr, ng = native.shape
    # Azimuth run lengths on each raw contour. Unknown flank gates can nominate
    # morphology, but do NOT establish measured background or source evidence.
    for level in cfg.levels_dbz:
        echo = valid & (z >= level)
        left = np.zeros(native.shape, 'uint16')
        right = np.zeros_like(left)
        for i in range(nr):
            left[i] = np.where(echo[i], 1 + (left[i-1] if i and not gaps[i-1] else 0), 0)
        for i in range(nr-1, -1, -1):
            right[i] = np.where(echo[i], 1 + (right[i+1] if i+1 < nr and not gaps[i] else 0), 0)
        for i in range(nr):
            lo = i-left[i].astype(int)+1
            hi = i+right[i].astype(int)-1
            a = np.clip(lo-1, 0, nr-1); b = np.clip(hi+1, 0, nr-1)
            width = left[i].astype(int)+right[i].astype(int)-1
            angular = (az[np.clip(hi, 0, nr-1)]-az[np.clip(lo, 0, nr-1)]) % 360
            safe = (lo > 0) & (hi < nr-1) & good[a] & good[b] & ~gaps[a] & ~gaps[np.clip(hi, 0, nr-1)]
            narrow = echo[i] & safe & (width <= cfg.maximum_width_rays) & (angular <= cfg.maximum_width_deg)
            seeds[i] |= narrow
            if cfg.morphology_quarantine_enabled:
                gates = np.arange(ng)
                measured = narrow & valid[a, gates] & valid[b, gates] & ~blocked[a, gates] & ~blocked[b, gates]
                contrast = np.minimum(z[i]-z[a, gates], z[i]-z[b, gates])
                edge[i] = np.fmax(edge[i], np.where(measured, contrast, np.nan))
    seeds &= (r[None, :] >= cfg.minimum_range_m) & ~blocked
    # Detection reduction only: restore nominations to actual raw contour gates.
    stride = max(1, int(round(cfg.detection_bin_m/dr)))
    bins = (ng+stride-1)//stride
    coarse = np.pad(seeds, ((0, 0), (0, bins*stride-ng))).reshape(nr, bins, stride).any(axis=2)
    lines = probabilistic_hough_line(
        coarse, threshold=max(5, int(np.ceil(cfg.minimum_support_m/(stride*dr)))),
        line_length=max(2, int(np.ceil(cfg.minimum_span_m/(stride*dr)))),
        line_gap=int(cfg.maximum_gap_m/(stride*dr)), theta=np.array([np.pi/2]), rng=42,
    )
    result = np.zeros(native.shape, bool)
    morphology = np.zeros(native.shape, bool)
    isolated = np.zeros(native.shape, bool)
    empty_flanks, width_m = (isolated_support(native, cfg, blocked) if cfg.isolated_quarantine_enabled
                            else (None, None))
    report = dict(version=cfg.version, detected_lines=len(lines), accepted_lines=0,
                  candidate_gates=0, status='evaluated', filled_gates=0)
    if len(lines) > cfg.maximum_lines:
        fields = {'RV2_LINE_MASK': result.astype('uint8')}
        if cfg.morphology_quarantine_enabled:
            fields.update(RV2_LINE_MORPH_MASK=morphology.astype('uint8'), RV2_LINE_EDGE_DB=np.full(native.shape, np.nan, 'float32'))
        if cfg.isolated_quarantine_enabled:
            fields.update(RV2_LINE_ISOLATED_MASK=isolated.astype('uint8'), RV2_LINE_EMPTY_FLANK_MASK=isolated.astype('uint8'))
        return fields, dict(report, status='resource_limit_abstained')
    for (x0, y0), (x1, y1) in lines:
        if y0 != y1:
            continue
        lo, hi = min(x0, x1)*stride, min(ng, (max(x0, x1)+1)*stride)
        ix = np.flatnonzero(seeds[y0, lo:hi])+lo
        if len(ix)*dr < cfg.minimum_support_m or len(ix) < 3 or np.ptp(r[ix]) < cfg.minimum_span_m:
            continue
        # Physical geometry check is on native metres, never map screenshot pixels.
        xy = r[ix, None]*np.array([[np.sin(np.deg2rad(az[y0])), np.cos(np.deg2rad(az[y0]))]])
        sample = xy[::max(1, len(xy)//256)]
        model, inliers = ransac(sample, LineModelND, min_samples=2,
                               residual_threshold=max(dr, 250.), max_trials=30, rng=42)
        if model is None or inliers.mean() < .95 or model.residuals(np.zeros((1, 2)))[0] > 1000:
            continue
        result[y0, ix] = True
        if cfg.morphology_quarantine_enabled:
            supported = ix[np.isfinite(edge[y0, ix]) & (edge[y0, ix] >= cfg.minimum_edge_db)]
            if (len(supported)*dr >= cfg.minimum_support_m and
                    np.ptp(r[supported]) >= cfg.minimum_span_m and
                    len(np.unique((r[supported]//20000).astype(int))) >= 3):
                morphology[y0, supported] = True
        report['accepted_lines'] += 1
    if cfg.isolated_quarantine_enabled:
        # Hough may split one native ray into several shorter, overlapping
        # segments. Qualify the bounded union, not that stochastic partition.
        for row in np.flatnonzero(np.any(result & empty_flanks, axis=1)):
            indices = np.flatnonzero(result[row] & empty_flanks[row])
            cuts = np.flatnonzero(np.diff(r[indices])-dr > cfg.isolated_link_gap_m)+1
            for isolated_ix in np.split(indices, cuts):
                if len(isolated_ix)*dr < max(20000., cfg.minimum_support_m):
                    continue
                span = float(np.ptp(r[isolated_ix]))
                if (span >= max(60000., cfg.minimum_span_m) and len(isolated_ix)*dr/(span+dr) >= .4 and
                        len(np.unique((r[isolated_ix]//20000).astype(int))) >= 3 and
                        span/max(1., float(np.max(width_m[row, isolated_ix]))) >= 8.):
                    isolated[row, isolated_ix] = True
    if cfg.isolated_quarantine_enabled and cfg.sparse_isolated_enabled:
        # Independent raw sparse support, before Hough can discard fragments.
        for row in np.flatnonzero(np.any(empty_flanks, axis=1)):
            ix = np.flatnonzero(empty_flanks[row])
            fragments = [g for g in np.split(ix, np.flatnonzero(np.diff(ix) > 1)+1)
                         if len(g)*dr >= 500.]
            if not fragments:
                continue
            boundaries = [i for i in range(1, len(fragments))
                          if r[fragments[i][0]]-r[fragments[i-1][-1]]-dr > 100000.]
            for ids in np.split(np.arange(len(fragments)), boundaries):
                if len(ids) < 3:
                    continue
                gates = np.concatenate([fragments[i] for i in ids])
                span = float(np.ptp(r[gates]))
                if (len(gates)*dr >= 10000. and span >= 120000. and
                        len(np.unique((r[gates]//20000).astype(int))) >= 4 and
                        span/max(1., float(np.max(width_m[row, gates]))) >= 12.):
                    result[row, gates] = True
                    isolated[row, gates] = True
    if cfg.isolated_quarantine_enabled and cfg.sparse_isolated_enabled and source is not None:
        # Associate only the immediate weak outer edge with independent source
        # evidence. Never bootstrap source evidence from our own morphology.
        anchors = mask(source, native.shape, 'line source anchors') & valid & ~blocked
        outer, outer_width = isolated_support(native, cfg.model_copy(update={
            'maximum_width_rays': 5, 'maximum_width_deg': 5.}), blocked)
        for row in range(1, nr-1):
            edge_support = outer[row] & (
                (anchors[row-1] & (z[row-1]-z[row] >= 20.) & ~valid[row+1]) |
                (anchors[row+1] & (z[row+1]-z[row] >= 20.) & ~valid[row-1]))
            ix = np.flatnonzero(edge_support)
            if not len(ix):
                continue
            for gates in np.split(ix, np.flatnonzero(np.diff(r[ix])-dr > 100000.)+1):
                if len(gates)*dr < 10000.:
                    continue
                span = float(np.ptp(r[gates]))
                if (span >= 120000. and len(np.unique((r[gates]//20000).astype(int))) >= 4 and
                        span/max(1., float(np.max(outer_width[row, gates]))) >= 8.):
                    result[row, gates] = True
                    empty_flanks[row, gates] = True
                    isolated[row, gates] = True
    report['candidate_gates'] = int(result.sum())
    fields = {'RV2_LINE_MASK': result.astype('uint8')}
    if cfg.isolated_quarantine_enabled:
        fields.update(RV2_LINE_ISOLATED_MASK=isolated.astype('uint8'),
                      RV2_LINE_EMPTY_FLANK_MASK=(empty_flanks & result).astype('uint8'))
        report['isolated_qualified_gates'] = int(isolated.sum())
    if cfg.morphology_quarantine_enabled:
        edge[~result] = np.nan
        fields.update(RV2_LINE_MORPH_MASK=morphology.astype('uint8'), RV2_LINE_EDGE_DB=edge)
        report['morphology_qualified_gates'] = int(morphology.sum())
    return fields, report


def coherent_source(native, candidates, blocked):
    """Strict multi-moment held-out reference for strong receiver-like lines.

    Reference selection never uses target values or morphological nominations.
    Fixed limits are versioned by fragment-line-v1; these are not probabilities.
    """
    r, _, dr, good, _ = native_geometry(native)
    values, available = {}, {}
    for k in ('DBZH', 'SNR', 'PHIDP', 'ZDR', 'RHOHV'):
        values[k], available[k] = moment(native, k)
    valid = np.logical_and.reduce(list(available.values())) & good[:, None] & ~blocked
    valid &= (values['SNR'] >= 20.) & (values['RHOHV'] >= .98) & (r[None, :] >= 50000.)
    blocks = (r//20000.).astype(int)
    accepted = np.zeros(native.shape, bool)
    span = np.full(native.shape, np.nan, 'float32')
    folds = np.zeros(native.shape, 'uint32')
    fold = 0
    for row in np.flatnonzero(np.any(candidates & valid, axis=1)):
        for b in np.unique(blocks[candidates[row] & valid[row]]):
            fold += 1
            target = candidates[row] & valid[row] & (blocks == b)
            ix = np.flatnonzero(valid[row] & (abs(blocks-b) > 1))
            if len(ix)*dr < 20000. or len(ix) < 20 or np.ptp(r[ix]) < 60000.:
                continue
            bb = np.unique(blocks[ix])
            if len(bb) < 3:
                continue
            # Two disjoint reference groups must each support the same template.
            groups = [ix[np.isin(blocks[ix], bb[p::2])] for p in (0, 1)]
            if min(map(len, groups))*dr < 5000.:
                continue
            snr = values['SNR'][row]; phase = values['PHIDP'][row]; zdr = values['ZDR'][row]
            sc = float(np.median(snr[ix])); zc = float(np.median(zdr[ix]))
            pc = float(np.angle(np.mean(np.exp(1j*np.deg2rad(phase[ix]))), deg=True))
            pd = (phase-pc+180)%360-180
            if any(np.percentile(abs(snr[g]-sc), 90) > 1. or
                   np.percentile(abs(pd[g]), 90) > 1. or
                   np.percentile(abs(zdr[g]-zc), 90) > .25 for g in groups):
                continue
            use = target & (abs(snr-sc) <= 1.) & (abs(pd) <= 1.) & (abs(zdr-zc) <= .25)
            accepted[row, use] = True
            span[row, use] = float(np.ptp(r[ix]))
            folds[row, use] = fold
    return {'RV2_LINE_SOURCE_MASK': accepted.astype('uint8'),
            'RV2_LINE_REFERENCE_SPAN_M': span, 'RV2_LINE_FOLD_ID': folds}


def grouped_strips(native, cfg, blocked):
    """Bounded raw component proposals plus gatewise polar confirmation."""
    from skimage.morphology import closing
    from skimage.measure import label, regionprops
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    valid = observed & good[:, None] & ~blocked & (r[None, :] >= 100000.)
    result = np.zeros(native.shape, bool)
    morphology = np.zeros(native.shape, bool)
    width = max(1, int(3000./dr)) | 1
    cuts = np.flatnonzero(gaps[:-1])+1
    for rows in np.split(np.arange(native.shape[0]), cuts):
        if not len(rows):
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        for level in (0., 10., 20., 35.):
            raw = valid[rows] & (z[rows] >= level)
            closed = closing(raw, footprint=np.ones((1, width), bool), mode="ignore")
            closed &= ~blocked[rows] & good[rows, None]
            components = label(closed, connectivity=2)
            for component in regionprops(components):
                a, b, c, d = component.bbox
                if c-a > 12 or angles[c-1]-angles[a] > 12. or r[d-1]-r[b] < 100000.:
                    continue
                support = raw[a:c, b:d] & (components[a:c, b:d] == component.label)
                if support.sum(axis=1).max()*dr < 20000.:
                    continue
                cols = np.flatnonzero(support.any(axis=0))+b
                if len(np.unique((r[cols]//20000).astype(int))) < 4:
                    continue
                for k in range(c-a):
                    result[rows[a+k], b:d] |= support[k]
                if not cfg.group_morphology_enabled or a == 0 or c == len(rows):
                    continue
                spacing = float(np.median(np.diff(angles))) if len(angles)>1 else 360.
                angular = angles[c-1]-angles[a]+spacing
                span = r[d-1]-r[b]
                if angular > 8. or span < 150000. or support.sum(axis=1).max()*dr < 30000.:
                    continue
                if span/max(1., r[d-1]*np.deg2rad(angular)) < 5.:
                    continue
                centres = np.divide((support*angles[a:c, None]).sum(axis=0),
                                    support.sum(axis=0), out=np.full(d-b, np.nan),
                                    where=support.sum(axis=0)>0)
                if np.diff(np.nanpercentile(centres, [10,90]))[0] > 2.:
                    continue
                lo, hi = rows[a-1], rows[c]
                if not good[lo] or not good[hi] or gaps[lo] or gaps[rows[c-1]]:
                    continue
                flank_ok = ~blocked[lo,b:d] & ~blocked[hi,b:d]
                for k in range(c-a):
                    row = rows[a+k]
                    clear = (~np.isfinite(z[lo,b:d]) | (z[row,b:d]-z[lo,b:d] >= 6.))
                    clear &= (~np.isfinite(z[hi,b:d]) | (z[row,b:d]-z[hi,b:d] >= 6.))
                    morphology[row,b:d] |= support[k] & flank_ok & clear

    track_details = {}
    if cfg.group_morphology_enabled:
        # Per-ray tracks do not depend on a globally isolated connected object.
        track = radial_tracks(native, blocked)
        if cfg.window_tracks_enabled:
            window, details = window_tracks(native, blocked, beam_width=cfg.antenna_beam_width_deg, details=True)
            variable, boundaries = variable_tracks(native, blocked, details=True)
            track |= window | variable
            track_details.update(details)
            track_details.update(boundaries)
        result |= track
        morphology |= track
    snr, sv = moment(native, 'SNR')
    rho, rv = moment(native, 'RHOHV')
    zdr, zv = moment(native, 'ZDR')
    polar = result & sv & (snr >= 10.) & rv & (rho >= 0.) & (rho <= 1.)
    polar &= (rho < .7) | ((rho < .85) & zv & (abs(zdr) > 3.))
    return {'RV2_GROUP_MASK': result.astype('uint8'),
            'RV2_GROUP_POLAR_MASK': polar.astype('uint8'),
            'RV2_GROUP_MORPH_MASK': morphology.astype('uint8'), **track_details}


def radial_tracks(native, blocked):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    valid = observed & good[:, None] & ~blocked & (r[None,:] >= 100000.) & (z >= 0.)
    result = np.zeros(native.shape, bool)
    for row in range(1, native.shape[0]-1):
        if not valid[row].any():
            continue
        for left, right in ((a,b) for a in range(1,5) for b in range(1,5)):
            lo, hi = row-left, row+right
            if lo < 0 or hi >= native.shape[0]:
                continue
            if not good[lo:hi+1].all() or gaps[lo:hi].any():
                continue
            angular = (az[hi]-az[lo])%360
            if angular <= 0 or angular > 8.:
                continue
            support = valid[row] & ~blocked[lo] & ~blocked[hi]
            support &= (~np.isfinite(z[lo]) | (z[row]-z[lo] >= 6.))
            support &= (~np.isfinite(z[hi]) | (z[row]-z[hi] >= 6.))
            interior = valid[lo+1:hi] & (z[lo+1:hi] >= z[row]-6.)
            support &= interior.mean(axis=0) >= .6
            ix = np.flatnonzero(support)
            if not len(ix):
                continue
            for gates in np.split(ix, np.flatnonzero(np.diff(r[ix])-dr > 60000.)+1):
                if len(gates)*dr < 30000.:
                    continue
                span = float(np.ptp(r[gates]))
                if (span < 150000. or len(gates)*dr/(span+dr) < .15 or
                        len(np.unique((r[gates]//20000).astype(int))) < 6 or
                        span/max(1., r[gates[-1]]*np.deg2rad(angular)) < 5.):
                    continue
                result[row,gates] = True
    return result


def window_tracks(native, blocked, *, beam_width=None, details=False):
    """Physical-scale radial occupancy; unknown flanks stay unknown."""
    from scipy.ndimage import uniform_filter1d
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    valid = observed & good[:,None] & ~blocked & (r[None,:]>=20000.) & (z>=0.)
    out = np.zeros(native.shape,bool)
    evidence={"RV2_WINDOW_"+k:np.full(native.shape,np.nan,"float32") for k in
              ("LEFT_DEG","RIGHT_DEG","SCALE_M","LEFT_MISSING_FRACTION","RIGHT_MISSING_FRACTION","BEAM_PROXY_DEG")}
    # Unwrap each geometry-contiguous angular segment independently.
    for rows in np.split(np.arange(native.shape[0]), np.flatnonzero(gaps[:-1])+1):
        if len(rows)<3:
            continue
        angles=np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing=float(np.median(np.diff(angles)))
        if spacing<=0:
            continue
        for index in range(1,len(rows)-1):
            row=rows[index]
            if not valid[row].any():
                continue
            pairs=set()
            beam = beam_width if beam_width is not None else spacing
            offsets = sorted(set((1.,2.,4.,beam,2*beam)))
            for left in offsets:
                for right in offsets:
                    lo=np.searchsorted(angles,angles[index]-left+1e-8,side='right')-1
                    hi=np.searchsorted(angles,angles[index]+right-1e-8,side='left')
                    if lo>=0 and hi<len(rows) and lo<index<hi:
                        pairs.add((lo,hi))
            for lo,hi in sorted(pairs):
                stencil=rows[lo:hi+1]
                angular=angles[hi]-angles[lo]
                if angular>8. or not good[stencil].all():
                    continue
                a,b=rows[lo],rows[hi]
                safe=~blocked[a]&~blocked[b]
                high_a=np.isfinite(z[a]) & (z[a]>z[row]-6.)
                high_b=np.isfinite(z[b]) & (z[b]>z[row]-6.)
                interior=(valid[rows[lo+1:hi]] &
                          (z[rows[lo+1:hi]]>=z[row]-6.)).mean(axis=0)
                target=valid[row]&safe&~high_a&~high_b
                centre=valid[row]&safe
                for scale in (20000.,40000.,80000.):
                    size=max(3,int(round(scale/dr))|1)
                    avg=lambda x: uniform_filter1d(x.astype(float),size,mode='constant')
                    extent=avg(np.ones(len(r)))
                    count=avg(centre)
                    inside=avg(np.where(centre,interior,0.))
                    shoulders=(avg(centre&high_a)<=.2*count)&(avg(centre&high_b)<=.2*count)
                    qualified=(extent>=.75)&(count>=.35*extent)&(inside>=.6*count)&shoulders
                    physical_width=np.maximum(angular,beam)
                    qualified &= scale/np.maximum(1.,(r+scale/2)*np.deg2rad(physical_width))>= (6. if scale==20000. else 4.)
                    if scale==20000.:
                        # Short tracks require denser support and cleaner shoulders.
                        qualified &= (count>=.7*extent)&(inside>=.85*count)
                        qualified &= (avg(centre&high_a)<=.05*count)&(avg(centre&high_b)<=.05*count)
                    hit=target&qualified
                    out[row] |= hit
                    for key,value in (("LEFT_DEG",angles[lo]),("RIGHT_DEG",angles[hi]),
                                      ("SCALE_M",scale),("BEAM_PROXY_DEG",beam),
                                      ("LEFT_MISSING_FRACTION",avg(~np.isfinite(z[a]))/np.maximum(extent,1e-9)),
                                      ("RIGHT_MISSING_FRACTION",avg(~np.isfinite(z[b]))/np.maximum(extent,1e-9))):
                        evidence["RV2_WINDOW_"+key][row,hit]=value if np.ndim(value)==0 else value[hit]
    return (out,evidence) if details else out


def variable_tracks(native, blocked, *, details=False):
    """Track measured angular boundaries as width changes with range."""
    r,az,dr,good,gaps=native_geometry(native)
    z,observed=moment(native,'DBZH')
    valid=observed&good[:,None]&~blocked&(r[None,:]>=50000.)&(z>=0.)
    out=np.zeros(native.shape,bool)
    boundaries={"RV2_TRACK_"+k:np.full(native.shape,np.nan,"float32") for k in ("LEFT_DEG","RIGHT_DEG")}
    for rows in np.split(np.arange(native.shape[0]),np.flatnonzero(gaps[:-1])+1):
        angles=np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        for index in range(1,len(rows)-1):
            row=rows[index]
            if not valid[row].any():
                continue
            low=np.full(len(r),np.nan);high=low.copy()
            best=np.full(len(r),np.inf)
            pairs=set()
            for left in (1.,2.,3.,4.):
                for right in (1.,2.,3.,4.):
                    a=np.searchsorted(angles,angles[index]-left+1e-8,side='right')-1
                    b=np.searchsorted(angles,angles[index]+right-1e-8,side='left')
                    if a>=0 and b<len(rows) and a<index<b:
                        pairs.add((a,b))
            for a,b in sorted(pairs):
                width=angles[b]-angles[a]
                if width>8. or not good[rows[a:b+1]].all():
                    continue
                lo,hi=rows[a],rows[b]
                hit=valid[row]&~blocked[lo]&~blocked[hi]
                hit&=(~np.isfinite(z[lo])|(z[row]-z[lo]>=6.))
                hit&=(~np.isfinite(z[hi])|(z[row]-z[hi]>=6.))
                inside=valid[rows[a+1:b]]&(z[rows[a+1:b]]>=z[row]-6.)
                hit&=(inside.mean(axis=0)>=.6)&(width<best)
                best[hit]=width;low[hit]=angles[a];high[hit]=angles[b]
            ix=np.flatnonzero(np.isfinite(best))
            if not len(ix):
                continue
            breaks=(np.diff(r[ix])-dr>10000.)|(abs(np.diff(low[ix]))>1.5)|(abs(np.diff(high[ix]))>1.5)
            for gates in np.split(ix,np.flatnonzero(breaks)+1):
                if len(gates)*dr<20000.:
                    continue
                span=float(np.ptp(r[gates]))
                if (span<60000. or len(np.unique((r[gates]//20000).astype(int)))<3 or
                    np.ptp((low[gates]+high[gates])/2)>2. or
                    span/max(1.,float(np.max(r[gates]*np.deg2rad(best[gates]))))<5.):
                    continue
                out[row,gates]=True
                boundaries["RV2_TRACK_LEFT_DEG"][row,gates]=low[gates]
                boundaries["RV2_TRACK_RIGHT_DEG"][row,gates]=high[gates]
    return (out,boundaries) if details else out
