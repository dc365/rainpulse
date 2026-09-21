"""Single action projection. CR-only and quantitative quarantine remain distinct."""
import numpy as np
from ..data import checked_mask
from ..disposition import DERIVED_FIELDS, derived_invalidation
from .classifier import decide

CR="REFLECTIVITY_ELIGIBLE_FOR_CR"
FIXED={CR,"CR_UNCERTAIN_MASK","CR_QUALIFICATION_REASON","QPE_ELIGIBLE_MASK","QC_ACTION","DBZH_USABLE",
       "QC_FLAGS","QUALITY_INDEX","LOW_QUALITY_MASK","QI_METEO","QI_INTERFERENCE","P2_ADMIN_PENALTY_REMOVED_MASK"}


def mutable_names(group):
    return sorted(k for k in group if not k.startswith(("CF_","RDR_","NMR_","VOR_","EBG_")) and
                  (k in FIXED or k in DERIVED_FIELDS or k.endswith("_TRUST_MASK")))


def check_evidence(e,group,cfg):
    obs=np.asarray(group["VALID_MASK"])==1;shape=obs.shape
    z=np.asarray(group["DBZH_RAW"])
    if not np.array_equal(e["CF_RAW_DBZH"],z,equal_nan=True):raise ValueError("fusion raw identity differs")
    for k,v in e.items():
        a=np.asarray(v)
        if not k.startswith("CF_") or a.shape!=shape or a.dtype.kind not in "fiu":raise ValueError("invalid fusion evidence "+k)
        if k.endswith("_MASK") and (a.dtype!=np.uint8 or not np.isin(a,(0,1)).all() or np.any((a==1)&~obs)):
            raise ValueError("fusion mask filled original missing: "+k)
    if np.any((e["CF_DOMAIN_MASK"]==1)&(~np.isfinite(z)|(z<cfg.no_rain_below_dbz)|(z>=cfg.protected_dbz))):
        raise ValueError("fusion domain crosses no-rain/strong boundary")
    for name,expected in decide(e,cfg).items():
        if name not in e or not np.array_equal(expected,e[name],equal_nan=True):raise ValueError("fusion decision differs: "+name)
    for prefix in ("UPPER","DOPPLER"):
        available=e["CF_"+prefix+"_MEASURED_MASK"]==1
        action=e["CF_"+prefix+"_ACTION_AVAILABLE_MASK"]==1
        if np.any(action&~available):raise ValueError("context action lacks measured support")
        for axis in ("SWEEP","RAY","GATE"):
            if np.any(available&(e["CF_"+prefix+"_"+axis]<0)) or np.any(~available&(e["CF_"+prefix+"_"+axis]!=-1)):
                raise ValueError("context donor identity mismatch")
        age=e["CF_"+prefix+"_AGE_S"]
        cap=cfg.maximum_context_seconds if prefix=="UPPER" else cfg.maximum_doppler_seconds
        if np.any(available&(~np.isfinite(age)|(age<0)|(age>cap))):raise ValueError("context age invalid")
    upper = e["CF_UPPER_MEASURED_MASK"] == 1
    doppler = e["CF_DOPPLER_MEASURED_MASK"] == 1
    paired = e["CF_DOPPLER_PAIRED_MASK"] == 1
    if cfg.vertical_policy == "diagnostic_only" and np.any(e["CF_UPPER_ACTION_AVAILABLE_MASK"]):
        raise ValueError("diagnostic upper context cannot drive an action")
    if cfg.paired_doppler_policy == "diagnostic_only" and np.any(paired & (e["CF_DOPPLER_ACTION_AVAILABLE_MASK"] == 1)):
        raise ValueError("unverified paired Doppler cannot drive an action")
    if np.any(paired & ~doppler):
        raise ValueError("paired Doppler is not observed")
    for prefix, mask in (("UPPER", upper), ("DOPPLER", doppler)):
        dz = e["CF_" + prefix + "_DZ_M"]
        horizontal = e["CF_" + prefix + "_HORIZONTAL_ERROR_M"]
        if np.any(mask & (~np.isfinite(dz) | ~np.isfinite(horizontal) | (horizontal < 0)
                          | (horizontal > cfg.maximum_horizontal_error_m + .01))):
            raise ValueError("context geometry error")
        if prefix == "UPPER" and np.any(mask & ((dz < cfg.minimum_vertical_delta_m - .01)
                                                | (dz > cfg.maximum_vertical_delta_m + .01))):
            raise ValueError("upper context height difference")
        if prefix == "DOPPLER" and np.any(mask & (abs(dz) > cfg.maximum_doppler_delta_m + .01)):
            raise ValueError("Doppler context height difference")
    if np.any(doppler & (~np.isfinite(e["CF_DOPPLER_V_MS"]) | ~np.isfinite(e["CF_DOPPLER_SW_MS"])
                        | (e["CF_DOPPLER_SW_MS"] < 0))):
        raise ValueError("invalid measured Doppler values")
    if np.any(upper & ~np.isfinite(e["CF_UPPER_DROP_DB"])):
        raise ValueError("upper drop requires actual finite reflectivities")
    bg=e["CF_BG_CURRENT_NONMET_MASK"]==1
    if np.any(bg&((e["CF_BG_MATCH_MASK"]!=1)|(e["CF_BG_STABLE_MASK"]!=1))):
        raise ValueError("background nonmet lacks stable comparison")


