"""Opt-in final clutter review. Existing radial/near algorithms are not rewritten."""
from dataclasses import replace
import hashlib
import numpy as np
from ..data import Sweep,json_bytes,array_digest
from .context import ContextMetadata
from .background import for_native
from .classifier import protections,EchoClass,Family,Reason
from .engine import evaluate_volume
from .disposition import apply,CR
from ..near_measurement.backends import require,check_native_gatefilter


def attributes(cfg,low_quality_flag):
    return {"qc_clutter_fusion_version":cfg.version,"qc_clutter_fusion_sha256":cfg.digest,
            "qc_clutter_fusion_config":cfg.model_dump(mode="json"),
            "qc_clutter_fusion_low_quality_flag":int(low_quality_flag)}


def raw_sweep(n):
    times=np.asarray(n.ray_time)
    if times.dtype.kind=="M":
        times=None if np.isnat(times).any() else times.astype("datetime64[ns]").astype("int64")/1e9
    elif times.dtype.kind not in "fiu" or not np.isfinite(times).all():times=None
    return Sweep(n.name,n.azimuth,n.elevation,n.ranges,n.fields,n.field_available,
                 n.geometry_good,n.gap_after,times,original_indices=n.original_indices)


def metadata(n):
    # Never infer a 1-degree beam or a verified waveform from an array index.
    src=n.attrs.get("clutter_context_contract",{})
    if not isinstance(src,dict):raise ValueError("clutter context contract must be an object")
    allowed=set(ContextMetadata.__dataclass_fields__)
    if set(src)-allowed:raise ValueError("unknown clutter context contract fields")
    return ContextMetadata(**src)


