"""Explicit proposals and monotone experimental application; never restores old rejects."""

import hashlib
from enum import IntFlag
from pathlib import Path

import numpy as np

from .io import digest


class Reason(IntFlag):
    NO_SCORE = 1
    POOR_FEATURE_COVERAGE = 2
    BELOW_POLICY = 4
    WEATHER_CONFLICT = 8
    CONFIRM_PROPOSED = 16
    QUARANTINE_PROPOSED = 32
    AUDIT_ONLY = 64
    BASELINE_EXCLUDED = 128
    NO_ECHO_PROTECTED = 256
    STATE_UNDECIDED = 512


def policy_identity(cfg):
    return digest(
        {
            "policy": cfg.policy.model_dump(mode="json", exclude={"mode"}),
            "state": cfg.state.model_dump(mode="json"),
            "policy_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "state_code_sha256": hashlib.sha256(
                Path(__file__).with_name("states.py").read_bytes()
            ).hexdigest(),
        }
    )


def check_receipt(receipt, model, model_sha, cfg):
    """Local human review attestation, NOT a cryptographic signature or auto promotion."""
    expected = {
        "schema_version": "rainpulse.measurement-review.v1",
        "model_sha256": model_sha,
        "policy_sha256": policy_identity(cfg),
        "feature_identity": model["feature_identity"],
        "data_kind": "real",
        "decision": "approved_for_offline_experiment",
    }
    if not receipt or any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError("matching human-reviewed real-data receipt required")
    if (
        model["data_kind"] != "real"
        or not receipt.get("reviewer")
        or not receipt.get("reviewed_at_utc")
    ):
        raise ValueError("synthetic weights or unsigned review cannot alter experimental QC")
    if not receipt.get("validation_report_sha256") or not receipt.get("validation_dataset_sha256"):
        raise ValueError("review must bind independent validation evidence")


