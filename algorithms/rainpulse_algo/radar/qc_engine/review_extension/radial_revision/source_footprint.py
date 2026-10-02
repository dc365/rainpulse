"""Track residuals inside an independently frozen current source footprint.

RAW family membership alone never qualifies. The target and adjacent blocks
cannot train its original-source angular boundary; accepted tails never seed.
"""

import numpy as np

from ..arrays import mask, moment, native_geometry, runs

PREFIX = "RV2_SOURCE_FOOTPRINT_"
# Ordered decision trace, distinct from action/hold policy. A failed boundary
# must remain distinguishable from missing original sources or weather veto.
REJECTIONS = {
    0: "qualified",
    1: "no_original_source",
    2: "insufficient_original_support_or_rows",
    3: "native_angular_gap",
    4: "outside_original_row_or_range_extent",
    5: "original_stencil_barrier",
    6: "guard_excluded_reference_support",
    7: "insufficient_adjacent_source_boundary_blocks",
    8: "unstable_original_boundary",
    9: "outside_reference_boundary_or_distance",
    10: "current_measured_polar_retained",
    11: "qualified_original_object_shape",
}


def qualify(
    native,
    blocked,
    group,
    *,
    anchored_enabled=True,
    original_fan_enabled=True,
    sparse_target_enabled=True,
):
    """Combine frozen footprint and independently replayable object morphology.

    The caller's existing source-footprint policy still controls actions. A
    historical artifact without the new policy marker replays only v3 geometry.
    """
    fields, report = _qualify_original(native, blocked, group)
    if not anchored_enabled:
        return fields, report
    from .anchored_radial_shape import PREFIX as shape_prefix
    from .anchored_radial_shape import qualify as qualify_shape

    proposals, shape_report = qualify_shape(native, blocked, group)
    selected = proposals[shape_prefix + "QUALIFIED_MASK"] == 1
    added = selected & (fields[PREFIX + "QUALIFIED_MASK"] != 1)
    fields.update(proposals)
    fields[PREFIX + "ANCHORED_POLICY_CODE"] = np.full(
        native.shape, (3 if sparse_target_enabled else 2) if original_fan_enabled else 1, "uint8"
    )
    fields[PREFIX + "ANCHORED_ADDED_MASK"] = added.astype("uint8")
    for name in ("CANDIDATE_MASK", "QUALIFIED_MASK"):
        fields[PREFIX + name][added] = 1
    fields[PREFIX + "HOLD_REASON"][added] = 0
    fields[PREFIX + "REJECTION_CODE"][added] = 11
    fields[PREFIX + "RAW_PARENT_ID"][added] = np.asarray(group["RV2_RAW_FAN_ID"])[added]
    fan_added = np.zeros(native.shape, bool)
    fan_report = None
    if original_fan_enabled:
        from .original_fan_shape import PREFIX as fan_prefix
        from .original_fan_shape import qualify as qualify_fan

        fan_fields, fan_report = qualify_fan(
            native, blocked, group, sparse_target_enabled=sparse_target_enabled
        )
        fan_added = (fan_fields[fan_prefix + "QUALIFIED_MASK"] == 1) & (
            fields[PREFIX + "QUALIFIED_MASK"] != 1
        )
        fields.update(fan_fields)
        fields[PREFIX + "ORIGINAL_FAN_ADDED_MASK"] = fan_added.astype("uint8")
        for name in ("CANDIDATE_MASK", "QUALIFIED_MASK"):
            fields[PREFIX + name][fan_added] = 1
        fields[PREFIX + "HOLD_REASON"][fan_added] = 0
        fields[PREFIX + "REJECTION_CODE"][fan_added] = 11
        fields[PREFIX + "RAW_PARENT_ID"][fan_added] = np.asarray(group["RV2_RAW_FAN_ID"])[fan_added]
    report.update(
        version=(
            "original-source-footprint-v6-heldout-sparse-target"
            if sparse_target_enabled
            else "original-source-footprint-v5-complete-fan"
        )
        if original_fan_enabled
        else "original-source-footprint-v4-anchored-object",
        candidate_gates=int(fields[PREFIX + "CANDIDATE_MASK"].sum()),
        qualified_gates=int(fields[PREFIX + "QUALIFIED_MASK"].sum()),
        anchored_added_gates=int(added.sum()),
        anchored_shape=shape_report,
        original_fan_added_gates=int(fan_added.sum()),
        original_fan_shape=fan_report,
        candidate_decisions={
            name: int(
                (
                    (fields[PREFIX + "CANDIDATE_MASK"] == 1)
                    & (fields[PREFIX + "REJECTION_CODE"] == code)
                ).sum()
            )
            for code, name in REJECTIONS.items()
        },
    )
    return fields, report


