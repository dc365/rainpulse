"""Three-stage opt-in evidence with conservative, explicit action qualification."""
from enum import IntFlag
import hashlib
import json
import numpy as np
from ..arrays import mask, moment, native_geometry, numeric
from .geometry import ResourceLimit, numeric_plateaus
from .topology import support_topology
from .segments import segmented_references
from .bundles import bundle_candidates, fragment_identities


class Reason(IntFlag):
    RAW_TOPOLOGY = 1
    MEASURED_BUNDLE = 2
    EXISTING_SOURCE = 4
    SEGMENTED_COHERENT_SOURCE = 8
    WEAK_OR_MISSING_POLAR = 16
    WEATHER_OR_CONFLICT = 32
    NUMERIC_PLATEAU = 64
    RESOURCE_ABSTAINED = 128
    SOURCE_UNAVAILABLE_OR_MISMATCH = 256
    IDENTITY_ONLY_LINK = 512
    SEGMENTED_ACTION_DISABLED = 1024


DTYPES = {
    **{k: "uint8" for k in (
        "RV2_TOPOLOGY_MASK", "RV2_UNKNOWN_FLANK_MASK", "RV2_BUNDLE_MASK",
        "RV2_BUNDLE_RAYS", "RV2_SEGMENT_MATCH_MASK", "RV2_WEAK_MATCH_MASK",
        "RV2_FIT_AVAILABLE_MASK", "RV2_AMBIGUOUS_STATE_MASK", "RV2_STATE_FAMILY",
        "RV2_RANGE_TERM_MEASURED_MASK", "RV2_CANDIDATE_MASK", "RV2_WEAK_CANDIDATE_MASK",
        "RV2_LEGACY_MATCH_MASK", "RV2_QUALIFIED_MASK", "RV2_ACTION_PROPOSAL_MASK",
        "RV2_BARRED_MASK", "RV2_PLATEAU_MASK", "RV2_LINKED_SEGMENT_MASK",
        "RV2_MODE_CODE", "RV2_STEP_CODE", "RV2_SEGMENT_ACTION_ENABLED",
    )},
    **{k: "uint16" for k in ("RV2_SCALE_BITS", "RV2_BUNDLE_SCALE_BITS", "RV2_REASON")},
    **{k: "uint32" for k in ("RV2_SEGMENT_FOLD_ID", "RV2_MODEL_ID", "RV2_OBJECT_ID")},
    **{k: "float32" for k in (
        "RV2_SUPPORT_FRACTION", "RV2_TOPOLOGY_WIDTH_DEG", "RV2_BUNDLE_CONTRAST_DB",
        "RV2_SEGMENT_RESIDUAL_DB", "RV2_REFERENCE_MIN_M", "RV2_REFERENCE_MAX_M",
    )},
}


def empty_arrays(shape, cfg):
    result = {k: np.full(shape, np.nan if dt == "float32" else 0, dt) for k, dt in DTYPES.items()}
    result["RV2_MODE_CODE"][:] = cfg.mode == "experiment_quarantine"
    result["RV2_STEP_CODE"][:] = cfg.step
    result["RV2_SEGMENT_ACTION_ENABLED"][:] = cfg.allow_segmented_quarantine
    return result


