"""Build all evidence before a whole-volume action is published."""
from dataclasses import dataclass
import numpy as np
from ..data import ResourceLimit
from .features import extract
from .context import derive, empty as empty_context
from .background import empty as empty_background
from .classifier import decide


@dataclass(frozen=True)
class FusionEvidence:
    arrays: dict
    summary: dict


def zero_evidence(s,cfg,reason):
    z,obs=s.moment("DBZH");obs = obs & (z>=-32)&(z<=80)
    a={"CF_"+k+"_MASK":np.zeros(s.shape,"uint8") for k in ("OBSERVED","DOMAIN","SAFE","STRONG",
        "WEATHER_PROXY","POLAR_AVAILABLE","DR_AVAILABLE","BIO_AVAILABLE","ZDR_TAIL",
        "HARD_WEATHER","LOCAL_WEATHER","LEGACY_PROTECTED")}
    a["CF_OBSERVED_MASK"]=obs.astype("uint8")
    for k in ("Z_TEXTURE_DB","PHI_CIRCSTD_DEG","PHI_JITTER_DEG","DR_DB","POLAR_SCORE","RHO_SCORE",
              "TEXTURE_SCORE","BIO_ZDR_SCORE","BIO_PHASE_SCORE","BIO_SCORE","NEIGHBOUR_FRACTION"):
        a["CF_"+k]=np.full(s.shape,np.nan,"float32")
    for k in ("POLAR_SAMPLE_COUNT","TEXTURE_SAMPLE_COUNT","PHASE_PAIR_COUNT"):
        a["CF_"+k]=np.zeros(s.shape,"uint16")
    a["CF_RAW_DBZH"]=np.asarray(z,"float32").copy()
    a.update(empty_context(s.shape));a.update(empty_background(s.shape,cfg))
    if cfg.near_revision is not None:
        from .near_joint import empty
        a.update(empty(s, strong=cfg.near_revision.strong_near is not None))
    a.update(decide(a,cfg))
    return FusionEvidence(a,{"status":"RESOURCE_LIMIT_ABSTAINED","reason":reason,"candidate_gates":0})


def evaluate_volume(sweeps,cfg,*,backgrounds=None,protections=None,metadata=None,near_context=None):
    if not sweeps or len({s.name for s in sweeps})!=len(sweeps):raise ValueError("unique nonempty sweep list required")
    backgrounds=backgrounds or [(empty_background(s.shape,cfg),{"status":"NO_ASSET_BOUND"}) for s in sweeps]
    protections=protections or [(np.zeros(s.shape,bool),)*3 for s in sweeps]
    if len(backgrounds)!=len(sweeps) or len(protections)!=len(sweeps):raise ValueError("fusion input list lengths differ")
    before=[s.digest for s in sweeps]
    try:
        if sum(int(np.prod(s.shape)) for s in sweeps)>cfg.maximum_volume_gates:
            raise ResourceLimit("clutter fusion whole-volume budget")
        results=[]
        for i,s in enumerate(sweeps):
            f=extract(s,cfg);a=f.arrays
            a.update(derive(i,sweeps,cfg,metadata))
            bg,rec=backgrounds[i];a.update({k:np.array(v,copy=True) for k,v in bg.items()})
            for name,value in zip(("HARD_WEATHER","LOCAL_WEATHER","LEGACY_PROTECTED"),protections[i]):
                mask=np.asarray(value)
                if mask.shape!=s.shape or not np.isin(mask,(0,1)).all():raise ValueError("invalid fusion protection")
                a["CF_"+name+"_MASK"]=(mask.astype(bool)&(a["CF_OBSERVED_MASK"]==1)).astype("uint8")
            a["CF_RAW_DBZH"]=np.asarray(s.fields["DBZH"],"float32").copy()
            # New evidence is prepared for all sweeps before any new action.
            if cfg.near_revision is not None:
                from .near_joint import empty
                a.update(empty(s, strong=cfg.near_revision.strong_near is not None))
            a.update(decide(a,cfg))
            summary={"status":"EVALUATED","features":f.summary,"background":rec,
                "candidate_gates":int(a["CF_NONMET_SUPPORTED_MASK"].sum()),
                "quarantine_eligible_gates":int(a["CF_QUARANTINE_SUPPORTED_MASK"].sum()),
                "mixed_gates":int(a["CF_MIXED_MASK"].sum()),
                "paired_doppler_measured":int(a["CF_DOPPLER_PAIRED_MASK"].sum()),
                "paired_doppler_action_available":int(((a["CF_DOPPLER_PAIRED_MASK"]==1)&(a["CF_DOPPLER_ACTION_AVAILABLE_MASK"]==1)).sum()),
                "upper_measured":int(a["CF_UPPER_MEASURED_MASK"].sum()),
                "upper_action_available":int(a["CF_UPPER_ACTION_AVAILABLE_MASK"].sum()),
                "scores_are_probabilities":False,"confirmed_gates":0,"filled_gates":0}
            results.append(FusionEvidence(a,summary))
    except ResourceLimit as exc:
        results=[zero_evidence(s,cfg,str(exc)) for s in sweeps]
    if cfg.near_revision is not None and not any(x.summary["status"]=="RESOURCE_LIMIT_ABSTAINED" for x in results):
        from .near_joint import evidence,empty
        try:
            additions=[evidence(s,cfg,x.arrays,near_context) for s,x in zip(sweeps,results,strict=True)]
        except ResourceLimit as exc:
            # Preserve the old CF decision across the entire volume; only the
            # opt-in addition abstains when its own resource budget is exceeded.
            additions=[(empty(s, strong=cfg.near_revision.strong_near is not None),
                        {"status":"RESOURCE_LIMIT_ABSTAINED","reason":str(exc)}) for s in sweeps]
        for x,(arrays,detail) in zip(results,additions,strict=True):
            x.arrays.update(arrays);x.arrays.update(decide(x.arrays,cfg))
            x.summary["near_revision"]=detail
            x.summary["near_candidate_gates"]=int(x.arrays["CF_NR_ACTION_MASK"].sum())
            x.summary["near_dem_severe_gates"]=int(x.arrays["CF_NR_DEM_ACTION_MASK"].sum())
            if cfg.near_revision.strong_near is not None:
                x.summary["strong_near_candidate_gates"]=int(
                    x.arrays["CF_NR_STRONG_CANDIDATE_MASK"].sum())
                x.summary["strong_near_quarantine_gates"]=int(
                    x.arrays["CF_NR_STRONG_ACTION_MASK"].sum())
    if before != [s.digest for s in sweeps]:raise RuntimeError("fusion mutated original measurement")
    return results
