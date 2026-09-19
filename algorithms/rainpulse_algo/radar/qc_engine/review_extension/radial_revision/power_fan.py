"""Wide radial fans with stable angular extent and a range-power signature.

Block medians tolerate speckle; this is morphological evidence, not calibrated
receiver power or proof that an individual observation is non-meteorological.
Only measured, unprotected gates near the fitted profile can be isolated.
"""
import numpy as np
from ..arrays import native_geometry, moment, mask


def detect_power_fans(native, blocked):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    blocked = mask(blocked, native.shape, 'power fan barriers')
    valid = observed & good[:, None] & ~blocked & (r[None, :] >= 50000.)
    term = 20.*np.log10(np.maximum(r, 1.)/50000.)
    power = z-term[None, :]
    blocks = (r//20000).astype(int)
    ids = np.unique(blocks[r >= 50000.])
    med = np.full((len(az), len(ids)), np.nan)
    support = np.zeros_like(med)
    for j, b in enumerate(ids):
        cols = np.flatnonzero(blocks == b)
        for row in np.flatnonzero(valid[:, cols].sum(axis=1)*dr >= 5000.):
            use = cols[valid[row, cols]]
            med[row, j] = np.median(power[row, use])
            support[row, j] = len(use)*dr
    qualified = np.zeros(len(az), bool)
    intercept = np.full(len(az), np.nan)
    ranges = np.full((len(az), 2), np.nan)
    for row in range(len(az)):
        ix = np.flatnonzero(np.isfinite(med[row]))
        if len(ix) < 8 or support[row].sum() < 80000.:
            continue
        # No long unobserved range bridge. All reference blocks must agree;
        # unlike gatewise fitting, a few noisy gates cannot destroy the track.
        if np.max(np.diff(ids[ix])) > 2 or (ids[ix[-1]]-ids[ix[0]])*20000 < 160000.:
            continue
        centre = float(np.median(med[row, ix]))
        if (np.percentile(abs(med[row, ix]-centre), 90) > 2.5 or
                abs(np.median(med[row, ix[::2]])-np.median(med[row, ix[1::2]])) > 1.5):
            continue
        lo, hi = ids[ix[0]]*20000., (ids[ix[-1]]+1)*20000.
        if 20*np.log10(hi/max(lo, 50000.)) < 6.:
            continue
        qualified[row] = True
        intercept[row] = centre
        ranges[row] = lo, hi
    out = np.zeros(native.shape, bool)
    residual = np.full(native.shape, np.nan, 'float32')
    for rows in np.split(np.arange(len(az)), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 5:
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        ix = np.flatnonzero(qualified[rows])
        for band in np.split(ix, np.flatnonzero(np.diff(angles[ix]) > 4.)+1):
            if len(band) < 3 or band[0] == 0 or band[-1] == len(rows)-1:
                continue
            angular = angles[band[-1]]-angles[band[0]]+spacing
            if not 2. <= angular <= 90.:
                continue
            a, b = rows[band[0]-1], rows[band[-1]+1]
            if not good[a] or not good[b]:
                continue
            if not good[rows[band[0]:band[-1]+1]].all():
                continue
            # Locate the shoulders within four degrees of the model support.
            # A noisy edge ray may fail the power fit while still being inside
            # the fan. Never jump across missing scan geometry to find a flank.
            shoulders = []
            for edge, direction in ((band[0], -1), (band[-1], 1)):
                choices = []
                k = edge+direction
                while 0 <= k < len(rows) and abs(angles[k]-angles[edge]) <= 4.:
                    if not good[rows[k]]:
                        break
                    choices.append(rows[k])
                    k += direction
                shoulders.append(choices)
            for row in rows[band]:
                cols = (r >= ranges[row, 0]) & (r < ranges[row, 1]) & valid[row]
                if cols.sum()*dr < 80000.:
                    continue
                safe = cols.copy()
                for choices in shoulders:
                    accepted = None
                    for side in choices:
                        usable = cols & ~blocked[side]
                        if usable.sum() < .8*cols.sum():
                            continue
                        measured = observed[side, usable]
                        strong = measured & (z[side, usable] > z[row, usable]-6.)
                        if strong.mean() <= .2:
                            accepted = side
                            break
                    if accepted is None:
                        safe[:] = False
                        break
                    safe &= ~blocked[accepted]
                delta = power[row]-intercept[row]
                hit = safe & (abs(delta) <= 6.)
                out[row, hit] = True
                residual[row, hit] = delta[hit]
    return {'RV2_POWER_FAN_MASK': out.astype('uint8'),
            'RV2_POWER_FAN_RESIDUAL_DB': residual}