def apply(group,evidence,cfg,*,low_quality_flag):
    if any(k.startswith("CF_") for k in group):raise ValueError("fusion cannot apply twice")
    if not isinstance(low_quality_flag,(int,np.integer)) or not 0<int(low_quality_flag)<2**32:
        raise ValueError("LOW_QUALITY flag missing")
    if int(low_quality_flag) & (int(low_quality_flag)-1):
        raise ValueError("LOW_QUALITY must be a single bit")
    check_evidence(evidence,group,cfg)
    obs=checked_mask(group["VALID_MASK"],np.shape(group["DBZH_RAW"]),"valid")
    cr=checked_mask(group[CR],obs.shape,"CR");qpe=checked_mask(group["QPE_ELIGIBLE_MASK"],obs.shape,"QPE")
    trust=checked_mask(group["REFLECTIVITY_TRUST_MASK"],obs.shape,"trust")
    if np.any((cr|qpe)&(~trust|~obs)) or not np.array_equal(np.asarray(group["QC_ACTION"])==3,~obs):
        raise ValueError("invalid fusion baseline")
    enabled=cfg.mode!="audit"
    nm=evidence["CF_NONMET_SUPPORTED_MASK"]==1
    mixed=evidence["CF_MIXED_ACTION_MASK"]==1
    candidate=(nm|mixed)&enabled
    loss=cr&candidate
    quarantine=(evidence["CF_QUARANTINE_SUPPORTED_MASK"]==1)&trust&obs&(np.asarray(group["QC_ACTION"])!=2)&(cfg.mode=="quarantine")
    changing=set(mutable_names(group))
    # Share unchanged immutable raw/historical arrays instead of duplicating them.
    a={k:np.array(v,copy=True) if k in changing else v for k,v in group.items()}
    for k in mutable_names(group):a["CF_BEFORE_"+k]=a[k].copy()
    a.update({k:np.array(v,copy=True) for k,v in evidence.items()})
    a["CF_CR_WITHHELD_MASK"]=loss.astype("uint8")
    a["CF_QUARANTINE_MASK"]=quarantine.astype("uint8")
    a["CF_MIXED_CR_WITHHELD_MASK"]=(loss&mixed).astype("uint8")
    a[CR][loss]=0
    if enabled:
        a["CR_UNCERTAIN_MASK"][(evidence["CF_MIXED_MASK"]==1)|loss]=1
        a["CR_QUALIFICATION_REASON"][loss]|=np.uint16(4096)
        a["CR_QUALIFICATION_REASON"][loss]&=np.uint16(65534)
    invalid=derived_invalidation(trust,quarantine)
    a["CF_DERIVED_INVALIDATION_MASK"]=invalid.astype("uint8")
    for k in DERIVED_FIELDS:
        if k in a:a[k][invalid]=0 if k.endswith("_MASK") else np.nan
    a["QC_ACTION"][quarantine]=1;a["QC_FLAGS"][quarantine]|=np.uint32(low_quality_flag)
    for k in ("QUALITY_INDEX","QI_METEO","QI_INTERFERENCE"):
        if k in a:a[k][quarantine]=np.minimum(a[k][quarantine],cfg.quarantine_quality)
    a["LOW_QUALITY_MASK"][quarantine]=1;a["DBZH_USABLE"][quarantine]=np.nan;a["QPE_ELIGIBLE_MASK"][quarantine]=0
    for k in mutable_names(group):
        if k.endswith("_TRUST_MASK") or k=="P2_ADMIN_PENALTY_REMOVED_MASK":a[k][quarantine]=0
    qloss=qpe&quarantine;cf=float(loss.sum()/max(1,cr.sum()));qf=float(qloss.sum()/max(1,qpe.sum()))
    if np.any((a[CR]==1)&~cr) or np.any((a["QPE_ELIGIBLE_MASK"]==1)&~qpe):raise RuntimeError("fusion revived old exclusion")
    return a,{"cr_loss_gates":int(loss.sum()),"cr_loss_fraction":cf,"quarantine_gates":int(quarantine.sum()),
        "qpe_loss_gates":int(qloss.sum()),"qpe_loss_fraction":qf,"mixed_cr_loss_gates":int((loss&mixed).sum()),
        "review_required":cf>cfg.maximum_new_cr_loss_fraction or qf>cfg.maximum_new_qpe_loss_fraction,
        "budget_policy":"retain_isolation_require_review","confirmed_gates":0,"filled_gates":0,
        "operational_eligible":False}