def review_result(result,native,*,near_context=None):
    cfg=getattr(result.profile.volume_review,"clutter_fusion",None)
    if cfg is None:return result
    if cfg.no_rain_below_dbz!=result.profile.echo.no_rain_below_dbz:
        raise ValueError("fusion/parent no-rain semantics differ")
    if cfg.depolarization_backend=="wradlib":require("wradlib","wradlib",cfg.wradlib_version)
    if cfg.gatefilter_check=="pyart":require("arm_pyart","pyart",cfg.pyart_version)
    index={n.name:i for i,n in enumerate(native)}
    if len(index)!=len(native) or set(index)!={s.name for s in result.sweeps}:raise ValueError("fusion native/QC mismatch")
    by_name={s.name:s for s in result.sweeps}
    sweeps=[raw_sweep(n) for n in native];backgrounds=[];protect=[];raw_before=[]
    for n in native:
        old=by_name[n.name]
        if not np.array_equal(n.restore(n.fields["DBZH"]),old.dbzh_raw,equal_nan=True):raise ValueError("fusion raw QC mismatch")
        raw_before.append(array_digest(n.fields))
        group={**old.optional_qc_fields,"VALID_MASK":old.valid_mask}
        hard,local,prior=protections(group,result.profile.context.strong_support)
        protect.append(tuple(m[n.original_indices] for m in (hard,local,prior)))
        backgrounds.append(for_native(n,cfg))
    if near_context is not None:
        for n in native:
            if (str(n.attrs.get("radar_id","")).lower()!=near_context.radar_id.lower()
                    or str(n.attrs.get("scan_id",""))!=near_context.scan_id
                    or str(n.attrs.get("radar_config_version",""))!=near_context.processing_id):
                raise ValueError("frozen near context differs from current native identity")
    evidence=evaluate_volume(sweeps,cfg,backgrounds=backgrounds,protections=protect,metadata=[metadata(n) for n in native],near_context=near_context)
    updated=[];records=[]
    for n,ev in zip(native,evidence,strict=True):
        old=by_name[n.name]
        group={**old.optional_qc_fields,**old.qi_components,"DBZH_RAW":old.dbzh_raw,"DBZH_QC":old.dbzh_qc,
               "VALID_MASK":old.valid_mask,"QC_FLAGS":old.qc_flags,"QUALITY_INDEX":old.quality_index,
               "LOW_QUALITY_MASK":old.low_quality_mask}
        a={k:n.restore(v) for k,v in ev.arrays.items()}
        # Reorder both target matrices and values of donor-ray indices.
        for prefix in ("UPPER","DOPPLER"):
            for i,d in enumerate(native):
                mask=(a["CF_"+prefix+"_SWEEP"]==i)&(a["CF_"+prefix+"_RAY"]>=0)
                a["CF_"+prefix+"_RAY"][mask]=d.original_indices[a["CF_"+prefix+"_RAY"][mask]]
        out,delta=apply(group,a,cfg,low_quality_flag=result.profile.flag_masks["LOW_QUALITY"])
        receipt=check_native_gatefilter(n,group[CR],out[CR],cfg)
        optional={k:out[k] for k in old.optional_qc_fields}
        optional.update({k:v for k,v in out.items() if k.startswith("CF_")})
        updated.append(replace(old,optional_qc_fields=optional,qc_flags=out["QC_FLAGS"],quality_index=out["QUALITY_INDEX"],
            low_quality_mask=out["LOW_QUALITY_MASK"],qi_components={k:out[k] for k in old.qi_components}))
        records.append({"sweep":n.name,"evidence":ev.summary,"disposition":delta,"gatefilter":receipt,
                        "raw_digest":raw_before[index[n.name]]})
    if raw_before!=[array_digest(n.fields) for n in native]:raise RuntimeError("fusion modified native raw")
    summary=dict(result.summary);sr={k:dict(v) for k,v in summary["sweeps"].items()}
    for old,record in zip(updated,records,strict=True):
        sr[old.name]["clutter_fusion"]=record
        sr[old.name]["quantitative_eligible_gates"]=int((old.optional_qc_fields["QPE_ELIGIBLE_MASK"]==1).sum())
        sr[old.name]["action_counts"]={name:int((old.optional_qc_fields["QC_ACTION"]==i).sum()) for i,name in enumerate(("KEEP","DOWNWEIGHT","REJECT","MISSING"))}
    summary["sweeps"]=sr
    quality=np.concatenate([s.quality_index[np.isfinite(s.quality_index)] for s in updated])
    summary["mean_quality_index"]=float(quality.mean()) if quality.size else 0.
    summary["low_quality_gate_count"]=sum(int(s.low_quality_mask.sum()) for s in updated)
    path="qc/volume_review/clutter_fusion.json"
    payload=json_bytes({"schema":cfg.version,"config":cfg.model_dump(mode="json"),"config_sha256":cfg.digest,
        "sweeps":records,"donor_sweep_order":[n.name for n in native],"donor_ray_order":"original_acquisition",
        "background_is_optional":True,"radial_modules_changed":False,"operational_eligible":False})
    artifacts=dict(getattr(result,"volume_review_artifacts",None) or {});artifacts[path]=payload
    summary["clutter_fusion"]={"version":cfg.version,"config_sha256":cfg.digest,"mode":cfg.mode,
        "status":"RESOURCE_LIMIT_ABSTAINED" if any(r["evidence"]["status"]=="RESOURCE_LIMIT_ABSTAINED" for r in records) else "EVALUATED",
        "cr_loss_gates":sum(r["disposition"]["cr_loss_gates"] for r in records),
        "qpe_loss_gates":sum(r["disposition"]["qpe_loss_gates"] for r in records),
        "review_required":any(r["disposition"]["review_required"] for r in records),
        "evidence_path":path,"evidence_sha256":hashlib.sha256(payload).hexdigest(),"confirmed_gates":0}
    if cfg.near_revision is not None:
        summary["clutter_fusion"]["near_revision"] = {
            "version":cfg.near_revision.version,"mode":cfg.near_revision.mode,
            "status":"RESOURCE_LIMIT_ABSTAINED" if any(r["evidence"].get("near_revision",{}).get("status")=="RESOURCE_LIMIT_ABSTAINED" for r in records) else "EVALUATED",
            "net_new_cr_loss_gates":sum(r["disposition"]["near_revision_cr_loss_gates"] for r in records),
            "partial_cr_loss_gates":sum(r["disposition"]["near_partial_cr_loss_gates"] for r in records),
            "temporal_cr_loss_gates":sum(r["disposition"]["near_temporal_cr_loss_gates"] for r in records),
            "dem_cr_loss_gates":sum(r["disposition"]["near_dem_cr_loss_gates"] for r in records),
            "strong_near_cr_loss_gates":sum(r["disposition"].get("strong_near_cr_loss_gates",0) for r in records),
            "strong_near_quarantine_gates":sum(r["disposition"].get("strong_near_quarantine_gates",0) for r in records),
            "strong_near_object_propagated_gates":sum(r["disposition"].get("strong_near_object_propagated_gates",0) for r in records),
            "strong_near_dilated_gates":sum(r["disposition"].get("strong_near_dilated_gates",0) for r in records),
            "new_qpe_loss_gates":sum(r["disposition"]["near_revision_new_qpe_loss_gates"] for r in records),
            "operational_eligible":False}
    return replace(result,sweeps=tuple(updated),summary=summary,volume_review_artifacts=artifacts)


def annotate(group):
    for k in group:
        if not k.startswith("CF_") or not hasattr(group[k],"attrs"):continue
        meta={"extension":"clutter-fusion-20260921-v1","scores_are_probabilities":False}
        if k.endswith("_MASK"):meta.update(units="1",definition="0:false;1:true; unknown is not no-rain")
        if k=="CF_CLASS":meta["codes"]={str(v.value):v.name for v in EchoClass}
        if k=="CF_NR_STATE":
            from .near_joint import State
            meta["codes"]={str(v.value):v.name for v in State}
        if k=="CF_NR_REASON":
            from .near_joint import Reason as NearReason
            meta["bits"]={str(v.value):v.name for v in NearReason}
        if k=="CF_REASON":meta["bits"]={str(v.value):v.name for v in Reason}
        if k=="CF_FAMILY_BITS":meta["bits"]={str(v.value):v.name for v in Family}
        if k.startswith("CF_BEFORE_"):meta["semantics"]="exact_pre_fusion_state"
        if any(k.endswith(t) for t in ("_RAY","_GATE","_SWEEP")):
            meta["semantics"]="original acquisition indices; -1 unavailable; sweep index uses evidence.json ordered list"
        if k.startswith("CF_NR_TEMPORAL_") and k.endswith(("_SOURCE","_RAY","_GATE")):
            meta["semantics"]="sources list in this target sweep's near_revision.temporal receipt; ray/gate in original acquisition order; -1 unavailable"
        group[k].attrs.update(meta)
