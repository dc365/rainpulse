"""Bounded RAW physical speckles; measured noise is support, never rain absence."""

from types import SimpleNamespace

import numpy as np

from .volume_review.clutter_fusion.isolation_geometry import empty, inspect
from .volume_review.data import ResourceLimit, Sweep, array_digest, checked_mask


def physical_candidates(native, cfg, *, baseline_eligible, protected, pol_bad, low_snr):
    policy = cfg.speckle_physical_support
    shape = native.shape
    eligible = checked_mask(baseline_eligible, shape, "speckle eligibility")
    protected = checked_mask(protected, shape, "speckle protection")
    polar = checked_mask(pol_bad, shape, "speckle polar evidence")
    noise = checked_mask(low_snr, shape, "speckle noise evidence")
    sweep = Sweep(
        native.name,
        native.azimuth,
        native.elevation,
        native.ranges,
        native.fields,
        native.field_available,
        native.geometry_good,
        native.gap_after,
        no_echo=getattr(native, "no_echo", None),
    )
    z, observed = sweep.moment("DBZH")
    snr, sa = sweep.moment("SNR")
    rho, ra = sweep.moment("RHOHV")
    # One reliable weather gate protects its COMPLETE RAW object and nearby ring.
    weather = protected | (observed & ra & sa & (rho >= 0.95) & (snr >= 10.0))
    geometry_config = SimpleNamespace(
        isolated_objects=policy.model_copy(update={"weak_diagnostic_enabled": False}),
        minimum_range_m=cfg.minimum_range_m,
        maximum_range_m=float(sweep.ranges[-1]),
        protected_dbz=cfg.speckle_maximum_dbz,
    )
    try:
        result = inspect(
            sweep,
            geometry_config,
            {"CF_LEGACY_PROTECTED_MASK": weather.astype("uint8")},
            quiet_snr_db=0.0,
        )
    except ResourceLimit as exc:
        # Withdraw the WHOLE optional sweep review, never a partially visited set.
        result = SimpleNamespace(
            arrays=empty(shape),
            summary={
                "status": "RESOURCE_LIMIT_ABSTAINED",
                "reason": str(exc),
                "objects": 0,
                "small_objects": 0,
                "isolated_objects": 0,
                "noise_limited_support_gates": 0,
            },
        )
    g = result.arrays
    ids = g["CF_ISO_OBJECT_ID"]
    # A target must have its OWN current evidence. Missing polarization is never evidence.
    current = observed & (
        (polar & ra & sa & (snr >= cfg.minimum_snr_db))
        | (noise & sa & (snr >= -50) & (snr < cfg.minimum_snr_db))
    )
    counts = np.bincount(ids.ravel(), minlength=int(ids.max()) + 1)
    positive = np.bincount(ids.ravel(), weights=current.ravel(), minlength=len(counts))
    fraction = np.divide(positive, counts, out=np.zeros(len(counts)), where=counts > 0)
    fraction[0] = 0
    target = observed & eligible & (z >= cfg.minimum_echo_dbz) & (z < cfg.speckle_maximum_dbz)
    candidate = target & current & (g["CF_ISO_ISOLATED_MASK"] == 1)
    candidate &= fraction[ids] >= policy.minimum_object_evidence_fraction
    candidate &= g["CF_ISO_AREA_KM2"] <= cfg.speckle_maximum_area_km2
    candidate &= g["CF_ISO_DIAMETER_M"] <= cfg.speckle_maximum_span_m
    candidate &= ~weather
    arrays = {
        "V6_SPECKLE_CANDIDATE_MASK": candidate.astype("uint8"),
        "V6_SPECKLE_OBJECT_ID": np.where(candidate, ids, 0).astype("uint32"),
        "V6_SPECKLE_AREA_KM2": np.where(candidate, g["CF_ISO_AREA_KM2"], np.nan).astype("float32"),
        "V6_RAW_NEIGHBOUR_OBS_FRACTION": g["CF_ISO_KNOWN_FRACTION"],
        "V6_RAW_NEIGHBOUR_ECHO_FRACTION": g["CF_ISO_POSSIBLE_ECHO_FRACTION"],
        "V6_PHYSICAL_SPECKLE_CURRENT_MASK": current.astype("uint8"),
        "V6_PHYSICAL_SPECKLE_OBJECT_EVIDENCE_FRACTION": fraction[ids].astype("float32"),
        **{k.replace("CF_ISO_", "V6_PHYSICAL_SPECKLE_"): v for k, v in g.items()},
    }
    prefix = "V6_PHYSICAL_SPECKLE_INPUT_"
    arrays.update(
        {
            prefix + "PROTECTED_MASK": (protected & observed).astype("uint8"),
            prefix + "POLAR_MASK": (polar & observed).astype("uint8"),
            prefix + "LOW_SNR_MASK": (noise & observed).astype("uint8"),
            prefix + "NO_ECHO": sweep.no_echo.astype("uint8"),
        }
    )
    for key, values in (("GEOMETRY", sweep.good), ("GAP", sweep.gap_after)):
        arrays[prefix + key] = np.broadcast_to(values[:, None], shape).astype("uint8")
    for key, values in sweep.available.items():
        arrays[prefix + key + "_AVAILABLE"] = values.astype("uint8")
    if np.prod(shape) < 32:
        raise ValueError("physical speckle digest requires at least 32 native gates")
    order = np.argsort(sweep.azimuth, kind="stable")
    canonical = {
        key: value if key == "range" else value[order] for key, value in sweep.arrays().items()
    }
    raw_digest = array_digest(canonical)
    raw_bytes = np.frombuffer(bytes.fromhex(raw_digest), dtype="uint8")
    rank = np.argsort(order)
    code = (rank[:, None] * shape[1] + np.arange(shape[1])[None, :]) % 32
    arrays[prefix + "RAW_SHA256_BYTE"] = raw_bytes[code]
    return arrays, dict(
        result.summary,
        method="raw-physical-noise-speckle-20261002-v1",
        candidate_gates=int(candidate.sum()),
        raw_sha256=raw_digest,
        support_is_weather_absence=False,
        recursive_growth=False,
        target_evidence_required=True,
        quiet_support_threshold_db=0.0,
    )