def decide(
    native,
    baseline,
    probabilities,
    states,
    missing_fraction,
    cfg,
    *,
    weather_support=None,
    approved=False,
):
    shape = native.shape
    if (
        probabilities.shape != (*shape, 3)
        or states.shape != shape
        or missing_fraction.shape != shape
    ):
        raise ValueError("policy geometry mismatch")
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    p = cfg.policy
    if p.mode == "experimental" and not approved:
        raise ValueError("experimental application requires independently reviewed real model")
    if np.any(~np.isfinite(missing_fraction)) or np.any(
        (missing_fraction < 0) | (missing_fraction > 1)
    ):
        raise ValueError("invalid feature-missing fraction")
    if np.any((states < -1) | (states > 2)):
        raise ValueError("invalid classification state")
    finite = np.isfinite(probabilities).all(axis=-1)
    sufficient = finite & (missing_fraction <= p.maximum_missing_feature_fraction) & observed
    if np.any(probabilities[finite] < 0) or not np.allclose(
        probabilities[finite].sum(axis=-1), 1, atol=1e-5
    ):
        raise ValueError("invalid policy probabilities")
    noecho = observed & (native.fields["DBZH"] < p.no_echo_below_dbz)
    support = np.full(shape, np.nan) if weather_support is None else np.asarray(weather_support)
    if support.shape != shape:
        raise ValueError("weather support geometry mismatch")
    weather = np.isfinite(support) & (support >= p.strong_weather_support)
    relevant = sufficient & ~noecho
    rfi = probabilities[..., 1]
    mixed = probabilities[..., 2]
    confirm = relevant & (states == 1) & (rfi >= p.confirm_probability) & ~weather
    quarantine = (
        relevant
        & ~confirm
        & (
            ((states == 1) & (rfi >= p.quarantine_probability))
            | ((states == 2) & (mixed >= p.mixed_probability))
        )
    )
    # A weather conflict may be quarantined as a polluted measurement, not labeled no-rain.
    reason = np.zeros(shape, "uint16")
    for mask, bit in (
        (~finite & observed, Reason.NO_SCORE),
        (finite & ~sufficient & observed, Reason.POOR_FEATURE_COVERAGE),
        (relevant & ~confirm & ~quarantine, Reason.BELOW_POLICY),
        (weather & (confirm | quarantine), Reason.WEATHER_CONFLICT),
        (confirm, Reason.CONFIRM_PROPOSED),
        (quarantine, Reason.QUARANTINE_PROPOSED),
        (noecho, Reason.NO_ECHO_PROTECTED),
        (observed & (states < 0), Reason.STATE_UNDECIDED),
    ):
        reason[mask] |= int(bit)
    actual_confirm, actual_q = confirm.copy(), quarantine.copy()
    if p.mode == "audit" or not approved:
        actual_confirm[:] = False
        actual_q[:] = False
        reason[observed] |= int(Reason.AUDIT_ONLY)
    elif p.mode != "experimental":
        raise ValueError("unknown policy mode")
    old_reject = baseline["QC_ACTION"] == 2
    old_q = baseline["RFI_QUARANTINE_MASK"] == 1
    old_eligible = baseline["QPE_ELIGIBLE_MASK"] == 1
    reason[old_reject | old_q] |= int(Reason.BASELINE_EXCLUDED)
    reject = old_reject | actual_confirm
    withheld = (old_q | actual_q) & ~reject
    arrays = {k: v.copy() for k, v in baseline.items()}
    arrays["QC_ACTION"][withheld] = 1
    arrays["QC_ACTION"][reject] = 2
    arrays["RFI_QUARANTINE_MASK"] = withheld.astype("uint8")
    # Flags are bound to qc-flags-v2 by FrozenCase.
    arrays["QC_FLAGS"][actual_confirm] |= np.uint32(8 | 32768 | 16384)
    arrays["QC_FLAGS"][actual_q] |= np.uint32(16384)
    arrays["QUALITY_INDEX"][actual_confirm] = 0
    arrays["QUALITY_INDEX"][actual_q] = np.minimum(
        arrays["QUALITY_INDEX"][actual_q], p.quarantine_quality
    )
    if "RFI_RISK_STATE" in arrays:
        arrays["RFI_RISK_STATE"][withheld] = 2
        arrays["RFI_RISK_STATE"][actual_confirm] = 3
    changed = actual_confirm | actual_q
    if "LOW_QUALITY_MASK" in arrays:
        arrays["LOW_QUALITY_MASK"][changed] = 1
    for key in ("QI_METEO", "QI_INTERFERENCE"):
        if key in arrays:
            arrays[key][actual_confirm] = 0
            arrays[key][actual_q] = np.fmin(arrays[key][actual_q], p.quarantine_quality)
    allowed = observed & ~reject & ~withheld
    for name in (
        "REFLECTIVITY_TRUST_MASK",
        "QPE_ELIGIBLE_MASK",
        "RHOHV_TRUST_MASK",
        "ZDR_TRUST_MASK",
        "PHIDP_TRUST_MASK",
        "VR_TRUST_MASK",
        "SW_TRUST_MASK",
        "SNR_TRUST_MASK",
    ):
        if name in arrays:
            arrays[name] &= allowed.astype("uint8")
    # Preserve existing numerical values (including any registered corrections)
    # at kept gates. Classification may WITHHOLD but never recalculate a value.
    arrays["DBZH_USABLE"][arrays["QPE_ELIGIBLE_MASK"] == 0] = np.nan
    arrays.update(
        {
            "V8_PROPOSED_CONFIRM_MASK": confirm.astype("uint8"),
            "V8_PROPOSED_QUARANTINE_MASK": quarantine.astype("uint8"),
            "V8_CONFIRMED_ADDITION_MASK": (actual_confirm & ~old_reject).astype("uint8"),
            "V8_QUARANTINED_ADDITION_MASK": (actual_q & ~old_q & ~old_reject).astype("uint8"),
            "V8_POLICY_REASON": reason,
            "V8_STATE": states.astype("int8"),
        }
    )
    for i, name in enumerate(("WEATHER", "INTERFERENCE", "MIXED")):
        arrays["V8_" + name + "_PROBABILITY"] = np.where(
            observed, probabilities[..., i], np.nan
        ).astype("float32")
    if np.any((arrays["QPE_ELIGIBLE_MASK"] == 1) & ~old_eligible):
        raise AssertionError("V8 restored an unavailable baseline measurement")
    if np.any(arrays["V8_CONFIRMED_ADDITION_MASK"] & arrays["V8_QUARANTINED_ADDITION_MASK"]):
        raise AssertionError("contradictory added actions")
    return arrays
