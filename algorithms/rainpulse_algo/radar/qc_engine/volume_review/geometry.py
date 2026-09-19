"""Relative effective-Earth geometry and topology; no fabricated vertical data."""
import numpy as np
from skimage.measure import label

EARTH = 6371000. * 4. / 3.


def wrap(x):
    return (np.asarray(x)+180.) % 360.-180.


def xyz(sweep, rows, gates):
    r = sweep.ranges[gates]; e = np.deg2rad(sweep.elevation[rows]); a = np.deg2rad(sweep.azimuth[rows])
    height = np.sqrt(EARTH**2+r*r+2*EARTH*r*np.sin(e))-EARTH
    ground = EARTH*np.arctan2(r*np.cos(e), EARTH+r*np.sin(e))
    return np.column_stack((ground*np.sin(a), ground*np.cos(a), height))


def angular_widths(sweep):
    da = (np.roll(sweep.azimuth, -1)-sweep.azimuth) % 360
    usable = da[(~sweep.gap_after) & (da > .01)]
    nominal = float(np.median(usable)) if usable.size else 0.
    after = np.where(sweep.gap_after, nominal, da)
    return (after+np.roll(after, 1))/2.


def label_native(mask, sweep):
    """4-connectivity with closed-circle seam and explicit acquisition gaps."""
    mask = np.asarray(mask, bool)
    if mask.shape != sweep.shape:
        raise ValueError("label geometry mismatch")
    labels = np.zeros(mask.shape, "int32"); offset = 0
    for rows in np.split(np.arange(len(mask)), np.flatnonzero(sweep.gap_after[:-1])+1):
        part = label(mask[rows], connectivity=1)
        part[part > 0] += offset
        labels[rows] = part
        offset = int(part.max(initial=offset))
    if not sweep.gap_after[-1] and sweep.good[0] and sweep.good[-1] and offset:
        parent = np.arange(offset+1)
        def root(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        for a, b in zip(labels[0], labels[-1], strict=True):
            if a and b:
                pa, pb = root(a), root(b)
                parent[max(pa, pb)] = min(pa, pb)
        lut = np.array([root(i) for i in range(offset+1)])
        labels = lut[labels]
    _, inv = np.unique(labels, return_inverse=True)
    # Preserve zero even when every cell is foreground.
    unique = np.unique(labels)
    if unique[0] != 0:
        inv += 1
    return inv.reshape(mask.shape).astype("int32")


def runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype("int8"))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def nearest_ray(sweep, angles, maximum_offset):
    az = sweep.azimuth
    order = np.argsort(az); sorted_az = az[order]
    pos = np.searchsorted(sorted_az, angles)
    left, right = order[(pos-1)%len(az)], order[pos%len(az)]
    rows = np.where(abs(wrap(az[left]-angles)) <= abs(wrap(az[right]-angles)), left, right)
    # Reject targets outside the actual half-footprint, even inside the configured tolerance.
    delta = wrap(angles-az[rows])
    widths = angular_widths(sweep)
    limit = np.minimum(maximum_offset, widths[rows]/2.+1e-6)
    return rows, sweep.good[rows] & (abs(delta) <= limit)