def validate_serialized(group, parameters):
    """Replay complete RAW physical evidence in canonical order before writing."""
    from .residual_profile import ResidualConfig

    cfg = ResidualConfig.model_validate(parameters)
    if cfg.speckle_physical_support is None:
        raise ValueError("physical speckle parameters required")
    prefix = "V6_PHYSICAL_SPECKLE_INPUT_"
    az = np.asarray(group["azimuth"][:])
    order = np.argsort(az, kind="stable")
    shape = group["DBZH_RAW"].shape

    def matrix(key):
        if key not in group or group[key].shape != shape:
            raise ValueError("physical speckle input missing: " + key)
        return np.asarray(group[key][:])[order]

    fields, available = {}, {}
    for key in group:
        if key.startswith(prefix) and key.endswith("_AVAILABLE"):
            moment = key[len(prefix) : -len("_AVAILABLE")]
            fields[moment] = matrix(moment + "_RAW")
            available[moment] = checked_mask(matrix(key), shape, key)
    if not {"DBZH", "SNR"} <= fields.keys():
        raise ValueError("physical speckle original moments required")
    if not np.array_equal(available["DBZH"], matrix("VALID_MASK") == 1):
        raise ValueError("physical speckle reflectivity observation mismatch")
    rows = {}
    for key in ("GEOMETRY", "GAP"):
        value = checked_mask(matrix(prefix + key), shape, key)
        if not np.array_equal(value, np.broadcast_to(value[:, :1], shape)):
            raise ValueError("physical speckle ray geometry differs by range")
        rows[key] = value[:, 0]
    native = SimpleNamespace(
        name="serialized",
        shape=shape,
        fields=fields,
        field_available=available,
        azimuth=az[order],
        ranges=np.asarray(group["range"][:]),
        elevation=np.asarray(group["elevation"][:])[order],
        geometry_good=rows["GEOMETRY"],
        gap_after=rows["GAP"],
        no_echo=matrix(prefix + "NO_ECHO"),
    )
    arrays, _ = physical_candidates(
        native,
        cfg,
        baseline_eligible=matrix("V6_BASELINE_ELIGIBLE_MASK"),
        protected=matrix(prefix + "PROTECTED_MASK"),
        pol_bad=matrix(prefix + "POLAR_MASK"),
        low_snr=matrix(prefix + "LOW_SNR_MASK"),
    )
    for key, expected in arrays.items():
        if (
            key not in group
            or group[key].dtype != expected.dtype
            or not np.array_equal(matrix(key), expected, equal_nan=True)
        ):
            raise ValueError("physical speckle RAW replay differs: " + key)
