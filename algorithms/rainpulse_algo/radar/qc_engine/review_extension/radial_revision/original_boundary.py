"""Measured RAW exterior of frozen original seeds, never recursive growth.

Research qualification only. A parent ID is a clue, not a pollution decision.
The target and neighbouring distance windows cannot train their own boundary.
"""

import numpy as np

from ..arrays import mask, moment, native_geometry, runs
from .geometry import ResourceLimit

PREFIX = "RV2_ORIGINAL_BOUNDARY_"


def qualify(native, blocked, group, *, maximum_work=50000000):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    snr, snr_ok = moment(native, "SNR")
    rho, rho_ok = moment(native, "RHOHV")
    blocked = mask(blocked, native.shape, "original boundary barriers") | ~good[:, None]
    seed = np.asarray(group["RV2_SOURCE_LEDGER_SEED_ID"])
    parent = np.asarray(group["RV2_RAW_FAN_ID"])
    if any(v.shape != native.shape or v.dtype != np.dtype("uint32") for v in (seed, parent)):
        raise ValueError("frozen original source and RAW parent required")
    if not isinstance(maximum_work, int) or not 1 <= maximum_work <= 50000000:
        raise ValueError("bounded positive work budget required")
    if np.prod(native.shape) > 2000000:
        raise ResourceLimit("original boundary native gate budget exceeded")
    selected = np.zeros(native.shape, bool)
    owner = np.zeros(native.shape, "uint32")
    records = []
    holds = {}
    work = 0

    def charge(amount):
        nonlocal work
        work += int(amount)
        if work > maximum_work:
            raise ResourceLimit("original boundary work exceeded; no partial result")

    def hold(reason):
        holds[reason] = holds.get(reason, 0) + 1

    noise = snr_ok & (snr >= -50) & (snr <= 3)
    weather = rho_ok & snr_ok & (rho >= .95) & (snr >= 10)
    original = (seed > 0) & observed & ~blocked
    blocks = (r // 20000).astype(int)
    for identity in np.unique(parent[original & (parent > 0)]):
        ids = np.unique(seed[original & (parent == identity)])
        source = original & np.isin(seed, ids)
        source_rows = np.unique(np.where(source)[0])
        islands = np.split(source_rows, np.flatnonzero(np.diff(source_rows) > 1) + 1)
        for island in islands:
            if len(island) < 2:
                hold("single_original_row")
                continue
            lo, hi = int(island[0]), int(island[-1]) + 1
            if gaps[lo:hi-1].any():
                hold("source_gap")
                continue
            angles = np.rad2deg(np.unwrap(np.deg2rad(az)))
            spacing = np.diff(angles[lo:hi])
            beam = float(np.median(spacing))
            if beam <= 0 or np.any(spacing > 1.5 * beam) or angles[hi-1]-angles[lo] > 12:
                hold("source_width_or_geometry")
                continue
            # Fixed two-native-beam stencil from ORIGINAL rows. RAW members
            # are never used to enlarge it, including in subsequent windows.
            lower, upper = lo, hi
            while lower > 0 and angles[lo]-angles[lower-1] <= 2*beam+1e-6:
                if gaps[lower-1] or not good[lower-1]:
                    break
                lower -= 1
            while upper < len(az) and angles[upper]-angles[hi-1] <= 2*beam+1e-6:
                if gaps[upper-1] or not good[upper]:
                    break
                upper += 1
            # Exterior observations are mandatory; sector endpoints abstain.
            if lower == 0 or upper == len(az) or gaps[lower-1] or gaps[upper-1]:
                hold("unknown_sector_exterior")
                continue
            core_cols = np.where(source[lo:hi])[1]
            start, end = float(r[core_cols].min()), float(r[core_cols].max())
            full_columns = np.flatnonzero((r >= start) & (r <= end))
            if blocked[lower-1:upper+1][:, full_columns].any():
                hold("complete_object_barrier")
                continue
            history = []
            measured_boundaries = []
            clipped_history = False
            for block in np.unique(blocks[core_cols]):
                cols = np.flatnonzero((blocks == block) & (r >= start) & (r <= end))
                charge((upper-lower+2)*len(cols))
                ix = np.ix_(np.arange(lower, upper), cols)
                present = observed[ix] & (z[ix] >= 0)
                count = present.sum(axis=1)
                occupied = (count >= 2) & (count*dr >= 500)
                matches = [(a+lower, b+lower) for a, b in runs(occupied)
                           if a+lower <= lo and b+lower >= hi]
                anchors = source[lo:hi][:, cols]
                if len(matches) != 1 or len(np.unique(np.where(anchors)[0])) < 2:
                    history.append((int(block), None))
                    continue
                a, b = matches[0]
                # A fixed search fence must not masquerade as a measured RAW
                # edge. Keep this contradiction even if contrast later fails.
                for edge, side, fence in ((a, a-1, lower), (b, b, upper)):
                    count_side = int((observed[side, cols] & (z[side, cols] >= 0)).sum())
                    if edge == fence and count_side >= 2 and count_side*dr >= 500:
                        clipped_history = True
                measured_boundaries.append((a, b))
                columns = cols[anchors.any(axis=0)]
                if len(columns)*dr < 500:
                    history.append((int(block), None))
                    continue
                stencil = np.ix_(np.arange(a-1, b+1), columns)
                known = ((observed[stencil] | noise[stencil]) & ~blocked[stencil]).all(axis=0)
                body = z[np.ix_(np.arange(a, b), columns)]
                av = observed[np.ix_(np.arange(a, b), columns)]
                centre = np.divide(np.where(av, body, 0).sum(axis=0), av.sum(axis=0),
                                   out=np.full(len(columns), np.nan), where=av.sum(axis=0)>0)
                clear = known.copy()
                for side in (a-1, b):
                    clear &= ((observed[side, columns] & (z[side, columns] <= centre-6)) |
                              (~observed[side, columns] & noise[side, columns]))
                valid = known.mean() >= .8 and clear.mean() >= .8
                history.append((int(block), (a, b, columns) if valid else None))
            # Failed original windows remain in history. A convenient far
            # tail cannot restart as an independent clean boundary template.
            supported_history = [(block, entry) for block, entry in history if entry is not None]
            if clipped_history:
                hold("RAW_body_crosses_fixed_fence")
                continue
            if not history or len(supported_history)/len(history) < .8:
                hold("failed_complete_reference_history")
                continue
            # Boundary stability uses every matched RAW window, including
            # failed contrast windows; poor flanks cannot hide a curved body.
            boundaries = np.array(measured_boundaries)
            if (np.ptp(angles[boundaries[:, 0]]) > beam+1e-6 or
                    np.ptp(angles[boundaries[:, 1]-1]) > beam+1e-6):
                hold("unstable_complete_boundary")
                continue
            for target in np.unique(blocks[(r >= start) & (r <= end)]):
                independent_history = [(block, entry) for block, entry in history
                                       if abs(block-target) > 1]
                refs = [(block, entry) for block, entry in independent_history if entry is not None]
                if not independent_history or len(refs)/len(independent_history) < .8:
                    hold("heldout_reference_coverage")
                    continue
                if len(refs) < 3:
                    hold("heldout_reference_windows")
                    continue
                ref_cols = np.unique(np.concatenate([v[2] for _, v in refs]))
                if len(ref_cols)*dr < 10000 or np.ptp(r[ref_cols]) < 60000-dr:
                    hold("heldout_reference_support")
                    continue
                # Intersection of independent measured boundaries. No outward
                # rounding, interpolated holes or target-trained edge growth.
                a = max(v[0] for _, v in refs)
                b = min(v[1] for _, v in refs)
                cols = np.flatnonzero((blocks == target) & (r >= start) & (r <= end))
                rr, cc = np.where(observed[a:b][:, cols] & (seed[a:b][:, cols] == 0))
                rr, cc = rr+a, cols[cc]
                charge((b-a+2)*len(cols))
                safe = ((observed[a-1:b+1][:, cols] | noise[a-1:b+1][:, cols]) &
                        ~blocked[a-1:b+1][:, cols]).all(axis=0)
                accept = safe[np.searchsorted(cols, cc)] & ~weather[rr, cc]
                for side in (a-1, b):
                    accept &= ((observed[side, cc] & (z[side, cc] <= z[rr, cc]-6)) |
                               (~observed[side, cc] & noise[side, cc]))
                rr, cc = rr[accept], cc[accept]
                selected[rr, cc] = True
                owner[rr, cc] = identity
                records.append(dict(parent_id=int(identity), source_ids=ids.tolist(),
                                    target_block=int(target), left_row=int(a), right_row=int(b),
                                    reference_blocks=[int(v[0]) for v in refs],
                                    failed_original_blocks=[int(block) for block, entry in history
                                                            if entry is None],
                                    start_m=start, end_m=end, qualified_gates=len(rr)))
    return {PREFIX+"QUALIFIED_MASK": selected.astype("uint8"), PREFIX+"PARENT_ID": owner}, dict(
        version="original-measured-boundary-v1", qualified_gates=int(selected.sum()),
        records=records, holds=holds, work=work, action_authority=False, recursive_growth=False,
        product_writes=False, filled_gates=0,
    )


def validate(arrays, native, blocked, group, **kwargs):
    expected, _ = qualify(native, blocked, group, **kwargs)
    if arrays.keys() != expected.keys():
        raise ValueError("original boundary evidence fields differ")
    for key, value in expected.items():
        actual = np.asarray(arrays[key])
        if actual.dtype != value.dtype or not np.array_equal(actual, value):
            raise ValueError("original boundary replay differs: " + key)
