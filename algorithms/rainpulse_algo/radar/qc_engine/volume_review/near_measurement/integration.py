"""Post-VOR adapter. Nothing happens without explicit near_measurement config."""
from dataclasses import replace
import hashlib
import numpy as np
from ..data import array_digest, json_bytes
from .core import evaluate
from .disposition import apply, CR
from .backends import check_native_gatefilter


def attributes(cfg, low_quality_flag):
    return {"qc_near_measurement_version": cfg.version, "qc_near_measurement_sha256": cfg.digest,
            "qc_near_measurement_config": cfg.model_dump(mode="json"),
            "qc_near_measurement_low_quality_flag": int(low_quality_flag),
            "qc_near_measurement_mode": cfg.mode, "operational_eligible": False}


def review_result(result, native):
    cfg = getattr(result.profile.volume_review, "near_measurement", None)
    if cfg is None: return result
    if cfg.mode == "experiment" and result.profile.volume_review.mode != "experiment_quarantine":
        raise ValueError("near experiment requires the parent P3 experimental mode")
    if cfg.no_rain_below_dbz != result.profile.echo.no_rain_below_dbz:
        raise ValueError("near no-rain threshold differs from the source profile")
    if cfg.quarantine_quality >= result.profile.quality_index.quantitative_minimum:
        raise ValueError("near quarantine must remain below quantitative threshold")
    if sum(int(np.prod(n.shape)) for n in native) > cfg.maximum_volume_gates:
        raise ValueError("near measurement volume resource limit; no partial publication")
    if len(native) != len(result.sweeps) or len({n.name for n in native}) != len(native):
        raise ValueError("near native/QC sweep identity mismatch")
    lookup = {n.name: n for n in native}
    if set(lookup) != {s.name for s in result.sweeps}: raise ValueError("near sweep set differs")
    updated, records = [], []
    summary = dict(result.summary)
    sweep_summary = {k: dict(v) for k, v in summary["sweeps"].items()}
    for old in result.sweeps:
        n = lookup[old.name]
        raw_fields = {k + "_RAW": n.restore(v) for k, v in n.fields.items()}
        if not np.array_equal(raw_fields["DBZH_RAW"], old.dbzh_raw, equal_nan=True):
            raise ValueError("near source RAW differs from QC identity")
        group = {**old.optional_qc_fields, **old.qi_components,
                 "DBZH_RAW": old.dbzh_raw, "DBZH_QC": old.dbzh_qc,
                 "VALID_MASK": old.valid_mask, "QC_FLAGS": old.qc_flags,
                 "QUALITY_INDEX": old.quality_index, "LOW_QUALITY_MASK": old.low_quality_mask}
        input_fields = {**group, **raw_fields}
        available = {k: n.restore(v) for k, v in n.field_available.items()}
        az = np.empty_like(n.azimuth); az[n.original_indices] = n.azimuth
        good = np.empty_like(n.geometry_good); good[n.original_indices] = n.geometry_good
        gap = n.gap_after[np.argsort(n.azimuth % 360, kind="stable")]
        before = array_digest(raw_fields)
        evidence = evaluate(input_fields, az, n.ranges, cfg, available=available,
                            geometry_good=good, gap_after=gap)
        after, delta = apply(group, evidence.arrays, cfg, low_quality_flag=result.profile.flag_masks["LOW_QUALITY"])
        gatefilter = check_native_gatefilter(n, group[CR], after[CR], cfg)
        if before != array_digest(raw_fields): raise RuntimeError("near stage changed RAW values")
        optional = {k: after[k] for k in old.optional_qc_fields}
        optional.update({k: v for k, v in after.items() if k.startswith("NMR_")})
        components = {k: after[k] for k in old.qi_components}
        updated.append(replace(old, optional_qc_fields=optional, qc_flags=after["QC_FLAGS"],
                               quality_index=after["QUALITY_INDEX"], low_quality_mask=after["LOW_QUALITY_MASK"],
                               qi_components=components))
        rec = {"sweep": old.name, "raw_fields_sha256": before, "evidence": evidence.summary,
               "disposition": delta, "gatefilter": gatefilter}
        records.append(rec); sweep_summary[old.name]["near_measurement"] = rec
        sweep_summary[old.name]["quantitative_eligible_gates"] = int(after["QPE_ELIGIBLE_MASK"].sum())
        sweep_summary[old.name]["action_counts"] = {name: int((after["QC_ACTION"] == i).sum())
                      for i, name in enumerate(("KEEP", "DOWNWEIGHT", "REJECT", "MISSING"))}
    artifacts = dict(getattr(result, "volume_review_artifacts", None) or {})
    payload = json_bytes({"schema": "rainpulse.near-measurement-evidence-v1",
        "config": cfg.model_dump(mode="json"), "config_sha256": cfg.digest, "sweeps": records,
        "operational_eligible": False})
    path = "qc/volume_review/near_measurement.json"; artifacts[path] = payload
    summary["near_measurement"] = {"status": "AUDIT_ONLY" if cfg.mode == "audit" else "EXPERIMENT_APPLIED",
        "version": cfg.version, "config_sha256": cfg.digest, "evidence_path": path,
        "evidence_sha256": hashlib.sha256(payload).hexdigest(), "confirmed_gates": 0,
        "review_required": any(x["disposition"]["review_required"] for x in records),
        **{key: sum(x["disposition"][key] for x in records) for key in (
            "added_quarantine_gates", "qpe_loss_gates", "cr_loss_gates", "cr_nonmet_loss_gates", "cr_uncertainty_only_loss_gates")}}
    summary["sweeps"] = sweep_summary
    values = np.concatenate([s.quality_index[np.isfinite(s.quality_index)] for s in updated])
    summary["mean_quality_index"] = float(values.mean()) if values.size else 0.
    summary["low_quality_gate_count"] = sum(int(s.low_quality_mask.sum()) for s in updated)
    return replace(result, sweeps=tuple(updated), summary=summary, volume_review_artifacts=artifacts)
