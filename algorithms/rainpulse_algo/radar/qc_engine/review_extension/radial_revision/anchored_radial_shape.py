"""RAW radial morphology anchored to a frozen original contamination object.

No constant-power requirement. Unanchored points and accepted residuals cannot
become sources; the target window never supplies its own reference support.
"""

import numpy as np

from ..arrays import mask, moment, native_geometry, runs
from .geometry import ResourceLimit

PREFIX = "RV2_ANCHORED_RADIAL_SHAPE_"


def qualify(native, blocked, group):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    snr, snr_ok = moment(native, "SNR")
    rho, rho_ok = moment(native, "RHOHV")
    blocked = mask(blocked, native.shape, "anchored radial barriers") | ~good[:, None]
    seed = np.asarray(group["RV2_SOURCE_LEDGER_SEED_ID"])
    if seed.shape != native.shape or seed.dtype != np.dtype("uint32"):
        raise ValueError("original seed identity required")
    parent = np.asarray(group["RV2_RAW_FAN_ID"])
    if parent.shape != native.shape or parent.dtype != np.dtype("uint32"):
        raise ValueError("frozen original RAW parent identity required")
    if np.prod(native.shape) > 2000000:
        raise ResourceLimit("anchored radial shape native budget exceeded")
    out = {
        PREFIX + "QUALIFIED_MASK": np.zeros(native.shape, "uint8"),
        PREFIX + "SOURCE_ID": np.zeros(native.shape, "uint32"),
        PREFIX + "AMBIGUOUS_MASK": np.zeros(native.shape, "uint8"),
    }
    records = []
    blocks = (r // 20000).astype(int)
    raw = observed & (z >= 0) & good[:, None]
    # A single frozen partition of ORIGINAL RAW, not the evolving QC result.
    # Strong original members remain the source/history; weak members supply
    # only morphology and can never be promoted into reference source IDs.
    weak = raw & (seed == 0)
    known = observed | (snr_ok & (snr >= -50) & (snr <= 100))
    weather = rho_ok & snr_ok & (rho >= 0.95) & (snr >= 10)
    work = 0
    for row in np.flatnonzero((raw & ~blocked).any(axis=1)):
        if row == 0 or row == len(az) - 1 or gaps[row - 1 : row + 1].any():
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[row - 1 : row + 2])))
        steps = np.diff(angles)
        if (
            not good[row - 1 : row + 2].all()
            or np.any(steps <= 0)
            or steps.max() > 1.5 * steps.min()
        ):
            continue
        beam = float(np.median(steps))
        # Select original ancestry BEFORE morphology qualification. A failed
        # nearest ancestor cannot be replaced by a farther convenient fit.
        jobs = []
        nearest = {}
        for source_row in range(max(0, row - 2), min(len(az), row + 3)):
            a, b = min(row - 1, source_row), max(row + 1, source_row) + 1
            delta = abs(float((az[source_row] - az[row] + 180) % 360 - 180))
            if delta > 2 * beam + 1e-4 or not good[a:b].all() or gaps[a : b - 1].any():
                continue
            for identity in np.unique(seed[source_row]):
                if identity == 0:
                    continue
                original = (
                    (seed[source_row] == identity) & observed[source_row] & ~blocked[source_row]
                )
                parents = np.unique(parent[source_row, original])
                parents = parents[parents > 0]
                jobs.append((source_row, identity, parents, delta, a, b))
                for pid in parents:
                    nearest[int(pid)] = min(nearest.get(int(pid), float("inf")), delta)
        for source_row, identity, parents, delta, a, b in jobs:
            allowed = np.zeros(len(r), bool)
            if source_row == row:
                allowed |= parent[row] == 0
            for pid in parents:
                if abs(delta - nearest[int(pid)]) < 1e-4:
                    allowed |= parent[row] == pid
            if not allowed.any():
                continue
            # A multi-ray band is frozen ONLY from original source-bearing
            # rows of this parent. Weak residuals cannot widen that band.
            band_lo, band_hi = row, row + 1
            anchored_rows = []
            band_original_ids = {}
            for k in range(max(0, row - 2), min(len(az), row + 3)):
                ids = np.unique(
                    seed[k, (seed[k] > 0) & observed[k] & ~blocked[k] & np.isin(parent[k], parents)]
                )
                # Parent tiles are distance-local; recover COMPLETE frozen
                # source identities rather than requiring 60 km inside a tile.
                cc = np.flatnonzero(np.isin(seed[k], ids) & observed[k] & ~blocked[k])
                band_original_ids[k] = ids
                if (
                    len(cc) * dr >= 10000
                    and len(np.unique(blocks[cc])) >= 3
                    and np.ptp(r[cc]) >= 60000
                ):
                    anchored_rows.append(k)
            if row in anchored_rows:
                while band_lo - 1 in anchored_rows:
                    band_lo -= 1
                while band_hi in anchored_rows:
                    band_hi += 1
            if band_hi - band_lo > 1:
                ca, cb = band_lo - 1, band_hi + 1
                if ca < 0 or cb > len(az) or not good[ca:cb].all() or gaps[ca : cb - 1].any():
                    continue
                band_angles = np.rad2deg(np.unwrap(np.deg2rad(az[ca:cb])))
                band_steps = np.diff(band_angles)
                if (
                    np.any(band_steps <= 0)
                    or band_steps.max() > 1.5 * band_steps.min()
                    or np.ptp(band_angles) > 6 * beam + 1e-4
                ):
                    continue
                a, b = min(a, ca), max(b, cb)
            else:
                ca, cb = row - 1, row + 2
            for lo, hi in runs(~blocked[a:b].any(axis=0)):
                original = np.flatnonzero(
                    (seed[source_row] == identity) & observed[source_row] & ~blocked[source_row]
                )
                original = original[(original >= lo) & (original < hi)]
                if len(original) * dr < 10000:
                    continue
                start, end = int(original.min()), int(original.max()) + 1
                # The range is frozen by ORIGINAL seed gates, never by a tail.
                cols = np.arange(start, end)
                shape_windows = []
                contrast_windows = {}
                # A local native-angle interpolation removes only the angular
                # background. It does not fit a receiver power law or discard
                # source-marked flank observations.
                weight = float(steps[0] / steps.sum())
                difference = z[row] - ((1 - weight) * z[row - 1] + weight * z[row + 1])
                paired = observed[row - 1 : row + 2].all(axis=0) & raw[row] & ~weather[row]
                for block in np.unique(blocks[cols]):
                    cc = cols[blocks[cols] == block]
                    work += 3 * len(cc)
                    if work > 50000000:
                        raise ResourceLimit(
                            "anchored radial shape work exceeded; no partial result"
                        )
                    pc = cc[paired[cc]]
                    if len(pc) >= 2 and len(pc) * dr >= 500 and len(pc) >= 0.2 * len(cc):
                        center = float(np.median(difference[pc]))
                        if (
                            abs(center) >= 6
                            and np.mean(np.sign(center) * difference[pc] >= 3) >= 0.8
                        ):
                            contrast_windows[int(block)] = center
                    foreground = weak[ca:cb][:, cc].copy()
                    if band_hi - band_lo > 1:
                        # Track the COMPLETE pre-clear object, including its
                        # strong original members; cleared members are not holes.
                        foreground[1:-1] = raw[band_lo:band_hi][:, cc]
                    # An unrelated short seed in the target window must not
                    # erase an actual shoulder return and fabricate isolation.
                    for k in (ca, cb - 1):
                        foreground[k - ca] |= raw[k, cc] & (seed[k, cc] != identity)
                    # If an original member is excluded from a shoulder,
                    # inspect its FIXED outward neighbour as well. Otherwise a
                    # seed-labelled weather strip could manufacture quiet air.
                    shoulder_ok = True
                    for k, outward in ((ca, ca - 1), (cb - 1, cb)):
                        excluded = raw[k, cc] & (seed[k, cc] == identity)
                        if not excluded.any():
                            continue
                        if (
                            outward < 0
                            or outward >= len(az)
                            or not good[outward]
                            or gaps[min(k, outward)]
                            or blocked[min(k, outward) : max(k, outward) + 1][:, cc].any()
                            or known[outward, cc].mean() < 0.8
                        ):
                            shoulder_ok = False
                            break
                        foreground[k - ca] |= raw[outward, cc]
                    if not shoulder_ok:
                        continue
                    signal = foreground.sum(axis=1)
                    present = (signal >= 2) & (signal * dr >= 500)
                    # Foreground morphology uses actual RAW returns. Available
                    # side measurements establish sampling coverage, not dry air.
                    coverage = known[ca:cb][:, cc].mean(axis=1)
                    density = np.divide(
                        signal,
                        known[ca:cb][:, cc].sum(axis=1),
                        out=np.zeros(cb - ca),
                        where=coverage > 0,
                    )
                    narrow = (not present[0] and not present[-1]) or (
                        np.min(density[1:-1]) >= 3 * max(density[0], density[-1])
                    )
                    if present[1:-1].all() and narrow and np.min(coverage) >= 0.8:
                        shape_windows.append(int(block))
                for block in np.unique(blocks[cols]):
                    refs = original[abs(blocks[original] - block) > 1]
                    if (
                        len(refs) * dr < 10000
                        or len(np.unique(blocks[refs])) < 3
                        or np.ptp(r[refs]) < 60000 - dr
                    ):
                        continue
                    # Every original member row must independently have
                    # held-out source support; the target block trains nobody.
                    band_reference_ids = {}
                    for k in range(band_lo, band_hi):
                        cc = np.flatnonzero(
                            np.isin(seed[k], band_original_ids.get(k, []))
                            & observed[k]
                            & ~blocked[k]
                            & (np.arange(len(r)) >= lo)
                            & (np.arange(len(r)) < hi)
                            & (abs(blocks - block) > 1)
                        )
                        if (
                            len(cc) * dr < 10000
                            or len(np.unique(blocks[cc])) < 3
                            or np.ptp(r[cc]) < 60000 - dr
                        ):
                            break
                        band_reference_ids[int(k)] = np.unique(seed[k, cc]).tolist()
                    if band_hi - band_lo > 1 and len(band_reference_ids) != band_hi - band_lo:
                        continue
                    shape_refs = [v for v in shape_windows if abs(v - block) > 1]
                    band_width = beam * (band_hi - band_lo)
                    width = max(r[end - 1] * np.deg2rad(band_width), dr)
                    contrast_refs = {
                        v: d for v, d in contrast_windows.items() if abs(v - block) > 1
                    }
                    contrast_fit = None
                    if (
                        int(block) in contrast_windows
                        and len(contrast_refs) >= 3
                        and (max(contrast_refs) - min(contrast_refs)) * 20000 >= 100000
                    ):
                        values = np.asarray(list(contrast_refs.values()))
                        center = float(np.median(values))
                        width_single = max(r[end - 1] * np.deg2rad(beam), dr)
                        if (
                            abs(center) >= 6
                            and np.mean(np.sign(center) * values >= 6) >= 0.8
                            and np.sign(center) * contrast_windows[int(block)] >= 6
                            and (max(contrast_refs) - min(contrast_refs) + 1) * 20000 / width_single
                            >= 8
                        ):
                            contrast_fit = center
                    if int(block) not in shape_windows and contrast_fit is None:
                        continue
                    chain = (
                        len(shape_refs) >= 3
                        and (max(shape_refs) - min(shape_refs)) * 20000 >= 80000
                    )
                    if contrast_fit is not None:
                        pass  # The independent contrast template supplies geometry.
                    elif chain:
                        span = (max(shape_refs) - min(shape_refs) + 1) * 20000
                        if span / width < 8:
                            continue
                    else:
                        # Already confirmed original-source morphology carries
                        # the long-object support. A local elongated RAW member
                        # need not rediscover three other weak fragments.
                        if np.ptp(r[refs]) < 100000 or np.ptp(r[refs]) / width < 8:
                            continue
                    target = cols[
                        (blocks[cols] == block)
                        & (seed[row, cols] == 0)
                        & raw[row, cols]
                        & ~weather[row, cols]
                        & allowed[cols]
                    ]
                    if contrast_fit is not None:
                        target = target[
                            paired[target]
                            & (abs(difference[target]) >= 6)
                            & (np.sign(contrast_fit) * difference[target] >= 6)
                        ]
                    elif not chain:
                        local = np.zeros(len(cols), bool)
                        local[np.searchsorted(cols, target)] = True
                        keep = np.zeros(len(cols), bool)
                        for a, b in runs(local):
                            if b - a >= 4 and (b - a) * dr >= 1000:
                                keep[a:b] = True
                        target = cols[keep]
                    owner = out[PREFIX + "SOURCE_ID"][row, target]
                    ambiguous = ((owner > 0) & (owner != identity)) | (
                        out[PREFIX + "AMBIGUOUS_MASK"][row, target] == 1
                    )
                    out[PREFIX + "AMBIGUOUS_MASK"][row, target[ambiguous]] = 1
                    out[PREFIX + "QUALIFIED_MASK"][row, target[ambiguous]] = 0
                    out[PREFIX + "SOURCE_ID"][row, target[ambiguous]] = 0
                    target = target[~ambiguous]
                    out[PREFIX + "QUALIFIED_MASK"][row, target] = 1
                    out[PREFIX + "SOURCE_ID"][row, target] = identity
                    records.append(
                        dict(
                            row=int(row),
                            source_id=int(identity),
                            source_row=int(source_row),
                            angular_offset_deg=delta,
                            frozen_band_rows=[int(band_lo), int(band_hi)],
                            band_reference_ids=band_reference_ids,
                            original_parent_ids=parents.tolist(),
                            target_block=int(block),
                            start_m=float(r[start]),
                            end_m=float(r[end - 1] + dr),
                            original_reference_blocks=np.unique(blocks[refs]).tolist(),
                            shape_reference_blocks=shape_refs,
                            contrast_reference_windows=contrast_refs,
                            contrast_template_dbz=contrast_fit,
                            shape_mode="heldout_native_transverse_contrast"
                            if contrast_fit is not None
                            else (
                                "heldout_weak_chain" if chain else "anchored_local_radial_fragment"
                            ),
                            qualified_gates=len(target),
                        )
                    )
    for record in records:
        record["qualified_gates"] = int(
            (
                (out[PREFIX + "SOURCE_ID"][record["row"]] == record["source_id"])
                & (blocks == record["target_block"])
            ).sum()
        )
    return out, dict(
        version="anchored-original-object-morphology-v4-native-contrast",
        records=records,
        qualified_gates=int(out[PREFIX + "QUALIFIED_MASK"].sum()),
        work=work,
        recursive_growth=False,
        filled_gates=0,
        action_authority=False,
        product_writes=False,
    )


def validate(arrays, native, blocked, group):
    expected, _ = qualify(native, blocked, group)
    if arrays.keys() != expected.keys():
        raise ValueError("anchored shape evidence fields differ")
    for key, value in expected.items():
        actual = np.asarray(arrays[key])
        if actual.dtype != value.dtype or not np.array_equal(actual, value):
            raise ValueError("anchored shape replay differs: " + key)