def _qualify_original(native, blocked, group):
    r, az, dr, good, gaps = native_geometry(native)
    _, observed = moment(native, "DBZH")
    blocked = mask(blocked, native.shape, "source footprint barriers") | ~good[:, None]
    parent = np.asarray(group["RV2_RAW_FAN_ID"])
    seed = np.asarray(group["RV2_SOURCE_LEDGER_SEED_ID"])
    if (
        parent.shape != native.shape
        or seed.shape != native.shape
        or parent.dtype != np.dtype("uint32")
        or seed.dtype != np.dtype("uint32")
    ):
        raise ValueError("source footprint requires original parent and source IDs")
    candidate = (parent > 0) & (seed == 0) & observed & ~blocked
    qualified = np.zeros(native.shape, bool)
    hold = np.zeros(native.shape, "uint8")
    hold[candidate] = 1
    rejection = np.zeros(native.shape, "uint8")
    rejection[candidate] = 1
    proof = {
        k: np.full(native.shape, np.nan, "float32")
        for k in (
            "LEFT_DEG",
            "RIGHT_DEG",
            "START_M",
            "END_M",
            "REFERENCE_SUPPORT_M",
            "REFERENCE_BLOCKS",
        )
    }
    original = (seed > 0) & observed & ~blocked
    blocks = (r // 20000.0).astype(int)
    source_components = 0
    for identity in np.unique(parent[candidate]):
        clues = original & (parent == identity)
        parent_rows = np.unique(np.where(clues)[0])
        members = candidate & (parent == identity)
        if not len(parent_rows):
            continue
        # RAW tiles are local association regions, not original-source bounds.
        # Recover only the complete frozen IDs touching this tile; freeze the
        # tile's original angular rows so distant gates cannot widen a bundle.
        source_ids = np.unique(seed[clues])
        parent_source = original & np.isin(seed, source_ids)
        parent_source[~np.isin(np.arange(native.shape[0]), parent_rows)] = False
        rejection[members] = 2
        # Freeze disconnected ORIGINAL source bundles separately. Their empty
        # angular gap is not part of either bundle and can never train/grow one.
        islands = np.split(parent_rows, np.flatnonzero(np.diff(parent_rows) > 1) + 1)
        if any(len(island) >= 2 for island in islands):
            rejection[members] = 4
        for island in islands:
            if len(island) < 2:
                continue
            source = parent_source.copy()
            source[: island[0]] = False
            source[island[-1] + 1 :] = False
            source_components += 1
            _qualify_component(
                native,
                source,
                members,
                blocked,
                r,
                az,
                dr,
                gaps,
                blocks,
                rejection,
                hold,
                qualified,
                proof,
            )
    # Weather-like current polar measurements veto this geometric-only path.
    # This is conservative retention, not a claim of independent weather truth.
    rho, rho_ok = moment(native, "RHOHV")
    snr, snr_ok = moment(native, "SNR")
    retain = qualified & rho_ok & snr_ok & (rho >= 0.95) & (snr >= 10.0)
    qualified[retain] = False
    hold[retain] = 3
    rejection[retain] = 10
    for v in proof.values():
        v[retain] = np.nan
    return {
        PREFIX + "CANDIDATE_MASK": candidate.astype("uint8"),
        PREFIX + "QUALIFIED_MASK": qualified.astype("uint8"),
        PREFIX + "HOLD_REASON": hold,
        PREFIX + "REJECTION_CODE": rejection,
        PREFIX + "RAW_PARENT_ID": np.where(qualified, parent, 0).astype("uint32"),
        **{PREFIX + k: v for k, v in proof.items()},
    }, {
        "version": "original-source-footprint-v3-complete-ledger",
        "candidate_gates": int(candidate.sum()),
        "qualified_gates": int(qualified.sum()),
        "actions": 0,
        "original_source_components": source_components,
        "current_polar_retained_gates": int(retain.sum()),
        "candidate_decisions": {
            name: int((candidate & (rejection == code)).sum()) for code, name in REJECTIONS.items()
        },
        "source_claim": False,
        "recursive_growth": False,
        "filled_gates": 0,
    }


def _qualify_component(
    native,
    source,
    parent_members,
    blocked,
    r,
    az,
    dr,
    gaps,
    blocks,
    rejection,
    hold,
    qualified,
    proof,
):
    rr, cc = np.where(source)
    # The source itself carries its parent's row/gate footprint. Membership
    # is supplied by the caller; no child candidate becomes a new source.
    members = parent_members.copy()
    members[: rr.min()] = False
    members[rr.max() + 1 :] = False
    rejection[members] = 2
    if len(cc) * dr < 10000.0 or len(np.unique(rr)) < 2:
        return
    row_lo, row_hi = int(rr.min()), int(rr.max()) + 1
    rejection[members] = 3
    if gaps[row_lo : row_hi - 1].any():
        return
    rejection[members] = 4
    angles = np.rad2deg(np.unwrap(np.deg2rad(az[row_lo:row_hi])))
    start, end = float(r[cc].min()), float(r[cc].max())
    # A barrier anywhere in the ORIGINAL angular stencil splits the range.
    safe = ~blocked[row_lo:row_hi].any(axis=0)
    targets = members.copy()
    targets &= (r[None, :] >= start) & (r[None, :] <= end)
    rejection[targets] = 5
    for lo, hi in runs(safe):
        tr, tc = np.where(targets[:, lo:hi])
        tc += lo
        for block in np.unique(blocks[tc]):
            select = blocks[tc] == block
            rows, cols = tr[select], tc[select]
            rejection[rows, cols] = 6
            refs = (cc >= lo) & (cc < hi) & (abs(blocks[cc] - block) > 1)
            ref_rows, ref_cols = rr[refs], cc[refs]
            distinct = np.unique(blocks[ref_cols])
            if (
                len(ref_cols) * dr < 10000.0
                or len(distinct) < 3
                or np.ptp(r[ref_cols]) < 60000.0 - dr
                or len(np.unique(ref_rows)) < 2
            ):
                continue
            rejection[rows, cols] = 7
            left = []
            right = []
            for b in distinct:
                rows_in_block = np.unique(ref_rows[blocks[ref_cols] == b])
                if len(rows_in_block) < 2 or np.any(np.diff(rows_in_block) > 1):
                    continue
                a = angles[rows_in_block - row_lo]
                left.append(float(a.min()))
                right.append(float(a.max()))
            if len(left) < 3:
                continue
            rejection[rows, cols] = 8
            beam = float(np.median(np.diff(angles)))
            if not np.isfinite(beam) or beam <= 0:
                continue
            # The template comes from original source gates only. A weak
            # peripheral RAW ray cannot enlarge it by becoming a member.
            ll, hh = float(np.median(left)), float(np.median(right))
            if (
                hh <= ll
                or hh - ll > 12.0
                or np.ptp(left) > 2 * beam + 1e-6
                or np.ptp(right) > 2 * beam + 1e-6
            ):
                continue
            hold[rows, cols] = 2
            rejection[rows, cols] = 9
            actual = angles[rows - row_lo]
            inside = (actual >= ll - 1e-6) & (actual <= hh + 1e-6)
            nearest = np.min(abs(r[cols, None] - r[ref_cols][None, :]), axis=1)
            accepted = inside & (nearest <= 120000.0)
            rows, cols = rows[accepted], cols[accepted]
            qualified[rows, cols] = True
            hold[rows, cols] = 0
            rejection[rows, cols] = 0
            for key, value in {
                "LEFT_DEG": ll,
                "RIGHT_DEG": hh,
                "START_M": start,
                "END_M": end,
                "REFERENCE_SUPPORT_M": len(ref_cols) * dr,
                "REFERENCE_BLOCKS": len(left),
            }.items():
                proof[key][rows, cols] = value


def evidence(native):
    """Persist native coordinates/order and measured veto inputs for replay.

    Arrays use ray/gate shape so the existing native restore and Zarr writer
    preserve their alignment. Repeated coordinates compress without resampling.
    """
    r, az, _, good, gaps = native_geometry(native)
    out = {}
    for key, values, dtype in (
        ("NATIVE_RANGE_M", r[None, :], "float64"),
        ("NATIVE_AZ_DEG", az[:, None], "float64"),
        ("NATIVE_GOOD_MASK", good[:, None], "uint8"),
        ("NATIVE_GAP_MASK", gaps[:, None], "uint8"),
        ("NATIVE_ORDER", np.arange(native.shape[0])[:, None], "uint32"),
    ):
        out[PREFIX + key] = np.broadcast_to(values, native.shape).astype(dtype).copy()
    # RAW DBZH is immutable input evidence, not a reconstructed zero field.
    for name in ("DBZH", "RHOHV", "SNR"):
        values, available = moment(native, name)
        out[PREFIX + "MEASURED_" + name] = np.where(available, values, np.nan).astype("float32")
        out[PREFIX + name + "_AVAILABLE_MASK"] = available.astype("uint8")
    return out


def validate(group, observed, blocked):
    """Recompute eligibility from original parent/source gates, never scalars."""
    from types import SimpleNamespace

    shape = observed.shape

    def get(key):
        return np.asarray(group[PREFIX + key][:])

    typed = {
        "NATIVE_RANGE_M": "float64",
        "NATIVE_AZ_DEG": "float64",
        "NATIVE_GOOD_MASK": "uint8",
        "NATIVE_GAP_MASK": "uint8",
        "NATIVE_ORDER": "uint32",
        "MEASURED_RHOHV": "float32",
        "MEASURED_SNR": "float32",
        "RHOHV_AVAILABLE_MASK": "uint8",
        "SNR_AVAILABLE_MASK": "uint8",
    }
    anchored = PREFIX + "ANCHORED_POLICY_CODE" in group
    if anchored:
        typed.update(
            MEASURED_DBZH="float32", DBZH_AVAILABLE_MASK="uint8", ANCHORED_POLICY_CODE="uint8"
        )
    for key, dtype in typed.items():
        value = get(key)
        if value.shape != shape or value.dtype != np.dtype(dtype) or np.isinf(value).any():
            raise ValueError("invalid source footprint native evidence: " + key)
    ranges, angles, order = (get(k) for k in ("NATIVE_RANGE_M", "NATIVE_AZ_DEG", "NATIVE_ORDER"))
    if (
        not np.isfinite(ranges).all()
        or not np.isfinite(angles).all()
        or not np.array_equal(ranges, np.broadcast_to(ranges[0], shape))
        or not np.array_equal(angles, np.broadcast_to(angles[:, :1], shape))
        or not np.array_equal(order, np.broadcast_to(order[:, :1], shape))
        or not np.array_equal(np.sort(order[:, 0]), np.arange(shape[0]))
    ):
        raise ValueError("source footprint native coordinate/order mismatch")
    rows = np.argsort(order[:, 0])
    good, gaps = (mask(get(k), shape, k) for k in ("NATIVE_GOOD_MASK", "NATIVE_GAP_MASK"))
    for value in (good, gaps):
        if not np.array_equal(value, np.broadcast_to(value[:, :1], shape)):
            raise ValueError("source footprint ray geometry varies by gate")
    # Bind to other independently serialized original coordinates where present.
    for prefix in ("RV2_RAW_FAN_", "RV2_SOURCE_LEDGER_"):
        if prefix + "RANGE_M" in group:
            reference = np.asarray(group[prefix + "RANGE_M"][:])
            valid = np.isfinite(reference)
            if not np.allclose(ranges[valid], reference[valid], atol=0.02, rtol=0):
                raise ValueError("source footprint range differs from original ledger")
    if "RV2_SOURCE_LEDGER_REFERENCE_AZ_DEG" in group:
        reference = np.asarray(group["RV2_SOURCE_LEDGER_REFERENCE_AZ_DEG"][:])
        valid = np.isfinite(reference)
        delta = (angles[valid] - reference[valid] + 180.0) % 360.0 - 180.0
        if np.any(abs(delta) > 0.0001):
            raise ValueError("source footprint angle differs from original ledger")
    fields = {"DBZH": np.where(observed[rows], 0.0, np.nan).astype("float32")}
    available = {"DBZH": observed[rows]}
    policy = 0
    if anchored:
        values = np.unique(get("ANCHORED_POLICY_CODE"))
        if len(values) != 1 or values[0] not in (1, 2, 3):
            raise ValueError("unsupported anchored source policy")
        policy = int(values[0])
    for name in ("DBZH", "RHOHV", "SNR") if anchored else ("RHOHV", "SNR"):
        value = get("MEASURED_" + name)
        present = mask(get(name + "_AVAILABLE_MASK"), shape, name + " measured availability")
        if not np.array_equal(np.isfinite(value), present):
            raise ValueError("source footprint polar availability mismatch")
        if name == "DBZH" and not np.array_equal(present, observed):
            raise ValueError("anchored DBZH differs from immutable observation availability")
        fields[name], available[name] = value[rows], present[rows]
    native = SimpleNamespace(
        shape=shape,
        ranges=ranges[0],
        azimuth=angles[rows, 0],
        geometry_good=good[rows, 0],
        gap_after=gaps[rows, 0],
        fields=fields,
        field_available=available,
    )
    originals = {
        k: np.asarray(group[k][:])[rows] for k in ("RV2_RAW_FAN_ID", "RV2_SOURCE_LEDGER_SEED_ID")
    }
    expected, _ = qualify(
        native,
        blocked[rows],
        originals,
        anchored_enabled=anchored,
        original_fan_enabled=policy >= 2,
        sparse_target_enabled=policy == 3,
    )
    for key, value in expected.items():
        actual = np.asarray(group[key][:])[rows]
        if (
            actual.dtype != value.dtype
            or actual.shape != shape
            or not np.array_equal(actual, value, equal_nan=True)
        ):
            raise ValueError("source footprint original evidence replay differs: " + key)
