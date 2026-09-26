"""Thin adapters for RainPulse QCResult/NativeSweep; heavy imports remain lazy."""

from rainpulse_algo.performance import (timed as _perf_timed, measure as _perf_measure)
from dataclasses import replace
import numpy as np
from . import VERSION
from .data import Sweep, json_bytes
from .engine import evaluate
from .disposition import dispose


def from_native(native):
    times=np.asarray(native.ray_time)
    if times.dtype.kind=="M":
        times=times.astype("datetime64[ns]").astype("int64")/1e9
    elif times.dtype.kind not in "fiu":
        times=None
    return Sweep(native.name,native.azimuth,native.elevation,native.ranges,
                 native.fields,native.field_available,native.geometry_good,native.gap_after,times)


@_perf_timed("s.volume_extensions")
def review_result(result, native, *, near_clutter_context=None):
    cfg=getattr(result.profile,"volume_review",None)
    if cfg is None:
        return result
    sweeps=[from_native(n) for n in native]
    by_name={s.name:s for s in result.sweeps}
    if set(by_name)!={s.name for s in sweeps}:
        raise ValueError("QC/native volume mismatch")
    protected=[]
    for n in native:
        a=by_name[n.name].optional_qc_fields
        wx=np.zeros(n.shape,bool)
        # Keep actual positive vetted context; missing scores provide no negative evidence.
        for k in ("V7_VERTICAL_SUPPORT_SCORE","V7_CROSS_RADAR_SUPPORT_SCORE"):
            if k in a:
                score=np.asarray(a[k])[n.original_indices]
                wx |= np.isfinite(score)&(score>=result.profile.context.strong_support)
        protected.append(wx)
    with _perf_measure("s.vor_evidence"):
        ev=evaluate(sweeps,cfg,protected=protected)
    updated=[]; summary=dict(result.summary); records={k:dict(v) for k,v in summary["sweeps"].items()}
    dispositions=[]
    for n,new in zip(native,ev.arrays,strict=True):
        old=by_name[n.name]
        restored={k:n.restore(v) for k,v in new.items()}
        # Restore BOTH matrix row order and the donor-row index values themselves.
        if "VOR_DONOR_RAY" in restored:
            for donor_index, donor in enumerate(native):
                mapped=(restored["VOR_DONOR_SWEEP"]==donor_index)&(restored["VOR_DONOR_RAY"]>=0)
                restored["VOR_DONOR_RAY"][mapped]=donor.original_indices[restored["VOR_DONOR_RAY"][mapped]]
        a,flags,quality,diag=dispose(old.optional_qc_fields,old.qc_flags,old.quality_index,
                 old.valid_mask==1,restored,cfg,low_quality_flag=result.profile.flag_masks["LOW_QUALITY"])
        a["VOR_BEFORE_QC_FLAGS"]=old.qc_flags.copy()
        a["VOR_BEFORE_QUALITY_INDEX"]=old.quality_index.copy()
        a["VOR_BEFORE_LOW_QUALITY_MASK"]=old.low_quality_mask.copy()
        for key in ("QI_METEO","QI_INTERFERENCE"):
            if key in old.qi_components:
                a["VOR_BEFORE_"+key]=old.qi_components[key].copy()
        added=a["VOR_QUARANTINE_MASK"]==1
        components={k:v.copy() for k,v in old.qi_components.items()}
        for key in ("QI_METEO","QI_INTERFERENCE"):
            if key in components:
                components[key][added]=np.minimum(components[key][added],cfg.quarantine_quality)
        low=old.low_quality_mask.copy(); low[added]=1
        updated.append(replace(old,optional_qc_fields=a,qc_flags=flags,quality_index=quality,
                               low_quality_mask=low,qi_components=components))
        records[n.name]["volume_review"]=diag
        records[n.name]["quantitative_eligible_gates"]=int(a["QPE_ELIGIBLE_MASK"].sum())
        records[n.name]["action_counts"]={name:int((a["QC_ACTION"]==i).sum()) for i,name in enumerate(("KEEP","DOWNWEIGHT","REJECT","MISSING"))}
        dispositions.append(diag)
    model_records=[]
    for n,models in zip(native,ev.models):
        model_records.append([{**m,"native_sorted_ray":m["ray"],"ray":int(n.original_indices[m["ray"]])} for m in models])
    detail={"objects":ev.objects,"reference_models":model_records,"links":ev.links,
            "sweep_order":[s.name for s in sweeps],"raw_digests":ev.summary["raw_digests"],
            "raw_digest_view":"native_sorted_raw",
            "record_ray_order":"original_acquisition_order",
            "native_to_original_ray":[n.original_indices.tolist() for n in native]}
    artifacts={"qc/volume_review/evidence.json":json_bytes(detail)}
    import hashlib
    summary["volume_review"]={**ev.summary,"evidence_path":"qc/volume_review/evidence.json",
            "evidence_sha256":hashlib.sha256(artifacts["qc/volume_review/evidence.json"]).hexdigest(),
            "added_quarantine_gates":sum(x["added_quarantine_gates"] for x in dispositions),
            "review_required":any(x["review_required"] for x in dispositions)}
    summary["sweeps"]=records
    values=np.concatenate([s.quality_index[np.isfinite(s.quality_index)] for s in updated])
    summary["mean_quality_index"]=float(values.mean()) if values.size else 0.
    summary["low_quality_gate_count"]=sum(int(s.low_quality_mask.sum()) for s in updated)
    reviewed = replace(result,sweeps=tuple(updated),summary=summary,volume_review_artifacts=artifacts)
    if cfg.near_measurement is not None:
        from .near_measurement.integration import review_result as review_near_result
        with _perf_measure("s.nmr"):
            reviewed = review_near_result(reviewed, native)
    if cfg.receiver_domain is not None:
        from .receiver_domain.integration import review_result as review_receiver
        with _perf_measure("s.rdr"):
            reviewed = review_receiver(reviewed, native)
    if cfg.clutter_fusion is not None:
        from .clutter_fusion.integration import review_result as review_clutter
        with _perf_measure("s.cf"):
            reviewed = review_clutter(reviewed, native, near_context=near_clutter_context)
    return reviewed


