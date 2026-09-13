from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np

from ..qc import QCInputError, QCModuleRecord, QCResult, QCSweep
from ..qc_geometry import build_vertical_consistency_diagnostics
from ..qc_input import open_qc_input
from .adapters import adapt_sweep
from .algorithms import library_evidence
from .decision import Action, decide
from .objects import radial_objects
from .phase import process_phase
from .radial import local_radial_candidates

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
    **kwargs,
):
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
    # Stage 1 is independent of neighbouring final QC: no circular dependencies.
    independent = []
    for sweep in native:
        prior = (ancillary_maps or {}).get(sweep.name, {}).get("ground_clutter")
        if prior is not None:
            prior = prior[sweep.original_indices]
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
        independent.append((evidence, radial, radial_record, first, prior, objects_evidence))
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
    results, sweep_records = [], {}
    for index, (sweep, item) in enumerate(zip(native, independent, strict=True)):
        evidence, radial, radial_record, _, prior, objects_evidence = item
        weather = vertical.probabilities[index].copy()
        context = (radial_context or {}).get(sweep.name, {})
        cross = context.get("WEATHER_SUPPORT_SCORE")
        if cross is not None:
            cross = np.asarray(cross)[sweep.original_indices]
            weather = np.fmax(weather, cross)
        if profile.rfi_objects is not None and not profile.context.enabled:
            weather[:] = np.nan
            context = {}
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
        phase, phase_record = process_phase(
            sweep, decision.arrays, profile, context.get("environment")
        )
        quality = decision.quality.copy()
        if health["health"] == "DEGRADED":
            quality *= profile.health_gate.degraded_quality_multiplier
        observed = sweep.field_available["DBZH"]
        low = observed & (quality < profile.quality_index.low_quality_threshold)
        flags = decision.flags.copy()
        flags[low] |= profile.flag_masks["LOW_QUALITY"]
        # Health may lower quantitative eligibility even when a local field was trusted.
        eligible = (decision.arrays["QPE_ELIGIBLE_MASK"] == 1) & (
            quality >= profile.quality_index.quantitative_minimum
        )
        decision.arrays["QPE_ELIGIBLE_MASK"] = eligible.astype("uint8")
        decision.arrays["DBZH_USABLE"] = np.where(eligible, sweep.fields["DBZH"], np.nan).astype(
            "float32"
        )
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
            "quantitative_eligible_gates": int(eligible.sum()),
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
    return QCResult(
        profile, tuple(results), tuple(modules), health, summary, created_at or datetime.now(UTC)
    )
