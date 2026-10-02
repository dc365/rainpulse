"""Held-out complete RAW fan geometry; diagnostic proposals, never new sources."""

import hashlib

import numpy as np

from ..arrays import mask, moment, native_geometry, runs
from .geometry import ResourceLimit

PREFIX = "RV2_ORIGINAL_FAN_SHAPE_"


def qualify(native, blocked, group, *, sparse_target_enabled=False):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    snr, snr_ok = moment(native, "SNR")
    rho, rho_ok = moment(native, "RHOHV")
    blocked = mask(blocked, native.shape, "original fan barriers") | ~good[:, None]
    seed = np.asarray(group["RV2_SOURCE_LEDGER_SEED_ID"])
    parent = np.asarray(group["RV2_RAW_FAN_ID"])
    for value in (seed, parent):
        if value.shape != native.shape or value.dtype != np.dtype("uint32"):
            raise ValueError("frozen original identities required")
    if np.prod(native.shape) > 2000000:
        raise ResourceLimit("original fan native budget exceeded")
    out = {
        PREFIX + "QUALIFIED_MASK": np.zeros(native.shape, "uint8"),
        PREFIX + "SOURCE_ID": np.zeros(native.shape, "uint32"),
        PREFIX + "AMBIGUOUS_MASK": np.zeros(native.shape, "uint8"),
        PREFIX + "SOURCE_REFERENCE_MASK": np.zeros(native.shape, "uint8"),
    }
    raw = observed & (z >= 0)
    known = observed | (snr_ok & (snr >= -50) & (snr <= 100))
    weather = rho_ok & snr_ok & (rho >= 0.95) & (snr >= 10)
    excluded = blocked | weather
    blocks = (r // 20000).astype(int)
    angles = np.rad2deg(np.unwrap(np.deg2rad(az)))
    records, work = [], 0
    # Boundaries are measured once on full pre-clear RAW. Source-marked
    # shoulders are actual foreground; no subtraction can invent dry sides.
    profiles = {}
    for block in np.unique(blocks):
        cc = np.flatnonzero(blocks == block)
        density = raw[:, cc].mean(axis=1)
        occupied = density >= 0.2
        # At most two internal native rows may be sparse, with both ORIGINAL
        # measured edges present. This operation nominates no new source IDs.
        for a, b in runs(~occupied):
            if a > 0 and b < len(az) and b - a <= 2:
                occupied[a:b] = True
        bands = []
        for lo, hi in runs(occupied):
            if lo == 0 or hi == len(az) or hi - lo < 3:
                continue
            aa, bb = lo - 1, hi + 1
            steps = np.diff(angles[aa:bb])
            if (
                not good[aa:bb].all()
                or gaps[aa : bb - 1].any()
                or (steps <= 0).any()
                or steps.max() > 1.5 * steps.min()
                or angles[hi - 1] - angles[lo] > 90
            ):
                continue
            # Weather islands remain measured returns on BOTH shoulders.
            # Inside, use only unprotected foreground with the ORIGINAL
            # window denominator, never reclassify excluded cells as dry air
            # or inflate density by deleting them from the denominator.
            interior = (raw[lo:hi][:, cc] & ~excluded[lo:hi][:, cc]).mean(axis=1)
            if (
                known[aa:bb][:, cc].mean(axis=1).min() < 0.8
                or excluded[lo:hi][:, cc].mean(axis=1).max() > 0.2
                or interior.mean() < 0.6
                or max(density[lo - 1], density[hi]) > 0.2
            ):
                continue
            bands.append((lo, hi))
        profiles[int(block)] = bands
        work += len(az) * len(cc)
    original_bands = sorted({band for bands in profiles.values() for band in bands})
    for block, local_bands in profiles.items():
        cc = np.flatnonzero(blocks == block)
        # The original RAW object is frozen by held-out distances. Its target
        # window may contain only fragments; those fragments train no edges.
        bands = original_bands if sparse_target_enabled else local_bands
        for lo, hi in bands:
            if sparse_target_enabled:
                work += (hi - lo + 2) * len(cc)
                if work > 50000000:
                    raise ResourceLimit("original fan work exceeded; no partial result")
                if (
                    known[lo - 1 : hi + 1][:, cc].mean(axis=1).min() < 0.8
                    or max(raw[lo - 1, cc].mean(), raw[hi, cc].mean()) > 0.2
                ):
                    continue
            # Target plus adjacent windows train neither edges nor source.
            refs = [v for v, pairs in profiles.items() if abs(v - block) > 1 and (lo, hi) in pairs]
            if len(refs) < 5 or (max(refs) - min(refs)) * 20000 < 150000:
                continue
            rc = np.flatnonzero(np.isin(blocks, refs))
            valid_ids, source_rows, source_cols = [], [], []
            source_refs = {}
            for row in range(lo, hi):
                for sid in np.unique(seed[row, rc]):
                    if not sid:
                        continue
                    work += len(r)
                    if work > 50000000:
                        raise ResourceLimit("original fan work exceeded; no partial result")
                    original = np.flatnonzero(
                        (seed[row] == sid) & observed[row] & ~blocked[row] & ~weather[row]
                    )
                    held = original[np.isin(blocks[original], refs)]
                    if (
                        len(held) * dr >= 10000
                        and len(np.unique(blocks[held])) >= 3
                        and np.ptp(r[held]) >= 100000
                    ):
                        valid_ids.append(int(sid))
                        source_rows.append(row)
                        source_cols.extend(original.tolist())
                        source_refs[f"{int(row)}:{int(sid)}"] = dict(
                            row=int(row), source_id=int(sid), columns=held.tolist()
                        )
            if len(set(source_rows)) < 3:
                continue
            # Range and parent membership come only from original identities.
            start, end = min(source_cols), max(source_cols) + 1
            ref_seed = (
                np.isin(seed[lo:hi][:, rc], valid_ids)
                & observed[lo:hi][:, rc]
                & ~blocked[lo:hi][:, rc]
                & ~weather[lo:hi][:, rc]
            )
            parents = np.unique(parent[lo:hi][:, rc][ref_seed])
            parents = parents[parents > 0]
            target = (
                raw[lo:hi][:, cc]
                & (seed[lo:hi][:, cc] == 0)
                & ~blocked[lo:hi][:, cc]
                & ~weather[lo:hi][:, cc]
                & np.isin(parent[lo:hi][:, cc], parents)
                & (cc[None, :] >= start)
                & (cc[None, :] < end)
            )
            # Frozen family membership is not a way around a protected island:
            # choose the nearest ORIGINAL source ray before tracing a target.
            # No accepted weak member can supply the missing bridge.
            target_owners = np.zeros(target.shape, "uint32")
            for local_row, row in enumerate(range(lo, hi)):
                reference = min(
                    source_refs.values(),
                    key=lambda ref: (
                        abs(angles[row] - angles[ref["row"]]),
                        ref["source_id"],
                        ref["row"],
                    ),
                )
                nearest = reference["source_id"]
                source_row = reference["row"]
                a, b = min(row, source_row), max(row, source_row) + 1
                target[local_row] &= ~(blocked[a:b] | weather[a:b])[:, cc].any(axis=0)
                target_owners[local_row, target[local_row]] = nearest
            yy, local_x = np.where(target)
            owners = target_owners[yy, local_x]
            yy, xx = yy + lo, cc[local_x]
            owner = out[PREFIX + "SOURCE_ID"][yy, xx]
            ambiguous = (owner > 0) & (owner != owners) | (
                out[PREFIX + "AMBIGUOUS_MASK"][yy, xx] == 1
            )
            out[PREFIX + "AMBIGUOUS_MASK"][yy[ambiguous], xx[ambiguous]] = 1
            out[PREFIX + "QUALIFIED_MASK"][yy[ambiguous], xx[ambiguous]] = 0
            out[PREFIX + "SOURCE_ID"][yy[ambiguous], xx[ambiguous]] = 0
            yy, xx, owners = yy[~ambiguous], xx[~ambiguous], owners[~ambiguous]
            out[PREFIX + "QUALIFIED_MASK"][yy, xx] = 1
            out[PREFIX + "SOURCE_ID"][yy, xx] = owners
            for ref in source_refs.values():
                out[PREFIX + "SOURCE_REFERENCE_MASK"][ref["row"], ref["columns"]] = 1
            records.append(
                dict(
                    target_block=block,
                    original_source_ids=valid_ids,
                    original_parent_ids=parents.tolist(),
                    frozen_rows=[int(lo), int(hi)],
                    start_m=float(r[start]),
                    end_m=float(r[end - 1] + dr),
                    reference_blocks=refs,
                    reference_gate_column_count=len(rc),
                    reference_gate_columns_sha256=hashlib.sha256(
                        np.asarray(rc, dtype="<u4").tobytes()
                    ).hexdigest(),
                    source_reference_gates={
                        key: dict(
                            row=ref["row"],
                            source_id=ref["source_id"],
                            gate_count=len(ref["columns"]),
                            columns_sha256=hashlib.sha256(
                                np.asarray(ref["columns"], dtype="<u4").tobytes()
                            ).hexdigest(),
                        )
                        for key, ref in source_refs.items()
                    },
                    proposed_gates=len(yy),
                )
            )
            work += (hi - lo) * len(rc)
            if work > 50000000:
                raise ResourceLimit("original fan work exceeded; no partial result")
    return out, dict(
        version=(
            "complete-original-fan-shape-v6-heldout-sparse-target"
            if sparse_target_enabled
            else "complete-original-fan-shape-v5-compact-native-proof"
        ),
        records=records,
        qualified_gates=int(out[PREFIX + "QUALIFIED_MASK"].sum()),
        work=int(work),
        recursive_growth=False,
        action_authority=False,
        product_writes=False,
    )


def validate(arrays, native, blocked, group, *, sparse_target_enabled=False):
    expected, _ = qualify(native, blocked, group, sparse_target_enabled=sparse_target_enabled)
    if arrays.keys() != expected.keys():
        raise ValueError("original fan evidence fields differ")
    for key, value in expected.items():
        actual = np.asarray(arrays[key])
        if actual.dtype != value.dtype or not np.array_equal(actual, value):
            raise ValueError("original fan replay differs: " + key)
