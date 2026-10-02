"""RAW object nomination, joint evidence, and local gate disposition.

This candidate reuses complete variable-width RAW tracking. It never depends
on the legacy strong mask or rejected-source IDs. Evidence scores are not
weather probabilities and the default is read-only audit.
"""

import numpy as np

from ..arrays import mask, moment, native_geometry
from .geometry import ResourceLimit
from .variable_morphology import detect

PREFIX = "RV2_UNIFIED_"
VERSION = "unified-native-objects-candidate-v1"
HARD_HOLDS = frozenset(
    (
        "ambiguous_fork_or_merge",
        "curved_or_drifting_centre",
        "narrowing_physical_width_weather_counterexample",
    )
)


def _classify(obj, beam):
    """Geometry and independently measured edges are mandatory families."""
    widths = np.asarray([w.right_deg - w.left_deg for w in obj.windows])
    median_width = float(np.median(widths))
    tolerance = max(2 * beam, 0.75 * median_width)
    stable = np.abs(widths - median_width) <= tolerance
    centre = np.asarray([(w.left_deg + w.right_deg) / 2 for w in obj.windows])
    stable &= np.abs(centre - np.median(centre)) <= max(beam, 0.25 * median_width)
    known = np.asarray([w.known_fraction for w in obj.windows])
    contrast = np.asarray([w.contrast_fraction for w in obj.windows])
    measured = (known >= 0.8) & (contrast >= 0.8) & stable
    span = obj.end_m - obj.start_m
    windows = len({w.block for w in obj.windows})
    kind = "line" if median_width <= 2 * beam else "fan"
    aspect = span / max(obj.end_m * np.deg2rad(median_width), 1.0)
    geometry = (
        (span >= 80000 and obj.support_m >= 10000 and windows >= 4 and aspect >= 3)
        if kind == "line"
        else (span >= 100000 and obj.support_m >= 20000 and windows >= 6)
    )
    short = (
        kind == "line"
        and 20000 <= span < 80000
        and obj.support_m >= 0.7 * span
        and windows >= 4
        and aspect >= 8
    )
    geometry |= short
    if obj.kind == "projected":
        geometry = (
            span >= 150000
            and obj.support_m >= 20000
            and windows >= 5
            and aspect >= 6
            and len(obj.origin_ids) >= 2
        )
    hard = sorted(HARD_HOLDS.intersection(obj.history_holds))
    if median_width > 90 or stable.mean() < 0.8:
        hard.append("unstable_original_envelope")
    if not geometry:
        hard.append("insufficient_object_geometry")
    # Local contaminated or unknown windows lose support; they cannot become
    # clean votes, and are excluded again during gate disposition below.
    edge_score = float(measured.mean())
    score = 0.4 * float(geometry) + 0.3 * float(stable.mean()) + 0.3 * edge_score
    minimum_edge = 0.95 if short else 0.9 if obj.kind == "projected" else 0.65
    minimum_score = 0.98 if short else 0.94 if obj.kind == "projected" else 0.88
    confirmed = not hard and edge_score >= minimum_edge and score >= minimum_score
    return (
        confirmed,
        measured,
        dict(
            kind=kind,
            evidence_score=score,
            score_is_probability=False,
            geometry_family=bool(geometry),
            stable_window_fraction=float(stable.mean()),
            edge_window_fraction=edge_score,
            independent_windows=windows,
            span_m=float(span),
            support_m=obj.support_m,
            width_median_deg=median_width,
            holds=hard if hard else ([] if confirmed else ["insufficient_joint_evidence"]),
        ),
    )


