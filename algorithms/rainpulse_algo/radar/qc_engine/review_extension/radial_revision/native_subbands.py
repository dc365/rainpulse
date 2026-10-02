"""Held-out original RAW subbands, independent of legacy rejected-source IDs."""

import numpy as np

from ..arrays import moment, native_geometry, runs
from .geometry import ResourceLimit
from .object_model import MeasuredWindow, RawObject


def _edges(z, observed, snr, sa, barred, lo, hi, cols):
    present = observed[lo:hi][:, cols]
    body = z[lo:hi][:, cols]
    mean = np.divide(
        np.where(present, body, 0).sum(axis=0),
        present.sum(axis=0),
        out=np.full(len(cols), np.nan),
        where=present.sum(axis=0) > 0,
    )
    clear = np.ones(len(cols), bool)
    known = np.ones(len(cols), bool)
    for row in (lo - 1, hi):
        noise = sa[row, cols] & (snr[row, cols] <= 3)
        known &= ~barred[row, cols] & (observed[row, cols] | noise)
        clear &= ~barred[row, cols] & (
            (observed[row, cols] & (z[row, cols] <= mean - 6)) | (~observed[row, cols] & noise)
        )
    return float(known.mean()), float(clear.mean())


def nominate(native, blocked, parents, beam, *, maximum_work=50000000):
    ranges, az, dr, good, gaps = native_geometry(native)
    bearing = np.rad2deg(np.unwrap(np.deg2rad(az)))
    sectors = np.r_[0, np.cumsum(gaps[:-1])]
    z, observed = moment(native, "DBZH")
    snr, sa = moment(native, "SNR")
    rho, ra = moment(native, "RHOHV")
    raw = observed & good[:, None] & (z >= 0)
    weather = observed & ra & sa & (rho >= 0.95) & (snr >= 10)
    known = observed | (sa & (snr >= -50) & (snr <= 100))
    barred = blocked | ~good[:, None]
    blocks = (ranges // 20000).astype(int)
    cols_by_block = {int(b): np.flatnonzero(blocks == b) for b in np.unique(blocks)}
    profiles = {}
    all_bands = {}
    work = 0

    def charge(count):
        nonlocal work
        work += int(count)
        if work > maximum_work:
            raise ResourceLimit("native subband work exceeded; no partial result")

    for block, cols in cols_by_block.items():
        charge(len(az) * len(cols))
        density = raw[:, cols].mean(axis=1)
        occupied = density >= 0.2
        for lo, hi in runs(~occupied):
            if lo > 0 and hi < len(az) and hi - lo <= 2:
                if known[lo:hi][:, cols].mean(axis=1).min() >= 0.8:
                    occupied[lo:hi] = True
        profiles[block] = []
        all_bands[block] = []
        for lo, hi in runs(occupied):
            if lo == 0 or hi == len(az) or hi - lo < 3:
                continue
            angles = np.rad2deg(np.unwrap(np.deg2rad(az[lo - 1 : hi + 1])))
            steps = np.diff(angles)
            if (
                not good[lo - 1 : hi + 1].all()
                or gaps[lo - 1 : hi].any()
                or (steps <= 0).any()
                or steps.max() > 1.5 * steps.min()
                or angles[-2] - angles[1] > 90
            ):
                continue
            all_bands[block].append((lo, hi))
            charge((hi - lo + 2) * len(cols))
            k, c = _edges(z, observed, snr, sa, barred, lo, hi, cols)
            excluded = barred[lo:hi][:, cols] | weather[lo:hi][:, cols]
            # Occupancy includes protected/weather observations. Exclusion
            # cannot manufacture an edge or change the denominator.
            if (
                known[lo - 1 : hi + 1][:, cols].mean(axis=1).min() >= 0.8
                and excluded.mean(axis=1).max() <= 0.2
                and (raw[lo:hi][:, cols] & ~excluded).mean() >= 0.6
                and max(density[lo - 1], density[hi]) <= 0.2
                and k >= 0.8
                and c >= 0.8
            ):
                profiles[block].append((lo, hi))
    seeds = sorted({band for bands in profiles.values() for band in bands})
    pure_counter = set()
    narrowing_counter = set()
    for parent in parents:
        if "ambiguous_fork_or_merge" not in parent.history_holds and any(
            key in parent.history_holds
            for key in (
                "curved_or_drifting_centre",
                "narrowing_physical_width_weather_counterexample",
            )
        ):
            pure_counter.update(parent.members)
        if "narrowing_physical_width_weather_counterexample" in parent.history_holds:
            narrowing_counter.update(parent.members)
    identity = max((o.identity for o in parents), default=0)
    output = []
    for lo, hi in seeds:
        # Native rows, not accepted targets, define this immutable hypothesis.
        reference_blocks = []
        history = []
        measured_bounds = []
        for block, bands in all_bands.items():
            charge(len(bands))
            compatible = [
                (a, b)
                for a, b in bands
                if sectors[a] == sectors[lo]
                and abs(bearing[a] - bearing[lo]) <= 2 * beam + 1e-6
                and abs(bearing[b - 1] - bearing[hi - 1]) <= 2 * beam + 1e-6
            ]
            enclosed = [(a, b) for a, b in bands if lo <= a < b <= hi]
            state = "unknown_or_unmatched"
            if len(enclosed) > 1:
                state = "forked"
            elif len(compatible) == 1:
                state = "matched"
                a, b = compatible[0]
                measured_bounds.append((block, bearing[a], bearing[b - 1]))
                if compatible[0] in profiles[block]:
                    reference_blocks.append(block)
            elif any(a <= lo and b >= hi for a, b in bands):
                state = "merged"
            history.append((block, state))
        if (
            len(reference_blocks) < 5
            or (max(reference_blocks) - min(reference_blocks)) * 20000 < 150000
        ):
            continue
        source_cols = np.concatenate([cols_by_block[b] for b in reference_blocks])
        signal_cols = source_cols[raw[lo:hi][:, source_cols].any(axis=0)]
        if not len(signal_cols):
            continue
        start, end = int(signal_cols.min()), int(signal_cols.max() + 1)
        windows = []
        for block, cols in cols_by_block.items():
            cols = cols[(cols >= start) & (cols < end)]
            if not len(cols):
                continue
            charge((hi - lo + 2) * len(cols))
            rr, cc = np.where(raw[lo:hi][:, cols])
            rr += lo
            cc = cols[cc]
            if not len(rr):
                continue
            refs = tuple(b for b in reference_blocks if abs(b - block) > 1)
            enough = len(refs) >= 5 and (max(refs) - min(refs)) * 20000 >= 150000
            k, c = _edges(z, observed, snr, sa, barred, lo, hi, cols)
            coverage = known[lo - 1 : hi + 1][:, cols].mean(axis=1).min() >= 0.8
            width_clear = max(raw[lo - 1, cols].mean(), raw[hi, cols].mean()) <= 0.2
            windows.append(
                MeasuredWindow(
                    block=block,
                    start_m=float(ranges[cols[0]]),
                    end_m=float(ranges[cols[-1]] + dr),
                    left_deg=float(bearing[lo] - beam / 2),
                    right_deg=float(bearing[hi - 1] + beam / 2),
                    left_row=lo - 1,
                    right_row=hi,
                    members=tuple(map(int, rr * native.shape[1] + cc)),
                    known_fraction=k if coverage else 0.0,
                    contrast_fraction=c if enough and width_clear else 0.0,
                    anchor_support_m=float(len(np.unique(cc)) * dr),
                    reference_blocks=refs,
                )
            )
        if not windows:
            continue
        members = {i for w in windows for i in w.members}
        holds = []
        active_history = [
            (b, state)
            for b, state in history
            if start <= cols_by_block[b][-1] and cols_by_block[b][0] < end
        ]
        matched = [state == "matched" for _, state in active_history]
        merged = [state == "merged" for _, state in active_history]
        # A local union can shift a parent's centre. Override that global veto
        # only using stable measured boundaries over the complete subband.
        bounds = np.asarray(
            [
                (a, b)
                for block, a, b in measured_bounds
                if start <= cols_by_block[block][-1] and cols_by_block[block][0] < end
            ]
        )
        centres = bounds.mean(axis=1)
        widths = bounds[:, 1] - bounds[:, 0]
        local_union_only = (
            any(merged)
            and np.mean(matched) >= 0.8
            and np.mean(merged) <= 0.2
            and np.ptp(centres) <= beam + 1e-6
            and np.ptp(widths) <= 2 * beam + 1e-6
        )
        if members & narrowing_counter:
            holds.append("narrowing_physical_width_weather_counterexample")
        if members & pure_counter and not local_union_only:
            holds.append("curved_or_drifting_centre")
        if any(state == "forked" for _, state in history):
            holds.append("ambiguous_fork_or_merge")
        identity += 1
        support = len(np.unique(np.fromiter(members, dtype=np.int64) % native.shape[1])) * dr
        output.append(
            RawObject(
                identity=identity,
                kind="subband",
                scale_m=20000.0,
                level_dbz=0.0,
                start_m=float(ranges[start]),
                end_m=float(ranges[end - 1] + dr),
                support_m=float(support),
                windows=tuple(windows),
                history_holds=tuple(holds),
                history_states=tuple(history),
                native_segment_start=int(np.flatnonzero(gaps[:lo])[-1] + 1)
                if gaps[:lo].any()
                else 0,
            )
        )
    return output