def root_attributes(profile):
    cfg=getattr(profile,"volume_review",None)
    if cfg is None:
        return {}
    near = {}
    if cfg.clutter_fusion is not None:
        from .clutter_fusion.integration import attributes as clutter_attributes
        near.update(clutter_attributes(cfg.clutter_fusion, profile.flag_masks["LOW_QUALITY"]))
    if cfg.near_measurement is not None:
        from .near_measurement.integration import attributes
        near.update(attributes(cfg.near_measurement, profile.flag_masks["LOW_QUALITY"]))
    if cfg.receiver_domain is not None:
        from .receiver_domain.integration import attributes as receiver_attributes
        near.update(receiver_attributes(cfg.receiver_domain, profile.flag_masks["LOW_QUALITY"]))
    nonprecip = getattr(profile,"nonprecip_review",None)
    if nonprecip is not None and (getattr(nonprecip,"near_reliability",None) is not None
            or (cfg.clutter_fusion is not None and cfg.clutter_fusion.near_revision is not None)):
        near.update(qc_near_clutter_enabled=bool(nonprecip.near_enabled),
                    qc_near_clutter_mode=nonprecip.mode,
                    qc_near_clutter_allowed_classes=list(nonprecip.quarantine_classes),
                    qc_near_clutter_policy=nonprecip.model_dump(mode="json"))
    return {**near, "qc_volume_review_version":VERSION,"qc_volume_review_phase":cfg.phase,
            "qc_volume_review_mode":cfg.mode,"qc_volume_review_sha256":cfg.digest,
            "cr_unknown_policy":cfg.unknown_cr_policy,
            "cr_qualification_version":"reflectivity-cr-eligibility-v1",
            "volume_quarantine_quality":cfg.quarantine_quality,
            "volume_low_quality_flag":int(profile.flag_masks["LOW_QUALITY"]),"cr_height_datum":"above_radar_effective_4_3_earth",
            "operational_eligible":False}
