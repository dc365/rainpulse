"""Pure per-cut evidence. Shared S core calls; one X action owner downstream."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntFlag

import numpy as np

from rainpulse_algo.performance import timed
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.background import (
    empty as empty_background,
)
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.engine import evaluate_volume
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.features import (
    extract as extract_features,
)
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
from rainpulse_algo.radar.qc_engine.volume_review.geometry import wrap
from rainpulse_algo.radar.qc_engine.volume_review.objects import extract_objects
from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.core import (
    evaluate as receiver_evaluate,
)

from .fragments import associate
from .geometry import adapt, mask


class Reason(IntFlag):
    RECEIVER_FULL = 1
    RECEIVER_PARTIAL = 2
    RADIAL_POLAR = 4
    CLUTTER = 8
    ISOLATED = 16
    WEATHER_PROTECTED = 32
    GEOMETRY_UNAVAILABLE = 64
    SUPPORT_INSUFFICIENT = 128
    ACTION_BUDGET = 256
    PHASE_PATH_BLOCKED = 512
    CALIBRATION_UNKNOWN = 1024
    ATTENUATION_UNKNOWN = 2048
    BACKGROUND = 4096
    RADIAL_FRAGMENT = 8192
    NOISE_FLOOR = 16384
    RADIAL_SOURCE = 32768
    CONTEXT_CONFLICT = 65536
    MORPHOLOGY = 131072
    NEAR_FLOOR_SOURCE = 262144


@dataclass(frozen=True)
class Evidence:
    arrays: dict
    record: dict


def empty(cut, status, reason=""):
    shape = cut.fields["DBZH"].shape
    masks = (
        "RECEIVER",
        "PARTIAL",
        "RADIAL_OBJECT",
        "RADIAL_POLAR",
        "RADIAL_FRAGMENT",
        "RADIAL_SOURCE",
        "MORPHOLOGY",
        "MORPHOLOGY_COUNTEREXAMPLE",
        "CLUTTER",
        "ISOLATED",
        "HARD_WEATHER",
        "LOCAL_WEATHER",
        "NOISE_FLOOR",
        "PROPOSED",
        "QUARANTINE",
        "AVAILABLE",
    )
    a = {"XQC_" + k + "_MASK": np.zeros(shape, "uint8") for k in masks}
    a.update(
        XQC_REASON=np.zeros(shape, "uint32"),
        XQC_CLASS=np.zeros(shape, "uint8"),
        XQC_RECEIVER_MODEL_ID=np.zeros(shape, "uint32"),
        XQC_MORPHOLOGY_OBJECT_ID=np.zeros(shape, "uint32"),
        XQC_RECEIVER_RESIDUAL_DB=np.full(shape, np.nan, "float32"),
    )
    if status != "EVALUATED":
        a["XQC_REASON"][cut.fields["OBSERVED_MASK"] == 1] |= int(Reason.SUPPORT_INSUFFICIENT)
    return Evidence(
        a,
        {
            "status": status,
            "detail": reason,
            "candidate_gates": 0,
            "cross_cut_context": "NOT_BOUND_SINGLE_CUT",
        },
    )


def background(s, metadata, cfg):
    from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.background import compare, load
    from rainpulse_algo.radar.qc_engine.volume_review.episode_background.data import Sample

    sid = str(metadata.get("radar_id", "")).lower()
    binding = cfg.clutter.background.assets.get(sid)
    if binding is None:
        return empty_background(s.shape, cfg.clutter), {"status": "NO_ASSET_BOUND"}
    processing = metadata.get("radar_config_version")
    if not processing:
        return empty_background(s.shape, cfg.clutter), {"status": "PROCESSING_ID_UNAVAILABLE"}
    # Missing assets / hash mismatch are explicit errors, not guessed backgrounds.
    model = load(binding.path, binding.sha256, cfg.clutter.background.maximum_asset_bytes)
    stamp = datetime.fromtimestamp(float(np.max(s.ray_time_s)), UTC).isoformat()
    sample = Sample(
        radar_id=sid,
        scan_id=metadata["scan_id"],
        sweep_id=s.name,
        processing_id=processing,
        observed_at=stamp,
        source_sha256=metadata["asset_sha256"],
        azimuth=s.azimuth,
        elevation=s.elevation,
        ranges=s.ranges,
        fields=dict(s.fields),
        available=dict(s.available),
        geometry_good=s.good,
    )
    arrays, record = compare(sample, model, cfg.clutter)
    return arrays, {**record, "asset_sha256": binding.sha256}


def measured_flanks(s, cfg):
    z, za = s.moment("DBZH")
    sn, sa = s.moment("SNR")
    result = np.zeros(s.shape, bool)
    for row in np.flatnonzero(s.good):
        sides = []
        for direction in (-1, 1):
            supported = np.zeros(s.shape[1], bool)
            current = row
            for step in range(1, s.shape[0]):
                other = (row + direction * step) % s.shape[0]
                edge = current if direction == 1 else other
                if s.gap_after[edge] or not s.good[other]:
                    break
                if abs(float(wrap(s.azimuth[other] - s.azimuth[row]))) > cfg.radial_flank_deg:
                    break
                measured = za[row] & za[other] & sa[row] & sa[other]
                supported |= (
                    measured
                    & (z[row] - z[other] >= cfg.radial_flank_contrast_db)
                    & (sn[row] - sn[other] >= 6.0)
                )
                current = other
            sides.append(supported)
        if cfg.radial_flank_mode == "either":
            # X interference bleeds into directly adjacent rays, so a quiet
            # comparison can sit one ray further out on either side.
            result[row] = sides[0] | sides[1]
        else:
            result[row] = sides[0] & sides[1]
    return result


@timed("x.v2.evidence")
def evaluate_cut(cut, metadata, cfg, *, context=None):
    """Known resource limits abstain for this cut; unexpected errors remain errors."""
    try:
        return _evaluate(cut, metadata, cfg, context=context)
    except ResourceLimit as error:
        return empty(cut, "RESOURCE_OR_GEOMETRY_ABSTAINED", str(error))


def _evaluate(cut, metadata, cfg, *, context=None):
    doppler_ok = bool(
        cfg.doppler_verified
        and metadata.get("doppler_verification_id") == cfg.doppler_verification_id
        and metadata.get("doppler_waveform") == cfg.doppler_waveform
        and metadata.get("nyquist_velocity_mps") == cfg.nyquist_velocity_mps
    )
    view = adapt(cut, cfg.model_copy(update={"doppler_verified": doppler_ok}))
    s = view.sweep
    original = s.digest
    observed = s.observed
    hard = mask(cut.fields, "WEATHER_PROTECTED_MASK", cut.fields["DBZH"].shape)[view.order]
    hard |= mask(cut.fields, "MIXED_WEATHER_MASK", cut.fields["DBZH"].shape)[view.order]
    from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.features import (
        PreparedFeatures,
    )

    features = extract_features(s, cfg.clutter)
    prepared_features = PreparedFeatures.bind(s, cfg.clutter, features)
    local = features.arrays["CF_WEATHER_PROXY_MASK"] == 1
    result = empty(cut, "EVALUATED")
    a = result.arrays
    records = {
        "status": "EVALUATED",
        "geometry": view.report,
        "index_space": "sorted_rays; sorted_to_original_ray below",
        "sorted_to_original_ray": view.order.tolist(),
        "scores_are_probabilities": False,
        "module_records": {},
    }
    receiver = partial = radial = noisy = fragments = np.zeros(s.shape, bool)
    quarantine = np.zeros(s.shape, bool)
    why = np.zeros(s.shape, "uint32")
    why[~s.good, :] |= int(Reason.GEOMETRY_UNAVAILABLE)
    if cfg.receiver_enabled:
        ev = receiver_evaluate(s, cfg.receiver, independent_weather=hard, local_coherence=local)
        if cfg.receiver.source_family is not None:
            from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.family_validation import (  # noqa: E501
                check_family_evidence,
                validate_family_records,
            )

            check_family_evidence(ev.arrays, observed, cfg.receiver)
            validate_family_records(ev.models, s, cfg.receiver)
        receiver = ev.arrays["RDR_SOURCE_MASK"] == 1
        partial = (
            (ev.arrays["RDR_PARTIAL_MATCH_MASK"] == 1)
            & ~hard
            & ~local
            & (ev.arrays["RDR_TARGET_SIDE_CONFLICT_MASK"] == 0)
            & (ev.arrays["RDR_TARGET_POLAR_CONFLICT_MASK"] == 0)
        )
        a["XQC_RECEIVER_MODEL_ID"] = view.restore(ev.arrays["RDR_MODEL_ID"])
        a["XQC_RECEIVER_RESIDUAL_DB"] = view.restore(ev.arrays["RDR_RESIDUAL_DB"])
        records["module_records"]["receiver"] = {"summary": ev.summary, "models": ev.models}
        why[receiver] |= int(Reason.RECEIVER_FULL)
        why[partial] |= int(Reason.RECEIVER_PARTIAL)
        receiver_quarantine = receiver.copy()
        if (
            cfg.receiver.source_family is not None
            and cfg.receiver.source_family.full_policy == "cr_only"
        ):
            receiver_quarantine &= ev.arrays["RDR_FAMILY_REFERENCE_MASK"] == 0
        quarantine |= receiver_quarantine
    else:
        records["module_records"]["receiver"] = {"status": "DISABLED"}
    if cfg.radial_objects_enabled:
        objects, rec, cap = extract_objects(s, cfg.objects)
        radial = objects["VOR_RADIAL_GEOMETRY_MASK"] == 1
        z, az = s.moment("DBZH")
        sn, ass = s.moment("SNR")
        rho, ar = s.moment("RHOHV")
        zdr, ad = s.moment("ZDR")
        _, ap = s.moment("PHIDP")
        jitter = features.arrays["CF_PHI_JITTER_DEG"]
        zdr_abnormal = (
            ad & (abs(zdr) < cfg.clutter.maximum_abs_zdr_db) & ((zdr < -1.0) | (zdr > 4.0))
        )
        polar = ar & ass & (rho <= cfg.radial_maximum_rhohv) & (sn >= cfg.radial_minimum_snr_db)
        polar &= zdr_abnormal | (ap & np.isfinite(jitter) & (jitter >= cfg.radial_phase_jitter_deg))
        noisy = (
            radial
            & polar
            & az
            & (z < cfg.radial_maximum_dbzh)
            & ~hard
            & ~local
            & measured_flanks(s, cfg)
        )
        why[noisy] |= int(Reason.RADIAL_POLAR)
        quarantine |= noisy
        fragment_record = {"status": "DISABLED"}
        if cfg.fragment_maximum_distance_m > 0:
            # Identity never confers pollution: associated gates still need
            # their own local polarimetric badness and the shared caps.
            fragments, fragment_record = associate(
                s, noisy, cfg, hard=hard, local=local, jitter=jitter
            )
            why[fragments] |= int(Reason.RADIAL_FRAGMENT)
            quarantine |= fragments
        records["module_records"]["radial_objects"] = {
            "capability": cap,
            "objects": rec,
            "qualified_gates": int(noisy.sum()),
            "shape_alone_actions": 0,
            "fragments": fragment_record,
        }
    else:
        records["module_records"]["radial_objects"] = {"status": "DISABLED"}
    clutter = isolated = np.zeros(s.shape, bool)
    if cfg.clutter_enabled:
        cf_cfg = cfg.clutter.model_copy(
            update={
                "isolated_objects": cfg.clutter.isolated_objects if cfg.isolation_enabled else None
            }
        )
        bg = background(s, metadata, cfg)
        ev = evaluate_volume(
            [s],
            cf_cfg,
            backgrounds=[bg],
            protections=[(hard, local, np.zeros(s.shape, bool))],
            prepared_features=[prepared_features],
        )[0]
        cf = ev.arrays
        if cfg.isolation_enabled and "CF_ISO_CANDIDATE_MASK" in cf:
            from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.isolated_objects import (  # noqa: E501
                validate,
            )

            validate(cf, cf_cfg)
        clutter = cf["CF_NONMET_SUPPORTED_MASK"] == 1
        quarantine |= cf["CF_QUARANTINE_SUPPORTED_MASK"] == 1
        if cfg.isolation_enabled and "CF_ISO_CANDIDATE_MASK" in cf:
            isolated = cf["CF_ISO_CANDIDATE_MASK"] == 1
            quarantine |= cf["CF_ISO_QUARANTINE_CANDIDATE_MASK"] == 1
        why[clutter] |= int(Reason.CLUTTER)
        why[isolated] |= int(Reason.ISOLATED)
        why[cf["CF_BG_MATCH_MASK"] == 1] |= int(Reason.BACKGROUND)
        a["XQC_CLASS"] = view.restore(cf["CF_CLASS"])
        # Keep selected diagnostics, not every intermediate array resident.
        diagnostic_names = {
            "CF_Z_TEXTURE_DB",
            "CF_PHI_JITTER_DEG",
            "CF_DR_DB",
            "CF_BG_MATCH_MASK",
            "CF_BG_STABLE_MASK",
            "CF_ISO_OBJECT_ID",
            "CF_ISO_STATE",
        }
        diagnostic_names.update(k for k in cf if k.startswith("CF_ISO_") and "WEAK" in k)
        for name in sorted(diagnostic_names):
            if name in cf:
                a["XQC_" + name[3:]] = view.restore(cf[name])
        records["module_records"]["clutter"] = ev.summary
    else:
        records["module_records"]["clutter"] = {"status": "DISABLED"}
    # Detection-floor censor: at SNR below the receiver's own detection floor
    # the DBZH processor still emits noise-floor + 20log10(r) values, which
    # render as false distant echo. SNRH is the radar's own measurement, so
    # censoring is a calibration policy, bounded by a field-integrity cap.
    censor = np.zeros(s.shape, bool)
    if cfg.noise_censor_snr_db is not None:
        sn_c, sa_c = s.moment("SNR")
        coverage = float((observed & sa_c).sum()) / max(float(observed.sum()), 1.0)
        candidate = observed & sa_c & np.isfinite(sn_c) & (sn_c < cfg.noise_censor_snr_db) & ~hard
        fraction = float(candidate.sum()) / max(float(observed.sum()), 1.0)
        if (
            coverage < cfg.noise_censor_minimum_coverage
            or fraction > cfg.noise_censor_maximum_fraction
        ):
            records["module_records"]["noise_censor"] = {
                "status": "ABSTAINED_SNR_FIELD_INVALID",
                "threshold_db": cfg.noise_censor_snr_db,
                "snr_coverage": coverage,
                "censored_fraction": fraction,
                "censored_gates": 0,
            }
        else:
            censor = candidate
            why[censor] |= int(Reason.NOISE_FLOOR)
            bands = {}
            for lo, hi in ((0, 25), (25, 50), (50, 75), (75, 150)):
                within = censor & (s.ranges[None, :] >= lo * 1000) & (s.ranges[None, :] < hi * 1000)
                if within.any():
                    bands[f"{lo}-{hi}km"] = int(within.sum())
            records["module_records"]["noise_censor"] = {
                "status": "APPLIED",
                "threshold_db": cfg.noise_censor_snr_db,
                "snr_coverage": coverage,
                "censored_fraction": fraction,
                "censored_gates": int(censor.sum()),
                "by_range_km": bands,
            }
    else:
        records["module_records"]["noise_censor"] = {"status": "DISABLED"}
    from .radial_source import detect as detect_radial_source

    source_details = {}
    try:
        radial_source, source_record = detect_radial_source(
            s,
            cfg,
            protected=hard | (local if cfg.radial_source_local_policy == "protect" else False),
            details=source_details,
        )
    except ResourceLimit as exc:
        radial_source = np.zeros(s.shape, bool)
        source_details.clear()
        source_record = {
            "status": "RESOURCE_LIMIT_ABSTAINED",
            "reason": str(exc),
            "source_gates": 0,
        }
    source_record["local_proxy_policy"] = cfg.radial_source_local_policy
    source_record["local_proxy_conflict_gates"] = int((radial_source & local).sum())
    records["module_records"]["radial_source"] = source_record
    if source_record["status"] in {"RESOURCE_LIMIT_ABSTAINED", "PARTIAL_RESOURCE_LIMIT"}:
        records["status"] = "DEGRADED_SOURCE_RESOURCE_LIMIT"
        records["degraded_modules"] = ["radial_source"]
    a["XQC_SOURCE_KIND"] = view.restore(
        source_details.get("source_kind", np.zeros(s.shape, np.uint8))
    )
    why[radial_source] |= int(Reason.RADIAL_SOURCE)
    quarantine |= radial_source
    morphology = np.zeros(s.shape, bool)
    if cfg.morphology is not None:
        from .polar_morphology import detect as detect_morphology

        try:
            protection = hard | (
                local if cfg.morphology.local_weather_policy == "protect" else False
            )
            morph = detect_morphology(s, cfg.morphology, protected=protection)
            morphology = morph.mask
            if morph.counterexample_mask is not None:
                a["XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK"] = view.restore(
                    morph.counterexample_mask
                ).astype("uint8")
            a["XQC_MORPHOLOGY_OBJECT_ID"] = view.restore(morph.object_id)
            morph.record["local_proxy_policy"] = cfg.morphology.local_weather_policy
            morph.record["local_proxy_conflict_gates"] = int((morphology & local).sum())
            morph.record["action_semantics"] = "candidate_withheld_not_confirmed"
            records["module_records"]["morphology"] = morph.record
        except ResourceLimit as exc:
            records["module_records"]["morphology"] = {
                "status": "RESOURCE_LIMIT_ABSTAINED",
                "reason": str(exc),
                "qualified_gates": 0,
            }
            records["status"] = "DEGRADED_MORPHOLOGY_RESOURCE_LIMIT"
            records.setdefault("degraded_modules", []).append("morphology")
        why[morphology] |= int(Reason.MORPHOLOGY)
        # Shape evidence alone never asserts confirmed non-meteorological
        # contamination. The sole finalizer withholds candidates from display
        # and CR, preserving raw values, cause and review state (action 3).
    else:
        records["module_records"]["morphology"] = {"status": "DISABLED"}
    near_floor = np.zeros(s.shape, bool)
    if cfg.near_floor_source_candidates_enabled:
        if records['module_records']['morphology'].get('status') != 'EVALUATED':
            near_record = {'status':'UNAVAILABLE_COMPACT_PROTECTION', 'source_gates':0}
        else:
            from .source_fans import detect as detect_near_floor
            from .source_summary import SourceStatistics

            # Full original objects are removed from references as well as
            # targets. A failed morphology pass cannot provide their absence.
            compact = a['XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK'][view.order].astype(bool)
            protected = hard | local | compact
            stats = None
            previous_work = source_record.get('work', {})
            previous_trials = int(previous_work.get('model_trials', 0))
            previous_models = int(previous_work.get('model_records', 0))
            try:
                if (previous_trials >= cfg.source_maximum_trials
                        or previous_models >= cfg.source_maximum_models):
                    raise ResourceLimit('X source allowance exhausted before near-floor candidates')
                stats = SourceStatistics.build(s, cfg)
                # This new source pass cannot restart the cut's model allowance.
                # Existing completed evidence has precedence; full RAW remains
                # the reference input and no partial new mask is published.
                stats.maximum_trials -= previous_trials
                stats.maximum_models -= previous_models
                near_floor, near_record = detect_near_floor(
                    s, cfg, protected=protected, prepared=stats, near_floor_references=True,
                )
                near_floor &= observed & ~protected
                near_record = dict(
                    near_record, status='EVALUATED', diagnostic_only=False,
                    action_semantics='candidate_withheld_not_confirmed',
                    source_gates=int(near_floor.sum()), work=stats.receipt(),
                    previous_source_trials=previous_trials,
                    cumulative_source_trials=previous_trials + stats.trials,
                )
            except ResourceLimit as exc:
                near_floor[:] = False
                near_record = {
                    'status':'RESOURCE_LIMIT_ABSTAINED', 'reason':str(exc),
                    'source_gates':0, 'work':stats.receipt() if stats is not None else {},
                }
        if near_record['status'] != 'EVALUATED':
            records['status'] = 'DEGRADED_NEAR_FLOOR_SOURCE_UNAVAILABLE'
            records.setdefault('degraded_modules', []).append('near_floor_source')
        records['module_records']['near_floor_source'] = near_record
        a['XQC_NEAR_FLOOR_SOURCE_MASK'] = view.restore(near_floor.astype('uint8'))
        why[near_floor] |= int(Reason.NEAR_FLOOR_SOURCE)
    from .limited_context import evaluate_context

    context_weather, context_record, context_measured, context_donor, context_ray, context_gate = (
        evaluate_context(s, metadata, cfg, context)
    )
    records["module_records"]["context"] = context_record
    mixed = context_weather & quarantine
    why[mixed] |= int(Reason.CONTEXT_CONFLICT)
    if cfg.context.mode == "mixed_review":
        quarantine &= ~mixed  # still proposed/withheld; never restore CR!
    a["XQC_CONTEXT_MEASURED_MASK"] = view.restore(context_measured.astype("uint8"))
    a["XQC_CONTEXT_WEATHER_MASK"] = view.restore(context_weather.astype("uint8"))
    a["XQC_CONTEXT_DONOR"] = view.restore(context_donor)
    a["XQC_CONTEXT_RAY"] = view.restore(context_ray)
    a["XQC_CONTEXT_GATE"] = view.restore(context_gate)
    a["XQC_SOURCE_MIXED_MASK"] = view.restore(mixed.astype("uint8"))
    def finalize(near_mask, override=None):
        # Re-finalize a rejected incremental evidence module without rerunning
        # fits, resetting work limits, or losing completed legacy masks/models.
        final_records = dict(records)
        final_records['module_records'] = {
            key: dict(value) for key, value in records['module_records'].items()
        }
        final_records['degraded_modules'] = list(records.get('degraded_modules', []))
        if not final_records['degraded_modules']:
            del final_records['degraded_modules']
        final_arrays = dict(a)
        final_why = why.copy()
        final_quarantine = quarantine.copy()
        if cfg.near_floor_source_candidates_enabled:
            final_arrays['XQC_NEAR_FLOOR_SOURCE_MASK'] = view.restore(near_mask.astype('uint8'))
            final_why &= np.uint32(~int(Reason.NEAR_FLOOR_SOURCE) & 0xffffffff)
            final_why[near_mask] |= int(Reason.NEAR_FLOOR_SOURCE)
        if override is not None:
            final_records['module_records']['near_floor_source'] = override
            final_records['status'] = 'DEGRADED_NEAR_FLOOR_SOURCE_UNAVAILABLE'
            final_records.setdefault('degraded_modules', []).append('near_floor_source')
        baseline_proposed = (
            (receiver | partial | noisy | fragments | radial_source | clutter | isolated)
            & observed
            & ~hard
        )
        proposed = baseline_proposed | ((morphology | near_mask) & observed & ~hard)
        final_quarantine &= proposed
        final_why[hard] |= int(Reason.WEATHER_PROTECTED)
        denominator = int(observed.sum())
        budget = cfg.maximum_new_exclusion_fraction * max(denominator, 1)
        if int(baseline_proposed.sum()) > budget:
            # Preserve the pre-existing full-enhancement abstention contract.
            final_why[proposed] |= int(Reason.ACTION_BUDGET)
            proposed = np.zeros(s.shape, bool)
            final_quarantine = np.zeros(s.shape, bool)
            final_records["status"] = "ACTION_BUDGET_ABSTAINED"
        elif int(proposed.sum()) > budget:
            # An incremental morphology entry must not undo previously accepted
            # baseline QC. Keep the same cap and abstain the new proposal as a
            # whole; do not select arbitrary pixels to fill the remaining budget.
            final_why[proposed & ~baseline_proposed] |= int(Reason.ACTION_BUDGET)
            proposed = baseline_proposed
            final_quarantine &= proposed
            final_records["module_records"]["morphology"]["action_status"] = (
                "ACTION_BUDGET_ABSTAINED"
            )
            if cfg.near_floor_source_candidates_enabled:
                final_records['module_records']['near_floor_source']['action_status'] = (
                    'ACTION_BUDGET_ABSTAINED'
                )
            final_records["status"] = (
                "DEGRADED_MORPHOLOGY_ACTION_BUDGET"
                if baseline_proposed.any() else "ACTION_BUDGET_ABSTAINED"
            )
        if cfg.morphology is not None:
            final_records["module_records"]["morphology"]["withheld_candidate_gates"] = int(
                (morphology & (proposed | ((final_why & int(Reason.ACTION_BUDGET)) != 0))
                 & ~final_quarantine).sum()
            )
        # The censor carries its own integrity cap and never triggers, nor is
        # subject to, the heuristic action budget.
        proposed |= censor
        final_quarantine |= censor
        for key, value in (
            ("RECEIVER", receiver),
            ("PARTIAL", partial),
            ("RADIAL_OBJECT", radial),
            ("RADIAL_POLAR", noisy),
            ("RADIAL_FRAGMENT", fragments),
            ("RADIAL_SOURCE", radial_source),
            ("MORPHOLOGY", morphology),
            ("CLUTTER", clutter),
            ("ISOLATED", isolated),
            ("HARD_WEATHER", hard),
            ("LOCAL_WEATHER", local),
            ("NOISE_FLOOR", censor),
            ("PROPOSED", proposed),
            ("QUARANTINE", final_quarantine),
            ("AVAILABLE", observed),
        ):
            final_arrays["XQC_" + key + "_MASK"] = view.restore(value.astype("uint8"))
        final_arrays["XQC_REASON"] = view.restore(final_why)
        final_records.update(
            candidate_gates=int(final_arrays["XQC_PROPOSED_MASK"].sum()),
            quarantine_supported_gates=int(final_arrays["XQC_QUARANTINE_MASK"].sum()),
            raw_digest=original,
        )
        if s.digest != original:
            raise RuntimeError("shared X core mutated original measurements")
        serialized = json.dumps(
            final_records, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if len(serialized) > cfg.maximum_evidence_bytes:
            from .evidence_tables import compact, pack_details

            final_records = compact(final_records)
            serialized = json.dumps(
                final_records, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            if len(serialized) > cfg.maximum_evidence_bytes:
                final_records = pack_details(final_records)
                serialized = json.dumps(
                    final_records, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            if len(serialized) > cfg.maximum_evidence_bytes:
                raise ResourceLimit("X evidence record budget exceeded after lossless compaction")
        return Evidence(final_arrays, final_records)

    try:
        return finalize(near_floor)
    except ResourceLimit:
        if (not cfg.near_floor_source_candidates_enabled
                or records['module_records']['near_floor_source']['status'] != 'EVALUATED'):
            raise
        # Evidence must fit losslessly. Withdraw only the new module as a
        # whole, preserving previous complete evidence and the original cap.
        near_record = records['module_records']['near_floor_source']
        return finalize(np.zeros(s.shape, bool), {
            'status': 'EVIDENCE_BUDGET_ABSTAINED',
            'reason': 'combined lossless evidence exceeds existing record allowance',
            'source_gates': 0, 'work': near_record.get('work', {}),
        })
