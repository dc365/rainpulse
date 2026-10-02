"""Bind unified object decisions to the actual native writer's RAW evidence."""

import numpy as np

from ..arrays import mask, moment, native_geometry
from . import morphology_objects, unified_objects

PREFIX = unified_objects.PREFIX
DECISIONS = (
    "CANDIDATE_MASK",
    "STRONG_OBJECT_MASK",
    "PROPOSAL_MASK",
    "ACTION_MASK",
    "WEATHER_RETAIN_MASK",
    "SEGMENT_RETAIN_MASK",
    "ID",
)
NATIVE_EVIDENCE = (
    "NATIVE_RANGE_M",
    "NATIVE_AZ_DEG",
    "NATIVE_ORDER",
    "NATIVE_GOOD_MASK",
    "NATIVE_GAP_MASK",
    "BEAM_DEG",
    "VERSION_CODE",
    "BARRED_MASK",
    "MEASURED_DBZH",
    "MEASURED_SNR",
    "MEASURED_RHOHV",
    "DBZH_AVAILABLE_MASK",
    "SNR_AVAILABLE_MASK",
    "RHOHV_AVAILABLE_MASK",
    "RAW_WEATHER_MASK",
)


def canonical_native_fields(native):
    """Adapter outputs exported by the runner independently of object proof."""
    _, _, _, good, gaps = native_geometry(native)
    return {
        "NATIVE_QC_ORDER": np.broadcast_to(
            np.arange(native.shape[0])[:, None], native.shape
        ).astype("uint32"),
        "NATIVE_QC_GOOD_MASK": np.broadcast_to(good[:, None], native.shape).astype("uint8"),
        "NATIVE_QC_GAP_MASK": np.broadcast_to(gaps[:, None], native.shape).astype("uint8"),
    }


def bind_canonical_native(group, observed):
    """A self-consistent proof cannot substitute its own measurements."""
    shape = observed.shape
    for source, proof, dtype in (
        ("NATIVE_QC_ORDER", "NATIVE_ORDER", "uint32"),
        ("NATIVE_QC_GOOD_MASK", "NATIVE_GOOD_MASK", "uint8"),
        ("NATIVE_QC_GAP_MASK", "NATIVE_GAP_MASK", "uint8"),
    ):
        if source not in group:
            raise ValueError("missing canonical native geometry: " + source)
        value = np.asarray(group[source][:])
        if value.shape != shape or value.dtype != np.dtype(dtype):
            raise ValueError("invalid canonical native geometry: " + source)
        if not np.array_equal(value, np.asarray(group[PREFIX + proof][:])):
            raise ValueError("unified proof differs from canonical native geometry: " + source)
    for source, proof, size, axis in (
        ("range", "NATIVE_RANGE_M", shape[1], 0),
        ("azimuth", "NATIVE_AZ_DEG", shape[0], 1),
    ):
        if source not in group:
            raise ValueError("missing canonical native coordinate: " + source)
        coordinate = np.asarray(group[source][:], dtype="float64")
        if coordinate.shape != (size,) or not np.isfinite(coordinate).all():
            raise ValueError("invalid canonical native coordinate: " + source)
        expected = np.broadcast_to(coordinate[None, :] if axis == 0 else coordinate[:, None], shape)
        if not np.array_equal(expected, np.asarray(group[PREFIX + proof][:])):
            raise ValueError("unified proof differs from canonical native coordinate: " + source)
    good = mask(np.asarray(group["NATIVE_QC_GOOD_MASK"][:]), shape, "canonical good rays")
    for name in ("DBZH", "SNR", "RHOHV"):
        key = name + "_RAW"
        raw = np.asarray(group[key][:]) if key in group else np.full(shape, np.nan, "float32")
        if raw.shape != shape or raw.dtype != np.dtype("float32") or np.isinf(raw).any():
            raise ValueError("invalid canonical native moment: " + name)
        present = observed if name == "DBZH" else np.isfinite(raw) & good
        if name == "RHOHV":
            present &= (raw >= 0) & (raw <= 1)
        if not np.array_equal(
            present, np.asarray(group[PREFIX + name + "_AVAILABLE_MASK"][:]) == 1
        ):
            raise ValueError("unified proof differs from canonical moment availability: " + name)
        if not np.array_equal(
            np.where(present, raw, np.nan),
            np.asarray(group[PREFIX + "MEASURED_" + name][:]),
            equal_nan=True,
        ):
            raise ValueError("unified proof differs from canonical native moment: " + name)


