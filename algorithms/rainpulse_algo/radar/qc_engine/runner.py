from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from time import perf_counter

import numpy as np

from ..qc import QCInputError, QCModuleRecord, QCResult, QCSweep
from ..qc_geometry import build_vertical_consistency_diagnostics
from ..qc_input import open_qc_input
from .adapters import adapt_sweep
from .algorithms import library_evidence
from .crossradar import fuse_crossradar, sweep_funnel
from .decision import Action, decide
from .finalize import finalize_decision
from .fragment_radials import apply_fragment_decision
from .hypotheses import graph_with_fallback
from .objects import radial_objects
from .paper_fusion import fuse_paper_decision, paper_evidence
from .phase import process_phase
from .radial import local_radial_candidates
from .range_signature import range_signatures
from .residual import residual_decision
from .stage_audit import Decider, StageAudit
from .standalone_evidence import stage_a, stage_a_key
from .support import weather_support as select_weather_support

QI_NAMES = (
    "QI_METEO",
    "QI_BLOCKAGE",
    "QI_BEAM_HEIGHT",
    "QI_ATTENUATION",
    "QI_INTERFERENCE",
    "QI_TIME",
    "QI_CALIBRATION",
    "QI_RANGE",
)


def run_open_source_qc(
    objects,
    profile,
    *,
    input_view=None,
    ancillary_maps=None,
    radial_context=None,
    radar_beam_context=None,
    created_at=None,
    paper_references_by_sweep=None,
    standalone_by_sweep=None,
    **kwargs,
):
    started = checkpoint = perf_counter()
    timings = {}
    if kwargs.get("phase_processing_profile") or kwargs.get("attenuation_profile"):
        raise QCInputError(
            "legacy phase/attenuation profiles cannot be mixed with the new QC engine"
        )
    if "health/summary.json" not in objects:
        raise QCInputError("normalized volume has no health summary")
    health = json.loads(objects["health/summary.json"])
    if health.get("health") not in {"HEALTHY", "DEGRADED"}:
        raise QCInputError("radar health is unavailable or invalid")
    view = input_view or open_qc_input(objects)
    if view.objects is not objects:
        raise QCInputError("QC input cache belongs to a different artifact")
    root = view.root
    if root.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise QCInputError("unexpected normalized radar contract")
    if health.get("radar_id") != root.attrs.get("radar_id"):
        raise QCInputError("health radar identity differs from normalized source")
    native = [adapt_sweep(root, f"sweep_{int(i):03d}", profile) for i in root["sweep_number"][:]]
    if not native:
        raise QCInputError("normalized radar volume contains no usable sweep")
    timings["input_adaptation_ms"] = (perf_counter() - checkpoint) * 1000
    checkpoint = perf_counter()
    # Stage 1 is independent of neighbouring final QC: no circular dependencies.
    if paper_references_by_sweep and profile.literature is None:
        raise QCInputError("paper references require the paper fusion profile")
    if set(paper_references_by_sweep or {}) - {s.name for s in native}:
        raise QCInputError("reference sweep absent from current volume")
    independent = []
    v7 = profile.evidence_graph
    unified = v7 is not None and v7.unified_stage_a
    standalones = {}
    for sweep in native:
        prior = (ancillary_maps or {}).get(sweep.name, {}).get("ground_clutter")
        if prior is not None:
            prior = prior[sweep.original_indices]
        if unified:
            if paper_references_by_sweep:
                raise QCInputError("V7 unified stage A does not silently mix external references")
            prepared = (standalone_by_sweep or {}).get(sweep.name)
            if prepared is not None and prepared.identity != stage_a_key(sweep, profile, prior):
                raise QCInputError(
                    "prepared stage A belongs to different raw/config/resource inputs"
                )
            prepared = prepared or stage_a(sweep, profile, prior)
            standalones[sweep.name] = prepared
            first = prepared.decision
            # Vertical support is evaluated using the same donor policy as other references.
            donor_arrays = dict(first.arrays)
            donor_arrays["REFLECTIVITY_TRUST_MASK"] = prepared.donor_usable.astype("uint8")
            from .decision import Decision

            first = Decision(donor_arrays, first.flags, first.quality)
            independent.append(
                (
                    prepared.library,
                    prepared.radial,
                    prepared.radial_record,
                    first,
                    prior,
                    prepared.objects,
                    prepared.papers,
                )
            )
            continue
        evidence = library_evidence(sweep, profile, prior)
        objects_evidence = (
            radial_objects(sweep, profile.rfi_objects) if profile.rfi_objects is not None else None
        )
        if objects_evidence is None:
            radial, radial_record = local_radial_candidates(sweep, profile.rfi)
        else:
            radial, radial_record = objects_evidence.candidate, objects_evidence.summary()
        first = decide(
            sweep,
            evidence,
            profile,
            rfi_candidate=radial,
            clutter_prior=prior,
            object_evidence=objects_evidence,
        )
        papers = None
        if profile.literature is not None:
            papers = paper_evidence(
                sweep, profile, (paper_references_by_sweep or {}).get(sweep.name)
            )
        independent.append(
            (evidence, radial, radial_record, first, prior, objects_evidence, papers)
        )
    timings["independent_evidence_ms"] = (perf_counter() - checkpoint) * 1000
    checkpoint = perf_counter()
    vertical = build_vertical_consistency_diagnostics(
        tuple(
            {
                "dbzh": np.where(
                    item[3].arrays["REFLECTIVITY_TRUST_MASK"] == 1, sweep.fields["DBZH"], np.nan
                ),
                "azimuth": sweep.azimuth,
                "elevation": sweep.elevation,
                "range": sweep.ranges,
            }
            for sweep, item in zip(native, independent, strict=True)
        ),
        minimum_dbzh=profile.context.echo_threshold_dbz,
        support_tolerance_db=15,
        maximum_range_m=150000,
        strict_observability=True,
        maximum_azimuth_offset_deg=0.75,
        radar_beam_context=radar_beam_context,
    )
    timings["vertical_support_ms"] = (perf_counter() - checkpoint) * 1000
    results, sweep_records = [], {}
    for index, (sweep, item) in enumerate(zip(native, independent, strict=True)):
        checkpoint = perf_counter()
        evidence, radial, radial_record, _, prior, objects_evidence, papers = item
        weather = vertical.probabilities[index].copy()
        context = (radial_context or {}).get(sweep.name, {})
        cross = context.get("WEATHER_SUPPORT_SCORE")
        if cross is not None:
            cross = np.asarray(cross)[sweep.original_indices]
        weather = select_weather_support(
            weather, cross, radial, split=profile.context.split_radial_weather_support
        )
        if profile.rfi_objects is not None and not profile.context.enabled:
            weather[:] = np.nan
            context = {}
        tracker = StageAudit(sweep.shape) if v7 is not None and v7.audit_enabled else None
        graph_record = None
        persistence = context.get("TEMPORAL_CANDIDATE_PERSISTENCE")
        temporal_count = context.get("TEMPORAL_RFI_SAMPLE_COUNT")
        decision = decide(
            sweep,
            evidence,
            profile,
            weather_support=weather,
            rfi_candidate=radial,
            clutter_prior=prior,
            object_evidence=objects_evidence,
            temporal_persistence=(
                np.asarray(persistence)[sweep.original_indices] if persistence is not None else None
            ),
            temporal_samples=(
                np.asarray(temporal_count)[sweep.original_indices]
                if temporal_count is not None
                else None
            ),
        )
        if tracker is not None:
            tracker.observe(Decider.BASE_MOMENT_OBJECT, decision)
        if papers is not None:
            decision = fuse_paper_decision(
                sweep,
                evidence,
                decision,
                papers,
                profile,
                weather_support=weather,
                temporal_persistence=(
                    np.asarray(persistence)[sweep.original_indices]
                    if persistence is not None
                    else None
                ),
                temporal_samples=(
                    np.asarray(temporal_count)[sweep.original_indices]
                    if temporal_count is not None
                    else None
                ),
            )
        if tracker is not None:
            tracker.observe(Decider.PAPER_FUSION, decision)
        range_evidence = None
        baseline_quality = decision.quality.copy() if profile.cross_radar is not None else None
        if profile.cross_radar is not None:
            range_evidence = (
                standalones[sweep.name].range_evidence
                if unified
                else range_signatures(sweep, profile.cross_radar)
            )
            decision = fuse_crossradar(
                sweep, decision, range_evidence, profile, weather_support=weather
            )
        if tracker is not None:
            tracker.observe(Decider.RANGE_SIGNATURE, decision)
        residual_record = None
        v5_quality = None
        if profile.residual is not None:
            v5_quality = decision.quality.copy()
            decision, residual_record = residual_decision(
                sweep, decision, profile, weather_support=weather
            )

        if tracker is not None:
            tracker.observe(Decider.RESIDUAL, decision)
        v7_baseline_quality = decision.quality.copy() if v7 is not None else None
        if v7 is not None:
            decision.arrays["V7_BASELINE_REJECT_MASK"] = (
                decision.arrays["QC_ACTION"] == Action.REJECT
            ).astype("uint8")
            decision.arrays["V7_BASELINE_QUARANTINE_MASK"] = decision.arrays[
                "RFI_QUARANTINE_MASK"
            ].copy()
            decision.arrays["V7_BASELINE_ELIGIBLE_MASK"] = decision.arrays[
                "QPE_ELIGIBLE_MASK"
            ].copy()
            if v7.graph_enabled:
                decision, graph_record = graph_with_fallback(
                    sweep, decision, profile, weather_support=weather
                )
            if v7.fragment_radials is not None:
                decision, fragment_record = apply_fragment_decision(
                    sweep,
                    decision,
                    v7.fragment_radials,
                    profile,
                    weather_support=weather,
                    cross_support=cross,
                )
                graph_record = {**(graph_record or {}), "fragment_radials": fragment_record}
            if tracker is not None:
                tracker.observe(Decider.GRAPH, decision)
            if profile.pipeline_version in {
                "qc-opensource-7.1.0",
                "qc-opensource-7.2.0",
                "qc-opensource-7.2.1",
                "qc-opensource-7.3.0",
                "qc-opensource-7.3.1",
                "qc-opensource-7.3.2",
                "qc-opensource-7.3.3",
                "qc-opensource-7.3.4",
                "qc-opensource-7.3.5",
                "qc-opensource-7.3.6",
                "qc-opensource-7.3.7",
                "qc-opensource-7.3.8",
            }:
                from .object_consensus.adapter import evaluate_native, scalar_completion
                from .object_consensus.config import Policy

                oldq = decision.arrays["RFI_QUARANTINE_MASK"].copy()
                p2_denominator = decision.arrays["QPE_ELIGIBLE_MASK"].copy()
                decision, oc_evidence, oc_outcome = evaluate_native(
                    sweep,
                    decision,
                    phase_period=profile.geometry.phase_period_deg,
                    low_quality_flag=profile.flag_masks["LOW_QUALITY"],
                    policy=Policy(
                        mode="experiment_quarantine",
                        allow_coherent_quarantine=True,
                        acknowledge_uncalibrated_model=True,
                    ),
                )
                decision.arrays["OC1_BASELINE_QUARANTINE_MASK"] = oldq
                decision.arrays["OC1_ADDED_QUARANTINE_MASK"] = oc_outcome.added_quarantine.astype(
                    "uint8"
                )
                for name in ("state", "family_code", "reason", "fold_id"):
                    if name in oc_evidence.arrays:
                        decision.arrays["OC1_" + name.upper()] = oc_evidence.arrays[name]
                graph_record = {
                    **(graph_record or {}),
                    "object_consensus": scalar_completion(oc_evidence, oc_outcome),
                }
                if tracker is not None:
                    tracker.observe(Decider.OBJECT_CONSENSUS, decision)
                if profile.generalization is not None:
                    from .generalization import broad_source_review

                    decision, p2_record = broad_source_review(
                        sweep,
                        decision,
                        profile,
                        oc_evidence,
                        oc_outcome,
                        weather_support=weather,
                        eligible_before_oc1=p2_denominator,
                    )
                    graph_record["generalization"] = p2_record
                    if tracker is not None:
                        tracker.observe(Decider.GENERALIZATION, decision)
            if unified:
                decision.arrays["V7_STAGE_A_DONOR_USABLE_MASK"] = standalones[
                    sweep.name
                ].donor_usable.astype("uint8")
                decision.arrays["V7_STAGE_A_DONOR_UNKNOWN_MASK"] = standalones[
                    sweep.name
                ].donor_unknown.astype("uint8")
        nonprecip_record = None
        if profile.nonprecip_review is not None:
            from .review_extension.runtime import apply_nonprecip_review

            decision, nonprecip_record = apply_nonprecip_review(
                sweep, decision, evidence, profile,
                ancillary=(ancillary_maps or {}).get(sweep.name, {}),
                context=context, weather_support=weather,
            )
            if tracker is not None:
                tracker.observe(Decider.NONPRECIP_REVIEW, decision)
        timings[sweep.name + ".decision_fusion_ms"] = (perf_counter() - checkpoint) * 1000
        checkpoint = perf_counter()
        phase, phase_record = process_phase(
            sweep, decision.arrays, profile, context.get("environment")
        )
        timings[sweep.name + ".phase_ms"] = (perf_counter() - checkpoint) * 1000
        checkpoint = perf_counter()
        quality, observed, low, flags = finalize_decision(
            sweep,
            decision,
            profile,
            health,
            baseline_quality=baseline_quality,
            v5_quality=v5_quality,
            v7_baseline_quality=v7_baseline_quality,
        )
        if tracker is not None:
            tracker.observe(Decider.HEALTH_QUALITY, decision)
            decision.arrays.update(tracker.arrays())
            decision.arrays["V7_VERTICAL_SUPPORT_SCORE"] = vertical.probabilities[index].copy()
            decision.arrays["V7_CROSS_RADAR_SUPPORT_SCORE"] = (
                cross.copy() if cross is not None else np.full(sweep.shape, np.nan, "float32")
            )
        timings[sweep.name + ".finalize_ms"] = (perf_counter() - checkpoint) * 1000
        optional = {**evidence.arrays, **decision.arrays, **phase}
        optional["P_VERTICAL_CONSISTENCY_AVAILABLE_MASK"] = vertical.available_masks[index]
        optional["VERTICAL_HEIGHT_DIFFERENCE_M"] = vertical.height_differences_m[index]
        for field in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
            if field in sweep.fields:
                optional[f"{field}_RAW"] = sweep.fields[field].copy()
        temporal = context.get("TEMPORAL_CANDIDATE_PERSISTENCE")
        if temporal is not None and profile.rfi_objects is None:
            optional["TEMPORAL_CANDIDATE_PERSISTENCE"] = np.asarray(temporal)[
                sweep.original_indices
            ]
        missing_float = np.full(sweep.shape, np.nan, dtype="float32")
        components = {name: missing_float.copy() for name in QI_NAMES}
        components["QI_METEO"] = quality.copy()
        components["QI_INTERFERENCE"] = np.where(
            observed, np.where(decision.arrays["QC_ACTION"] == Action.REJECT, 0.0, 1.0), np.nan
        ).astype("float32")
        if profile.rfi_objects is not None:
            components["QI_INTERFERENCE"] = np.where(
                observed,
                np.where(
                    decision.arrays["RFI_RISK_STATE"] == 3,
                    0.0,
                    np.where(
                        decision.arrays["RFI_QUARANTINE_MASK"] == 1,
                        profile.rfi_objects.quarantine_quality,
                        1.0,
                    ),
                ),
                np.nan,
            ).astype("float32")
        restore = sweep.restore
        results.append(
            QCSweep(
                name=sweep.name,
                dbzh_raw=restore(sweep.fields["DBZH"]),
                dbzh_qc=restore(np.where(observed, sweep.fields["DBZH"], np.nan).astype("float32")),
                optional_qc_fields={key: restore(value) for key, value in optional.items()},
                quality_index=restore(quality),
                qi_components={k: restore(v) for k, v in components.items()},
                qc_flags=restore(flags),
                valid_mask=restore(observed.astype("uint8")),
                low_quality_mask=restore(low.astype("uint8")),
                p_meteo=restore(evidence.arrays["METEO_SCORE"]),
                p_ap=missing_float.copy(),
                p_sea_clutter=missing_float.copy(),
                p_radial_interference=restore(
                    np.where(observed, radial.astype(float), np.nan).astype("float32")
                ),
                p_meteo_dual_pol=restore(evidence.arrays["METEO_SCORE"]),
                p_vertical_consistency=restore(vertical.probabilities[index]),
                interference_type=np.zeros(sweep.shape, dtype="uint8"),
            )
        )
        sweep_records[sweep.name] = {
            **({"nonprecip_review": nonprecip_record} if nonprecip_record is not None else {}),
            **({"v7_audit": tracker.summary()} if tracker is not None else {}),
            **({"v7_graph": graph_record} if graph_record is not None else {}),
            **(
                {
                    "v7_stage_a": {
                        k: v
                        for k, v in standalones[sweep.name].summary.items()
                        if k != "elapsed_ms"
                    }
                }
                if unified
                else {}
            ),
            **({"residual_v6": residual_record} if residual_record is not None else {}),
            **(
                {
                    "literature": papers.metadata,
                    "paper_confirmed_additions": int(
                        decision.arrays["PAPER_CONFIRMED_ADDITION_MASK"].sum()
                    ),
                    "paper_quarantined_additions": int(
                        decision.arrays["PAPER_QUARANTINED_ADDITION_MASK"].sum()
                    ),
                }
                if papers is not None
                else {}
            ),
            **(
                {
                    "cross_radar": range_evidence.summary,
                    "decision_funnel": sweep_funnel(sweep, decision.arrays, profile.cross_radar),
                }
                if range_evidence is not None
                else {}
            ),
            "input_audit": sweep.audit,
            "evidence": [
                {key: value for key, value in record.items() if key != "elapsed_ms"}
                for record in evidence.records
            ],
            "radial": radial_record,
            "phase": phase_record,
            "rfi_quarantined_gates": int(
                decision.arrays.get("RFI_QUARANTINE_MASK", np.zeros(sweep.shape)).sum()
            ),
            "quantitative_eligible_gates": int((decision.arrays["QPE_ELIGIBLE_MASK"] == 1).sum()),
            "rfi_temporal_confirmed_gates": int(
                decision.arrays.get("RFI_TEMPORAL_USED_MASK", np.zeros(sweep.shape)).sum()
            ),
            "rfi_residual_confirmed_gates": int(
                decision.arrays.get("RFI_RESIDUAL_PROMOTED_MASK", np.zeros(sweep.shape)).sum()
            ),
            "action_counts": {
                code.name: int(np.count_nonzero(decision.arrays["QC_ACTION"] == code))
                for code in Action
            },
        }
    finite_quality = np.concatenate(
        [s.quality_index[np.isfinite(s.quality_index)] for s in results]
    )
    radial_flags = [
        ((s.qc_flags & profile.flag_masks["RADIAL_INTERFERENCE"]) != 0) for s in results
    ]
    modules = [
        QCModuleRecord(
            "open_source_qc",
            profile.pipeline_version,
            "applied",
            ("DBZH", "RHOHV", "ZDR", "PHIDP"),
            ("QC_ACTION", "QC_FLAGS", "METEO_SCORE", "REFLECTIVITY_TRUST_MASK"),
            "engineering_candidate_not_operationally_accepted",
            {},
        )
    ]
    for name, available in (
        ("static_ground_clutter", any(item[4] is not None for item in independent)),
        ("sea_ap", False),
    ):
        modules.append(
            QCModuleRecord(
                name,
                "open-source-v1",
                "applied" if available else "skipped",
                ("DBZH",),
                (),
                None if available else "missing_verified_asset",
                {},
            )
        )
    if profile.review_extension_version is not None:
        modules.append(QCModuleRecord(
            "qc_review", profile.review_extension_version, "applied",
            ("DBZH",), ("SRC_REVIEW_QUALIFIED_MASK", "NP_CLASS", "NP_QUARANTINE_MASK"),
            "engineering_candidate_not_operationally_accepted", {},
        ))
    summary = {
        "schema_version": "1.1",
        "engine": "open_source",
        "operational_eligible": False,
        "radar_id": root.attrs["radar_id"],
        "scan_id": root.attrs.get("scan_id"),
        "qc_profile": profile.profile_version,
        "qc_pipeline_version": profile.pipeline_version,
        "decision_version": profile.decision_version,
        "flag_definition_version": profile.flag_definition_version,
        "parameters_hash": profile.parameters_hash,
        "libraries": independent[0][0].libraries,
        "health_state": health["health"],
        "sweeps": sweep_records,
        "mean_quality_index": float(finite_quality.mean()) if finite_quality.size else 0.0,
        "valid_gate_count": sum(int(s.valid_mask.sum()) for s in results),
        "missing_gate_count": sum(int((s.valid_mask == 0).sum()) for s in results),
        "low_quality_gate_count": sum(int(s.low_quality_mask.sum()) for s in results),
        "no_rain_gate_count": sum(
            int(((s.valid_mask == 1) & (s.dbzh_raw < profile.echo.no_rain_below_dbz)).sum())
            for s in results
        ),
        "radial_interference_ray_count": sum(int(m.any(axis=1).sum()) for m in radial_flags),
        "radial_interference_gate_count": sum(int(m.sum()) for m in radial_flags),
        "radial_interference_area_km2": None,
        "module_statuses": {m.name: m.status for m in modules},
        "module_records": [m.value() for m in modules],
        "vertical_context": vertical.metrics,
    }
    if profile.review_extension_version is not None:
        from .review_extension.runtime import review_summary

        summary["review_extension_version"] = profile.review_extension_version
        if profile.nonprecip_review is not None:
            summary["nonprecip_review_summary"] = review_summary(sweep_records)
    if profile.generalization is not None:
        from .quality_policy import health_facets

        p2_summaries = [
            rec.get("v7_graph", {}).get("generalization", {}) for rec in sweep_records.values()
        ]
        summary["health_facets"] = health_facets(health, profile)
        summary["generalization_summary"] = {
            "review_required": any(rec.get("review_required", False) for rec in p2_summaries),
            "new_confirmed_gates": 0,
            "added_quarantine_gates": sum(
                rec.get("added_quarantine_gates", 0) for rec in p2_summaries
            ),
            "administrative_penalty_removed_gates": sum(
                int(s.optional_qc_fields["P2_ADMIN_PENALTY_REMOVED_MASK"].sum()) for s in results
            ),
            "operational_eligible": False,
        }
    timings["total_compute_ms"] = (perf_counter() - started) * 1000
    # Runtime telemetry must not alter immutable artifact hashes.
    if kwargs.get("timing_sink") is not None:
        kwargs["timing_sink"].update(timings)
    logging.getLogger(__name__).info(
        "qc_compute_timing scan_id=%s timings=%s",
        root.attrs.get("scan_id"),
        json.dumps(timings, sort_keys=True),
    )
    result = QCResult(
        profile, tuple(results), tuple(modules), health, summary, created_at or datetime.now(UTC)
    )
    if getattr(profile, "volume_review", None) is not None:
        from .volume_review.integration import review_result

        result = review_result(result, native)
    return result
