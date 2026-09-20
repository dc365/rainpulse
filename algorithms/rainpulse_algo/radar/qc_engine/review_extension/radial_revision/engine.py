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
    FRAGMENT_LINE = 2048
    COHERENT_LINE_SOURCE = 4096
    DIRECT_LINE_MORPHOLOGY = 8192
    ISOLATED_LINE_MORPHOLOGY = 16384
    GROUP_POLAR_MORPHOLOGY = 32768


DTYPES = {
    **{k: "uint8" for k in (
        "RV2_TOPOLOGY_MASK", "RV2_UNKNOWN_FLANK_MASK", "RV2_BUNDLE_MASK",
        "RV2_BUNDLE_RAYS", "RV2_SEGMENT_MATCH_MASK", "RV2_WEAK_MATCH_MASK",
        "RV2_FIT_AVAILABLE_MASK", "RV2_AMBIGUOUS_STATE_MASK", "RV2_STATE_FAMILY",
        "RV2_RANGE_TERM_MEASURED_MASK", "RV2_CANDIDATE_MASK", "RV2_WEAK_CANDIDATE_MASK",
        "RV2_LEGACY_MATCH_MASK", "RV2_QUALIFIED_MASK", "RV2_ACTION_PROPOSAL_MASK",
        "RV2_BARRED_MASK", "RV2_PLATEAU_MASK", "RV2_LINKED_SEGMENT_MASK",
        "RV2_MODE_CODE", "RV2_STEP_CODE", "RV2_SEGMENT_ACTION_ENABLED",
        "RV2_RESIDUAL_SPAN_MASK",
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
        line_report = {"status": "disabled"}
        line_source = np.zeros(native.shape, bool)
        line_morphology = np.zeros(native.shape, bool)
        line_isolated = np.zeros(native.shape, bool)
        group_polar = np.zeros(native.shape, bool)
        group_morph = np.zeros(native.shape, bool)
        residual = np.zeros(native.shape, bool)
        if cfg.fragment_line is not None:
            from .fragment_line import detect, coherent_source
            fields, line_report = detect(native, cfg.fragment_line, barred, source=source & ~barred)
            out.update(fields)
            candidate |= fields["RV2_LINE_MASK"] == 1
            if 'RV2_LINE_MORPH_MASK' in fields:
                line_morphology = fields['RV2_LINE_MORPH_MASK'] == 1
            if 'RV2_LINE_ISOLATED_MASK' in fields:
                line_isolated = fields['RV2_LINE_ISOLATED_MASK'] == 1
            if cfg.fragment_line.coherent_source_enabled:
                out.update(coherent_source(native, fields["RV2_LINE_MASK"] == 1, barred))
                line_source = out["RV2_LINE_SOURCE_MASK"] == 1
                line_report["coherent_source_gates"] = int(line_source.sum())
        if cfg.fragment_line is not None and cfg.fragment_line.sparse_isolated_enabled:
            from .fragment_line import grouped_strips
            out.update(grouped_strips(native, cfg.fragment_line, barred))
            candidate |= out['RV2_GROUP_MASK'] == 1
            group_polar = out['RV2_GROUP_POLAR_MASK'] == 1
            group_morph = out['RV2_GROUP_MORPH_MASK'] == 1
            line_report['group_morphology_gates'] = int(group_morph.sum())
            line_report['group_candidate_gates'] = int(out['RV2_GROUP_MASK'].sum())
            line_report['group_polar_gates'] = int(group_polar.sum())
        if cfg.fragment_line is not None and cfg.fragment_line.residual_objects_enabled:
            from .residual_objects import detect as detect_residual
            span = cfg.fragment_line
            fields, residual_report = detect_residual(
                native, barred, source | line_morphology | line_isolated | group_morph,
                beam_width=span.antenna_beam_width_deg,
                span_minimum_m=span.residual_span_minimum_m if span.residual_span_enabled else None,
                span_flank_rays=span.residual_span_flank_rays,
                span_flank_delta_db=span.residual_span_flank_delta_db,
                span_flank_fraction=span.residual_span_flank_fraction,
                span_minimum_dbz=span.residual_span_minimum_dbz,
                span_weather_snr_db=span.residual_span_weather_snr_db,
                span_weather_fraction=span.residual_span_weather_fraction)
            out.update(fields)
            residual = (fields['RV2_RESIDUAL_LINK_MASK'] | fields['RV2_RESIDUAL_DIRECT_MASK'] |
                        fields.get('RV2_RESIDUAL_SPAN_MASK', 0)) == 1
            candidate |= residual
            line_report['residual_objects'] = residual_report
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
        qualified = legacy_match | segment_match | line_source | line_morphology | line_isolated | group_polar | group_morph | residual
        proposal = legacy_match | (segment_match & cfg.allow_segmented_quarantine) | line_source | line_morphology | line_isolated | group_polar | group_morph | residual
        if cfg.mode != "experiment_quarantine":
            proposal[:] = False
        reason = out["RV2_REASON"]
        reason[group_polar | group_morph] |= int(Reason.GROUP_POLAR_MORPHOLOGY)
        if "RV2_LINE_MASK" in out:
            reason[out["RV2_LINE_MASK"] == 1] |= int(Reason.FRAGMENT_LINE)
            reason[line_source] |= int(Reason.COHERENT_LINE_SOURCE)
            reason[line_morphology] |= int(Reason.DIRECT_LINE_MORPHOLOGY)
            reason[line_isolated] |= int(Reason.ISOLATED_LINE_MORPHOLOGY)
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
                     "weak_actions": int((proposal & weak).sum()),
                     "source": source_report, "identity": link_report, "fragment_line": line_report}
    except ResourceLimit as exc:
        out = empty_arrays(native.shape, cfg)
        out["RV2_REASON"][observed] = int(Reason.RESOURCE_ABSTAINED)
        return out, {**base, "status": "resource_limit_abstained", "reason": str(exc),
                     "candidate_gates": 0, "qualified_gates": 0, "action_proposal_gates": 0}