def evidence(
    native,
    blocked,
    *,
    beam_width=None,
    subbands=False,
    separated=False,
    maximum_objects=50000,
    weather_protection=None,
):
    if separated and not subbands:
        raise ValueError("separated proof requires original subbands")
    result = morphology_objects.evidence(native, blocked, beam_width=beam_width, prefix=PREFIX)
    result[PREFIX + "POLICY_CODE"] = np.full(
        native.shape, 5 if separated else 3 if subbands else 1, "uint8"
    )
    result[PREFIX + "MAXIMUM_OBJECTS"] = np.full(native.shape, maximum_objects, "uint32")
    result[PREFIX + "RAW_WEATHER_MASK"] = mask(
        weather_protection, native.shape, "unified RAW weather"
    ).astype("uint8")
    return result


def evaluate(
    native,
    blocked,
    *,
    beam_width=None,
    subbands=False,
    separated=False,
    maximum_objects=50000,
    weather_protection=None,
):
    _, observed = moment(native, "DBZH")
    protection = mask(weather_protection, native.shape, "unified RAW weather") & observed
    barred = mask(blocked, native.shape, "unified original barriers") | protection
    arrays, report = unified_objects.evaluate(
        native,
        barred,
        beam_width=beam_width,
        subbands_enabled=subbands,
        separated_edges_enabled=separated,
        maximum_objects=maximum_objects,
        mode="audit",
    )
    arrays.update(
        evidence(
            native,
            barred,
            beam_width=beam_width,
            subbands=subbands,
            separated=separated,
            maximum_objects=maximum_objects,
            weather_protection=protection,
        )
    )
    return arrays, report


def validate_serialized(group, observed, blocked):
    """Restore actual native order and replay every object/gate decision."""
    shape = observed.shape

    def constant(key, dtype, allowed=None):
        if PREFIX + key not in group:
            raise ValueError("missing unified policy proof: " + key)
        value = np.asarray(group[PREFIX + key][:])
        if value.shape != shape or value.dtype != np.dtype(dtype) or np.unique(value).size != 1:
            raise ValueError("invalid unified policy proof: " + key)
        code = int(value.flat[0])
        if allowed is not None and code not in allowed:
            raise ValueError("unsupported unified policy proof: " + key)
        return code

    policy = constant("POLICY_CODE", "uint8", (1, 3, 5))
    budget = constant("MAXIMUM_OBJECTS", "uint32")
    if not 1 <= budget <= 100000:
        raise ValueError("invalid unified object budget")
    constant("VERSION_CODE", "uint8", (1,))
    decisions = DECISIONS + (("SUBBAND_PROPOSAL_MASK",) if policy in (3, 5) else ())
    expected = {
        PREFIX + k for k in (*NATIVE_EVIDENCE, *decisions, "POLICY_CODE", "MAXIMUM_OBJECTS")
    }
    actual = {k for k in group if k.startswith(PREFIX)}
    if actual != expected:
        raise ValueError("unified serialized proof field set differs")
    if "DBZH_RAW" not in group:
        raise ValueError("unified serialized proof requires immutable RAW")
    bind_canonical_native(group, observed)
    value = np.asarray(group[PREFIX + "RAW_WEATHER_MASK"][:])
    if value.dtype != np.dtype("uint8"):
        raise ValueError("invalid unified RAW weather dtype")
    protection = mask(value, shape, "unified RAW weather")
    if (protection & ~observed).any():
        raise ValueError("unified RAW weather created observations")

    def replay(arrays, native, barred, *, beam_width=None):
        unified_objects.validate(
            arrays,
            native,
            barred,
            beam_width=beam_width,
            maximum_objects=budget,
            subbands_enabled=policy in (3, 5),
            separated_edges_enabled=policy == 5,
            mode="audit",
        )

    morphology_objects.validate_serialized(
        group, observed, blocked | protection, prefix=PREFIX, replay=replay, decision_keys=decisions
    )