def evaluate(native, cfg, legacy_source, legacy_residual, *, weather=None, conflicts=None, records_out=None):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    observed = observed & good[:, None]
    source = mask(legacy_source, native.shape, "legacy source")
    delta = numeric(legacy_residual, native.shape, "legacy residual")
    blocked = mask(weather, native.shape, "weather") | mask(conflicts, native.shape, "conflicts")
    source = source & observed & np.isfinite(delta) & (abs(delta) <= 2.5)
    base = {"version": cfg.version, "step": cfg.step, "mode": cfg.mode,
            "operational_eligible": False, "confirmed_gates": 0, "filled_gates": 0,
            "weak_actions": 0, "scores_are_probabilities": False,
            "config_sha256": hashlib.sha256(json.dumps(cfg.model_dump(mode="json"), sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()}
    local_records = [] if records_out is not None else None
    try:
        if np.prod(native.shape) > cfg.maximum_gates:
            raise ResourceLimit("native gate budget")
        plateau = numeric_plateaus(z, observed, r)
        barred = blocked | plateau | ~good[:, None]
        out = empty_arrays(native.shape, cfg)
        out.update(support_topology(native, cfg, barred))
        if cfg.step >= 3:
            out.update(bundle_candidates(native, cfg, barred))
        candidate = ((out["RV2_TOPOLOGY_MASK"] == 1) | (out["RV2_BUNDLE_MASK"] == 1)) & observed & ~barred
        source_report = {"status": "disabled", "reference_folds": 0, "reference_models": 0}
        if cfg.step >= 2:
            # Target plateau veto is already in candidate. Training plateau
            # membership is recomputed within each reference block, so the
            # extension's target/guard changes cannot grow into its references.
            fields, source_report = segmented_references(
                native, cfg, candidate, blocked | ~good[:, None], records_out=local_records
            )
            out.update(fields)
        link_report = {"identity_count": 0, "identity_links": 0, "filled_gates": 0}
        if cfg.step >= 3:
            fields, link_report = fragment_identities(native, cfg, candidate, barred)
            out.update(fields)
        snr, snr_ok = moment(native, "SNR")
        polar_ok = np.logical_and.reduce([moment(native, k)[1] for k in ("RHOHV", "ZDR", "PHIDP")])
        weak = candidate & (~snr_ok | (snr < cfg.minimum_coherent_snr_db) | ~polar_ok |
                            (out["RV2_WEAK_MATCH_MASK"] == 1))
        legacy_match = candidate & source & ~barred
        segment_match = (out["RV2_SEGMENT_MATCH_MASK"] == 1) & candidate & ~barred
        qualified = legacy_match | segment_match
        proposal = legacy_match | (segment_match & cfg.allow_segmented_quarantine)
        if cfg.mode != "experiment_quarantine":
            proposal[:] = False
        reason = out["RV2_REASON"]
        for test, bit in (
            (out["RV2_TOPOLOGY_MASK"] == 1, Reason.RAW_TOPOLOGY),
            (out["RV2_BUNDLE_MASK"] == 1, Reason.MEASURED_BUNDLE),
            (legacy_match, Reason.EXISTING_SOURCE),
            (segment_match, Reason.SEGMENTED_COHERENT_SOURCE),
            (weak | (out["RV2_WEAK_MATCH_MASK"] == 1), Reason.WEAK_OR_MISSING_POLAR),
            (blocked, Reason.WEATHER_OR_CONFLICT), (plateau, Reason.NUMERIC_PLATEAU),
            (candidate & ~qualified, Reason.SOURCE_UNAVAILABLE_OR_MISMATCH),
            (out["RV2_LINKED_SEGMENT_MASK"] == 1, Reason.IDENTITY_ONLY_LINK),
            (segment_match & ~legacy_match & (not cfg.allow_segmented_quarantine), Reason.SEGMENTED_ACTION_DISABLED),
        ):
            reason[test & observed] |= int(bit)
        out.update({
            "RV2_CANDIDATE_MASK": candidate.astype("uint8"),
            "RV2_WEAK_CANDIDATE_MASK": weak.astype("uint8"),
            "RV2_LEGACY_MATCH_MASK": legacy_match.astype("uint8"),
            "RV2_QUALIFIED_MASK": qualified.astype("uint8"),
            "RV2_ACTION_PROPOSAL_MASK": proposal.astype("uint8"),
            "RV2_BARRED_MASK": (barred & observed).astype("uint8"),
            "RV2_PLATEAU_MASK": (plateau & observed).astype("uint8"),
        })
        if records_out is not None:
            records_out.extend(local_records)
        return out, {**base, "status": "candidate_evaluated",
                     "candidate_gates": int(candidate.sum()), "topology_gates": int(out["RV2_TOPOLOGY_MASK"].sum()),
                     "bundle_gates": int(out["RV2_BUNDLE_MASK"].sum()), "weak_candidate_gates": int(weak.sum()),
                     "legacy_source_supported_gates": int(legacy_match.sum()),
                     "segmented_source_supported_gates": int(segment_match.sum()),
                     "qualified_gates": int(qualified.sum()), "action_proposal_gates": int(proposal.sum()),
                     "source": source_report, "identity": link_report}
    except ResourceLimit as exc:
        out = empty_arrays(native.shape, cfg)
        out["RV2_REASON"][observed] = int(Reason.RESOURCE_ABSTAINED)
        return out, {**base, "status": "resource_limit_abstained", "reason": str(exc),
                     "candidate_gates": 0, "qualified_gates": 0, "action_proposal_gates": 0}
