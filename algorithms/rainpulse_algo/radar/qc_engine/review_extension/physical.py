"""Physical-area diagnostics; small area alone NEVER creates a QC action."""
import numpy as np
from scipy.ndimage import label
from .arrays import mask, native_geometry


def gate_areas_km2(native):
    r, az, dr, good, gaps = native_geometry(native)
    forward = (np.roll(az, -1) - az) % 360
    actual = forward[(forward > 0) & ~gaps]
    if actual.size == 0:
        return np.full(native.shape, np.nan, dtype="float32")
    nominal = float(np.median(actual))
    # A gap supplies no angular support beyond half a nominal beam sample.
    f = np.where(gaps | (forward <= 0), nominal, np.minimum(forward, 1.8 * nominal))
    widths = .5 * (f + np.roll(f, 1))
    if not native.full_ppi:
        widths[0] = .5 * (nominal + f[0])
        widths[-1] = .5 * (nominal + f[-2])
    inner = np.maximum(0., r - dr/2)
    outer = r + dr/2
    area = .5 * (outer**2-inner**2)[None, :] * np.deg2rad(widths)[:, None] / 1e6
    return np.where(good[:, None], area, np.nan).astype("float32")


def component_areas(native, candidate):
    """4-connected components, respecting missing rays, sectors and PPI seam."""
    use = mask(candidate, native.shape, "component_candidate") & np.asarray(native.geometry_good, bool)[:, None]
    ids = np.zeros(native.shape, "uint32")
    offset, start = 0, 0
    # Run label separately on every connected ray segment; never clear measured rows.
    cuts = list(np.flatnonzero(np.asarray(native.gap_after, bool)[:-1]) + 1) + [native.shape[0]]
    for end in cuts:
        part, n = label(use[start:end])
        ids[start:end] = np.where(part > 0, part + offset, 0)
        offset += n; start = end
    if native.full_ppi and not native.gap_after[-1]:
        parent = np.arange(offset + 1)
        def root(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]; k = parent[k]
            return k
        for top, bottom in zip(ids[0], ids[-1], strict=True):
            if top and bottom:
                a, b = root(int(top)), root(int(bottom))
                parent[max(a, b)] = min(a, b)
        for k in range(1, len(parent)):
            parent[k] = root(k)
        ids = parent[ids].astype("uint32")
    area = gate_areas_km2(native)
    sums = np.bincount(ids.ravel(), weights=np.where(use, np.nan_to_num(area, nan=0.), 0.).ravel(), minlength=offset + 1)
    sums[0] = 0.
    return ids, sums[ids].astype("float32")