def evaluate(
    native,
    blocked,
    *,
    beam_width=None,
    mode="audit",
    maximum_objects=50000,
    projection_enabled=True,
):
    if mode not in ("audit", "quarantine"):
        raise ValueError("unified object mode must be audit or quarantine")
    ranges, az, dr, good, gaps = native_geometry(native)
    blocked = mask(blocked, native.shape, "unified object barriers") | ~good[:, None]
    z, observed = moment(native, "DBZH")
    snr, sa = moment(native, "SNR")
    rho, ra = moment(native, "RHOHV")
    weather = observed & ra & sa & (rho >= 0.95) & (snr >= 10)
    steps = (np.diff(az) + 360) % 360
    measured_steps = steps[(steps > 0) & ~gaps[:-1]]
    if not len(measured_steps):
        raise ValueError("no measured angular spacing for unified objects")
    beam = max(float(np.median(measured_steps)), beam_width or 0.0)
    objects = []
    member_count = 0

    def collect(obj):
        nonlocal member_count
        member_count += sum(len(w.members) for w in obj.windows)
        if member_count > 5000000:
            raise ResourceLimit("unified original membership budget exceeded; no partial result")
        objects.append(obj)

    # Original evidence collection is completed before any object is judged.
    detect(
        native, blocked, beam_width=beam_width, maximum_objects=maximum_objects, object_sink=collect
    )
    if projection_enabled:
        from .projected_objects import nominate

        projected = nominate(objects, beam, ranges, dr)
        for obj in projected:
            collect(obj)
    arrays = {
        PREFIX + name: np.zeros(native.shape, "uint8")
        for name in (
            "CANDIDATE_MASK",
            "STRONG_OBJECT_MASK",
            "PROPOSAL_MASK",
            "ACTION_MASK",
            "WEATHER_RETAIN_MASK",
            "SEGMENT_RETAIN_MASK",
        )
    }
    arrays[PREFIX + "ID"] = np.zeros(native.shape, "uint32")
    records = []
    for obj in objects:
        members = np.asarray(obj.members, dtype=np.int64)
        arrays[PREFIX + "CANDIDATE_MASK"].flat[members] = 1
        confirmed, measured, features = _classify(obj, beam)
        proposals = []
        if confirmed:
            arrays[PREFIX + "STRONG_OBJECT_MASK"].flat[members] = 1
        for window, supported in zip(obj.windows, measured, strict=True):
            index = np.asarray(window.members, dtype=np.int64)
            rr, cc = np.divmod(index, native.shape[1])
            local = observed[rr, cc] & ~blocked[rr, cc] & ~weather[rr, cc]
            # Aggregate evidence never makes unknown shoulders observed. Both
            # sides at the actual target coordinate must have measurements.
            for row in (window.left_row, window.right_row):
                if row is None:
                    local[:] = False
                else:
                    noise = sa[row, cc] & (snr[row, cc] <= 3)
                    local &= ~blocked[row, cc] & (
                        (observed[row, cc] & (z[row, cc] <= z[rr, cc] - 6))
                        | (~observed[row, cc] & noise)
                    )
            allow = local & confirmed & supported
            arrays[PREFIX + "WEATHER_RETAIN_MASK"].flat[index[weather[rr, cc]]] = 1
            arrays[PREFIX + "SEGMENT_RETAIN_MASK"].flat[index[~allow]] = 1
            chosen = index[allow]
            arrays[PREFIX + "PROPOSAL_MASK"].flat[chosen] = 1
            arrays[PREFIX + "ID"].flat[chosen] = obj.identity
            proposals.extend(map(int, chosen))
        records.append(
            dict(
                id=obj.identity,
                **features,
                confirmed=confirmed,
                member_gates=len(members),
                membership_sha256=obj.membership_sha256,
                proposal_gates=len(set(proposals)),
                scale_m=obj.scale_m,
                level_dbz=obj.level_dbz,
                nomination_kind=obj.kind,
                original_nomination_ids=list(obj.origin_ids),
                native_segment_start=obj.native_segment_start,
                original_history_holds=list(obj.history_holds),
            )
        )
    # A duplicate abstaining nomination cannot cancel another complete proof.
    hit = arrays[PREFIX + "PROPOSAL_MASK"] == 1
    arrays[PREFIX + "SEGMENT_RETAIN_MASK"][hit] = 0
    if mode == "quarantine":
        arrays[PREFIX + "ACTION_MASK"][hit] = 1
    return arrays, dict(
        version=VERSION,
        mode=mode,
        objects=records,
        candidate_gates=int(arrays[PREFIX + "CANDIDATE_MASK"].sum()),
        proposal_gates=int(hit.sum()),
        action_gates=int(arrays[PREFIX + "ACTION_MASK"].sum()),
        original_source_required=False,
        recursive_growth=False,
        filled_gates=0,
        product_writes=False,
        independent_weather_truth=False,
    )


def validate(arrays, native, blocked, **kwargs):
    expected, _ = evaluate(native, blocked, **kwargs)
    if set(arrays) != set(expected):
        raise ValueError("unified object proof field set differs")
    for key, value in expected.items():
        actual = np.asarray(arrays[key])
        if (
            actual.dtype != value.dtype
            or actual.shape != value.shape
            or not np.array_equal(actual, value)
        ):
            raise ValueError("unified object proof differs: " + key)
