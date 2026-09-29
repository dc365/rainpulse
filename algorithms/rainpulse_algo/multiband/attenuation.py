# ruff: noqa: E501, I001
"""X-only path correction. S observations never enter this module.

Z-Phi: same endpoint phase constraint, reflectivity-dependent path allocation.
The native kernel uses a normalized, trapezoidal integral of attenuated Z**beta;
its log form preserves the endpoint PIA constraint without a singular forward
recursion. This is NOT the phase-linear method or bitwise Py-ART equivalence.
See docs/SX_FUSION_PATH_20260929.md for equations, references and assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum, IntFlag
import re
from typing import Any

import numpy as np
from scipy.ndimage import median_filter

from .moment_support import binary_mask, moment_support

VERSION = "x-path-quality-20260929-v1"
PATH_INPUT_FIELDS = {
    "CLEAR_PATH_MASK", "PATH_ANCHOR_VALID_MASK", "PATH_ANCHOR_PIA_DB",
    "ATTENUATION_NEGLIGIBLE_MASK", "RADOME_VALID_MASK",
}
PATH_PROVENANCE_KEYS = (
    "radome_status", "radome_evidence_sha256", "clear_path_evidence_sha256",
    "path_anchor_evidence_sha256", "negligible_attenuation_evidence_sha256",
)


def copy_path_provenance(source: dict, target: dict) -> None:
    """Copy actual producer declarations; never synthesize proof or validity."""
    for key in PATH_PROVENANCE_KEYS:
        if key in source:
            target[key] = source[key]

_SHA = re.compile(r"^[a-f0-9]{64}$")


class PathState(IntEnum):
    UNKNOWN = 0
    NOT_REQUIRED = 1
    UPSTREAM_VERIFIED = 2
    PHIDP_LINEAR = 3
    ZPHI = 4
    VERIFIED_CLEAR_PATH = 5


class PathReason(IntFlag):
    INITIAL_LOSS_UNKNOWN = 1
    PHASE_UNAVAILABLE = 2
    NON_LIQUID_OR_CONTAMINATED = 4
    PHASE_SPAN_TOO_SMALL = 8
    PHASE_NOISY_OR_JUMP = 16
    CORRECTION_LIMIT = 32
    RADOME_UNVERIFIED = 64
    SHORT_SEGMENT = 128
    PATH_BROKEN = 256
    METHOD_NOT_CONFIGURED = 512
    INPUT_INVALID = 1024


@dataclass(frozen=True)
class ZPhiOptions:
    beta: float
    coefficient_id: str
    coefficient_source_sha256: str
    frequency_min_hz: float
    frequency_max_hz: float
    minimum_delta_phase_deg: float = 3.0
    minimum_segment_m: float = 1000.0
    minimum_rhohv: float = 0.90
    phase_period_deg: float = 360.0
    maximum_phase_step_deg: float = 30.0
    maximum_endpoint_noise_deg: float = 3.0

    def __post_init__(self):
        for name in ("beta", "frequency_min_hz", "frequency_max_hz",
                     "minimum_delta_phase_deg", "minimum_segment_m", "minimum_rhohv",
                     "phase_period_deg", "maximum_phase_step_deg", "maximum_endpoint_noise_deg"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                raise ValueError(f"finite Z-Phi {name} required")
        if not (0.3 <= self.beta <= 1.5 and 8e9 <= self.frequency_min_hz < self.frequency_max_hz <= 12e9):
            raise ValueError("explicit bounded X-frequency Z-Phi coefficients required")
        if not (0 < self.minimum_delta_phase_deg <= 90 and 100 <= self.minimum_segment_m <= 20000
                and 0.5 <= self.minimum_rhohv <= 1 and self.phase_period_deg in (180, 360)
                and 0 < self.maximum_phase_step_deg < self.phase_period_deg / 2
                and 0 < self.maximum_endpoint_noise_deg <= 20):
            raise ValueError("invalid Z-Phi support/phase limits")
        if not isinstance(self.coefficient_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", self.coefficient_id):
            raise ValueError("Z-Phi coefficient identity required")
        if not isinstance(self.coefficient_source_sha256, str) or not _SHA.fullmatch(self.coefficient_source_sha256):
            raise ValueError("Z-Phi coefficient source SHA256 required")


@dataclass
class PathResult:
    corrected_dbzh: np.ndarray
    pia_db: np.ndarray
    specific_attenuation_db_km: np.ndarray
    kdp_deg_km: np.ndarray
    valid: np.ndarray
    state: np.ndarray
    reason: np.ndarray
    limited: np.ndarray
    radome_valid: np.ndarray

    def fields(self) -> dict[str, np.ndarray]:
        return {
            "DBZH_ATTENUATION_CORRECTED": self.corrected_dbzh,
            "AH_DB_PER_KM": self.specific_attenuation_db_km,
            "PATH_VALID_MASK": self.valid.astype("uint8"),
            "PATH_STATE": self.state,
            "PATH_REASON": self.reason,
            "RADOME_QUALIFIED_MASK": self.radome_valid.astype("uint8"),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "implementation": VERSION,
            "state_counts": {s.name: int(np.count_nonzero(self.state == s)) for s in PathState},
            "reason_counts": {s.name: int(np.count_nonzero(self.reason & int(s))) for s in PathReason},
            "path_valid_gates": int(self.valid.sum()),
            "radome_qualified_gates": int(self.radome_valid.sum()),
            "max_pia_db": float(np.max(self.pia_db[np.isfinite(self.pia_db)]))
            if np.isfinite(self.pia_db).any() else None,
            "s_radar_input_required": False,
            "specific_attenuation_semantics": "one_way_db_per_km",
            "pia_semantics": "two_way_db_including_known_initial_path_loss_not_radome",
            "quality_is_probability": False,
        }


def _proof(metadata: dict, key: str) -> bool:
    value = metadata.get(key)
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def zphi_segment(dbzh, phase, ranges_m, *, alpha, beta, initial_pia_db=0.0):
    """Solve a single trusted liquid segment; caller owns path continuity.

    J(r)=integral[r0,r] Z_a(s)**beta ds; u=J/J_end; k=ln(10)*beta/10.
    PIA(r)=PIA0-log(1-(1-exp(-k*alpha*DeltaPhi))*u)/k.
    A_H(r)=(1-exp(-k*alpha*DeltaPhi))*Z_a(r)**beta /
           (2*k*J_end*(1-(1-exp(-k*alpha*DeltaPhi))*u)).
    Range is converted to km; PhiDP is already two-way, so alpha*DeltaPhi
    does NOT get an extra factor of two. No smoothing or hole filling here.
    """
    z, p, r = (np.asarray(a, dtype="float64") for a in (dbzh, phase, ranges_m))
    if z.ndim != 1 or len(z) < 3 or z.shape != p.shape or z.shape != r.shape:
        raise ValueError("equal one-dimensional Z/Phi/range arrays required")
    if not all(np.isfinite(a).all() for a in (z, p, r)) or np.any(np.diff(r) <= 0):
        raise ValueError("finite continuous segment on increasing native ranges required")
    if np.any((z < -50) | (z > 100)) or np.any(np.diff(p) < -1e-8):
        raise ValueError("invalid Z or nonmonotone prepared propagation phase")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not np.isfinite(value) for value in (alpha, beta, initial_pia_db)) or not (
           0 < alpha <= 1 and 0.3 <= beta <= 1.5 and 0 <= initial_pia_db <= 30):
        raise ValueError("invalid explicit coefficients/initial PIA")
    total_pia = float(alpha * (p[-1] - p[0]))
    if not 0 < total_pia <= 100:
        raise ValueError("positive bounded endpoint phase constraint required")
    k = np.log(10.0) * beta / 10.0
    # Normalization cancels from the ratio and avoids large Z**beta values.
    y = np.exp(k * (z - np.max(z)))
    dr = np.diff(r) / 1000.0
    integral = np.r_[0.0, np.cumsum(0.5 * (y[1:] + y[:-1]) * dr)]
    total = integral[-1]
    if not np.isfinite(total) or total <= 0:
        raise ValueError("Z-Phi path integral has no usable support")
    fraction = integral / total
    loss = -np.expm1(-k * total_pia)
    denominator = 1 - loss * fraction
    if np.any(denominator <= 0):
        raise ValueError("ill-conditioned Z-Phi denominator")
    pia = initial_pia_db - np.log1p(-loss * fraction) / k
    ah = loss * y / (2 * k * total * denominator)
    return pia, ah


def _finite_pia(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and np.isfinite(value) and 0 <= value <= 30)


def _phase_support(sweep, profile, options):
    f = sweep.fields
    shape = f["DBZH"].shape
    ref = moment_support(f, "DBZH", shape)
    phase = moment_support(f, "PHIDP", shape)
    rho = moment_support(f, "RHOHV", shape)
    snr = moment_support(f, "SNR", shape)
    observed = binary_mask(f, "OBSERVED_MASK", shape)
    support = observed & ~binary_mask(f, "NO_ECHO_MASK", shape)
    support &= ref.valid & (ref.values >= -50) & (ref.values <= 100) & phase.valid
    support &= binary_mask(f, "PHASE_VALID_MASK", shape) & binary_mask(f, "LIQUID_MASK", shape)
    support &= rho.valid & (rho.values >= options.minimum_rhohv)
    # Reliable phase estimation requires SNR even when display QC permits absent SNR.
    support &= snr.valid & (snr.values >= profile.snr_min_db)
    support &= ~binary_mask(f, "CONFIRMED_NONMET_MASK", shape)
    support &= ~binary_mask(f, "ATTENUATION_UNRELIABLE_MASK", shape)
    return support, phase.values


def _zphi(sweep, profile, metadata, result):
    options = ZPhiOptions(**profile.zphi)
    frequency = metadata.get("frequency_hz")
    if isinstance(frequency, bool) or not isinstance(frequency, (int, float)) or not (
        options.frequency_min_hz <= frequency <= options.frequency_max_hz
    ):
        raise ValueError("observed frequency is outside the explicit Z-Phi coefficient range")
    f, ranges = sweep.fields, np.asarray(sweep.range_m, dtype=float)
    support, raw_phase = _phase_support(sweep, profile, options)
    shape = support.shape
    nonliquid = ~binary_mask(f, "LIQUID_MASK", shape) | binary_mask(f, "CONFIRMED_NONMET_MASK", shape)
    clear = (binary_mask(f, "CLEAR_PATH_MASK", shape)
             & binary_mask(f, "NO_ECHO_MASK", shape)
             & binary_mask(f, "OBSERVED_MASK", shape)
             & ~binary_mask(f, "ATTENUATION_UNRELIABLE_MASK", shape)
             & ~binary_mask(f, "CONFIRMED_NONMET_MASK", shape))
    if not _proof(metadata, "clear_path_evidence_sha256"):
        clear[:] = False
    anchors = binary_mask(f, "PATH_ANCHOR_VALID_MASK", shape)
    anchor_pia = f.get("PATH_ANCHOR_PIA_DB")
    if anchors.any() and (anchor_pia is None or not _proof(metadata, "path_anchor_evidence_sha256")):
        raise ValueError("explicit segment anchors require PIA values and evidence identity")
    if anchor_pia is not None and (np.shape(anchor_pia) != shape or np.any(
        anchors & (~np.isfinite(anchor_pia) | (anchor_pia < 0) | (anchor_pia > profile.max_pia_db))
    )):
        raise ValueError("invalid known loss at segment anchor")
    near = (metadata.get("phase_anchor_verified") is True
            and _finite_pia(metadata.get("pia_at_first_gate_db"))
            and ranges[0] <= profile.phase_anchor_max_range_m)
    dr = float(np.median(np.diff(ranges))) if len(ranges) > 1 else 0
    if dr <= 0:
        result.reason[:] |= int(PathReason.SHORT_SEGMENT)
        return
    window = max(3, int(round(profile.phase_window_m / dr)))
    window = min(501, window + (window % 2 == 0))
    for ray in range(shape[0]):
        known = near
        current_pia = float(metadata["pia_at_first_gate_db"]) if near else 0.0
        gate = 0
        # Jump between real support/anchor boundaries, not over every missing
        # gate in Python. This bounds work on long extinguished/unsupported rays.
        restart = np.flatnonzero(anchors[ray])
        unsupported = np.flatnonzero(~support[ray])
        not_clear = np.flatnonzero(~clear[ray])

        def next_boundary(indices, position, *, include=False):
            loc = np.searchsorted(indices, position, side="left" if include else "right")
            return int(indices[loc]) if loc < len(indices) else shape[1]

        while gate < shape[1]:
            if anchors[ray, gate]:
                known, current_pia = True, float(anchor_pia[ray, gate])
            if not known:
                stop = next_boundary(restart, gate)
                result.reason[ray, gate:stop] |= int(PathReason.INITIAL_LOSS_UNKNOWN | PathReason.PATH_BROKEN)
                gate = stop
                continue
            if clear[ray, gate]:
                stop = min(next_boundary(not_clear, gate), next_boundary(restart, gate))
                if current_pia <= profile.max_pia_db:
                    result.pia_db[ray, gate:stop] = current_pia
                    result.valid[ray, gate:stop] = True
                    result.state[ray, gate:stop] = PathState.VERIFIED_CLEAR_PATH
                gate = stop
                continue
            if not support[ray, gate]:
                result.reason[ray, gate] |= int(PathReason.PHASE_UNAVAILABLE | PathReason.PATH_BROKEN)
                if nonliquid[ray, gate]:
                    result.reason[ray, gate] |= int(PathReason.NON_LIQUID_OR_CONTAMINATED)
                known = False
                gate += 1
                continue
            start = gate
            stop = min(next_boundary(unsupported, gate), next_boundary(restart, gate))
            gate = stop
            if stop - start < 3 or ranges[stop - 1] - ranges[start] < options.minimum_segment_m:
                result.reason[ray, start:stop] |= int(PathReason.SHORT_SEGMENT)
                known = False
                continue
            p = np.unwrap(np.asarray(raw_phase[ray, start:stop], float), period=options.phase_period_deg)
            step = np.diff(p)
            if np.any((step < -profile.max_negative_phase_step_deg) | (step > options.maximum_phase_step_deg)):
                result.reason[ray, start:stop] |= int(PathReason.PHASE_NOISY_OR_JUMP)
                known = False
                continue
            w = min(window, len(p) if len(p) % 2 else len(p) - 1)
            smooth = median_filter(p, size=w, mode="nearest")
            endpoint = max(2, min(w, len(p) // 3))
            residual = p - smooth
            endpoint_noise = max(float(np.median(np.abs(residual[:endpoint]))),
                                 float(np.median(np.abs(residual[-endpoint:]))))
            if endpoint_noise > options.maximum_endpoint_noise_deg:
                result.reason[ray, start:stop] |= int(PathReason.PHASE_NOISY_OR_JUMP)
                known = False
                continue
            phase = np.maximum.accumulate(smooth - smooth[0])
            delta = phase[-1]
            if delta < options.minimum_delta_phase_deg:
                result.reason[ray, start:stop] |= int(PathReason.PHASE_SPAN_TOO_SMALL)
                known = False  # small phase alone is not proof of zero attenuation
                continue
            expected = current_pia + profile.alpha_db_per_degree * delta
            if expected > profile.max_pia_db:
                result.reason[ray, start:] |= int(PathReason.CORRECTION_LIMIT | PathReason.PATH_BROKEN)
                result.limited[ray, start:] = True
                known = False
                continue
            pia, ah = zphi_segment(f["DBZH"][ray, start:stop], phase, ranges[start:stop],
                                  alpha=profile.alpha_db_per_degree, beta=options.beta,
                                  initial_pia_db=current_pia)
            corrected = np.asarray(f["DBZH"][ray, start:stop], float) + pia
            if not np.isfinite(corrected).all() or np.any(corrected > 100):
                result.reason[ray, start:stop] |= int(PathReason.CORRECTION_LIMIT)
                known = False
                continue
            result.pia_db[ray, start:stop] = pia
            result.specific_attenuation_db_km[ray, start:stop] = ah
            result.kdp_deg_km[ray, start:stop] = 0.5 * np.gradient(phase, ranges[start:stop] / 1000)
            result.corrected_dbzh[ray, start:stop] = corrected
            result.valid[ray, start:stop] = True
            result.state[ray, start:stop] = PathState.ZPHI
            current_pia = float(pia[-1])


def correct_sweep(sweep, profile, metadata, *, linear_solver):
    """Shared production dispatch; legacy linear solver stays a regression baseline.

    Path validity is separate from wet-radome and calibration qualification.
    No inferred zero-loss gaps, no S reference, no velocity prerequisite.
    """
    f = sweep.fields
    shape = np.shape(f["DBZH"])
    observed = binary_mask(f, "OBSERVED_MASK", shape)
    noecho = binary_mask(f, "NO_ECHO_MASK", shape)
    def blank():
        return np.full(shape, np.nan, "float32")
    result = PathResult(blank(), blank(), blank(), blank(), np.zeros(shape, bool),
                        np.zeros(shape, "uint8"), np.zeros(shape, "uint16"),
                        np.zeros(shape, bool), np.zeros(shape, bool))
    upstream = metadata.get("attenuation_status", "unknown")
    if upstream not in ("raw", "corrected", "unknown"):
        raise ValueError("unknown upstream attenuation provenance")
    method = profile.attenuation
    if method in ("zphi", "phidp_linear") and upstream != "raw":
        raise ValueError("refusing double or unknown upstream attenuation correction")
    if method == "zphi":
        _zphi(sweep, profile, metadata, result)
    elif method == "phidp_linear":
        # Retain the old formula, but all explicit moment-validity aliases
        # constrain its path just as they constrain Z-Phi.
        support = binary_mask(f, "PHASE_VALID_MASK", shape)
        support &= moment_support(f, "PHIDP", shape).valid
        support &= moment_support(f, "DBZH", shape).valid
        for name in ("SNR", "RHOHV"):
            moment = moment_support(f, name, shape)
            if moment.source is not None:
                support &= moment.valid
        work = replace(sweep, fields={**f, "PHASE_VALID_MASK": support.astype("uint8")})
        result.pia_db, result.kdp_deg_km, result.limited = linear_solver(
            work, profile, anchor_verified=metadata.get("phase_anchor_verified") is True,
            initial_pia_db=metadata.get("pia_at_first_gate_db"))
        result.valid = np.isfinite(result.pia_db)
        result.corrected_dbzh = np.asarray(f["DBZH"], "float32") + result.pia_db
        result.state[result.valid] = PathState.PHIDP_LINEAR
        result.reason[~result.valid] |= int(PathReason.PHASE_UNAVAILABLE)
        result.reason[result.limited] |= int(PathReason.CORRECTION_LIMIT)
    elif method == "upstream_verified":
        if upstream != "corrected":
            raise ValueError("verified vendor correction requires explicit source provenance")
        result.valid = binary_mask(f, "ATTENUATION_VALID_MASK", shape)
        result.corrected_dbzh = np.asarray(f["DBZH"], "float32").copy()
        if "PIA_DB" in f:
            p = np.asarray(f["PIA_DB"], "float32")
            if p.shape != shape or np.any(np.isfinite(p) & (p < 0)):
                raise ValueError("invalid upstream PIA")
            result.pia_db = p.copy()
            result.limited = np.isfinite(p) & (p > profile.max_pia_db)
        result.state[result.valid] = PathState.UPSTREAM_VERIFIED
    elif method == "none":
        result.reason[observed] |= int(PathReason.METHOD_NOT_CONFIGURED)
        # Explicit, source-verified negligibility only. NaN PIA is retained:
        # "correction not required" does not mean measured attenuation is zero.
        if upstream == "raw" and _proof(metadata, "negligible_attenuation_evidence_sha256"):
            result.valid = binary_mask(f, "ATTENUATION_NEGLIGIBLE_MASK", shape) & observed
            result.state[result.valid] = PathState.NOT_REQUIRED
            result.reason[result.valid] = 0
            result.corrected_dbzh = np.where(result.valid, f["DBZH"], np.nan).astype("float32")
    else:
        raise ValueError("unsupported attenuation method")
    blocked = binary_mask(f, "ATTENUATION_UNRELIABLE_MASK", shape)
    ref = moment_support(f, "DBZH", shape)
    measured = observed & (noecho | (ref.valid & (ref.values >= -50) & (ref.values <= 100)))
    correction_out_of_range = (np.isfinite(result.corrected_dbzh)
                               & ((result.corrected_dbzh > 100) | (result.corrected_dbzh < -50)))
    result.limited |= correction_out_of_range
    result.reason[result.limited] |= int(PathReason.CORRECTION_LIMIT)
    result.valid &= measured & ~blocked & ~result.limited
    result.reason[blocked] |= int(PathReason.PATH_BROKEN)
    result.reason[~measured] |= int(PathReason.INPUT_INVALID)
    result.state[~result.valid] = PathState.UNKNOWN
    result.corrected_dbzh[~result.valid | noecho] = np.nan
    # Never infer a wet-radome correction from path correction success.
    if metadata.get("radome_status") in ("verified_negligible", "upstream_corrected") and _proof(metadata, "radome_evidence_sha256"):
        result.radome_valid[:] = True
        if "RADOME_VALID_MASK" in f:
            result.radome_valid &= binary_mask(f, "RADOME_VALID_MASK", shape)
    result.reason[observed & ~result.radome_valid] |= int(PathReason.RADOME_UNVERIFIED)
    return result
