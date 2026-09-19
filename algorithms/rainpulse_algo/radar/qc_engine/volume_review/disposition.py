"""Single controlled disposition and a separate CR qualification contract."""
import numpy as np
from .data import checked_mask
from .geometry import runs

DERIVED_FIELDS = ("PHIDP_OS_RECONSTRUCTED", "KDP_OS", "KDP_OS_AVAILABLE_MASK",
                  "DBZH_OS_ATTENUATION_CORRECTED", "ATTENUATION_OS_AVAILABLE_MASK")

def derived_invalidation(trust, added):
    """Invalidate entire original trusted segments touched by new isolation.

    Values derived before this stage may depend on later-withheld observations.
    No reprocessing without the original phase/attenuation environment receipt.
    """
    out=np.zeros_like(added,dtype=bool)
    for row in np.flatnonzero(added.any(axis=1)):
        for lo,hi in runs(trust[row]):
            if added[row,lo:hi].any():
                out[row,lo:hi]=True
    return out


def dispose(arrays, flags, quality, observed, evidence, cfg, *, low_quality_flag):
    shape=observed.shape
    obs=checked_mask(observed,shape,"observed")
    a={k:np.array(v,copy=True) for k,v in arrays.items()}
    flags=np.array(flags,copy=True); quality=np.array(quality,copy=True)
    trusted=checked_mask(a["REFLECTIVITY_TRUST_MASK"],shape,"trust")
    eligible=checked_mask(a["QPE_ELIGIBLE_MASK"],shape,"QPE")
    action=np.asarray(a["QC_ACTION"])
    if not np.array_equal(action==3,~obs) or np.any(eligible & ~trusted):
        raise ValueError("invalid baseline action/trust")
    proposal=checked_mask(evidence["VOR_PROPOSAL_MASK"],shape,"proposal")
    if np.any(proposal & ~obs):
        raise ValueError("proposal created observation")
    added=proposal & trusted & (action!=2) & obs & (cfg.mode=="experiment_quarantine")
    for key,value in list(a.items()):
        if key in ("QC_ACTION","QPE_ELIGIBLE_MASK","DBZH_USABLE","P2_ADMIN_PENALTY_REMOVED_MASK") or key.endswith("_TRUST_MASK") or key in DERIVED_FIELDS:
            a["VOR_BEFORE_"+key]=np.array(value,copy=True)
    a.update({k:np.array(v,copy=True) for k,v in evidence.items()})
    a["VOR_BASELINE_TRUST_MASK"]=trusted.astype("uint8")
    a["VOR_BASELINE_QPE_MASK"]=eligible.astype("uint8")
    a["VOR_BASELINE_ACTION"]=action.copy()
    a["VOR_QUARANTINE_MASK"]=added.astype("uint8")
    derived=derived_invalidation(trusted,added)
    a["VOR_DERIVED_INVALIDATION_MASK"]=derived.astype("uint8")
    for key in DERIVED_FIELDS:
        if key in a:
            a[key][derived]=0 if key.endswith("_MASK") else np.nan
    a["QC_ACTION"][added]=1
    flags[added] |= np.uint32(low_quality_flag)
    quality[added]=np.minimum(quality[added],cfg.quarantine_quality)
    for key,value in a.items():
        if key.endswith("_TRUST_MASK") and not key.startswith("VOR_"):
            value[added]=0
    a["QPE_ELIGIBLE_MASK"][added]=0
    a["DBZH_USABLE"][added]=np.nan
    if "P2_ADMIN_PENALTY_REMOVED_MASK" in a:
        a["VOR_BASELINE_ADMIN_RESTORED_MASK"]=a["P2_ADMIN_PENALTY_REMOVED_MASK"].copy()
        a["P2_ADMIN_PENALTY_REMOVED_MASK"][added]=0
    unresolved=(a["VOR_UNKNOWN_MASK"]==1)
    source_supported=a["VOR_STATE"]==4
    # Distinct from QPE eligibility: measured TRUST is the starting point.
    # A low-QI but trusted meteorological reflection can enter this CR candidate;
    # old hard rejects and old quarantines can never be revived.
    cr=trusted & obs & ~source_supported & ~added
    if cfg.unknown_cr_policy=="withhold":
        cr &= ~unresolved
    a["REFLECTIVITY_ELIGIBLE_FOR_CR"]=cr.astype("uint8")
    a["CR_UNCERTAIN_MASK"]=(unresolved & obs).astype("uint8")
    a["CR_QUALIFICATION_REASON"]=np.zeros(shape,"uint16")
    for m,bit in ((cr,1),(~trusted & obs,2),(source_supported,4),(unresolved,8),(~obs,16)):
        a["CR_QUALIFICATION_REASON"][m]|=bit
    loss=eligible & added
    return a,flags,quality,{"added_quarantine_gates":int(added.sum()),"qpe_loss_gates":int(loss.sum()),
             "qpe_loss_fraction":float(loss.sum()/max(1,eligible.sum())),
             "review_required":bool(loss.sum()/max(1,eligible.sum())>cfg.maximum_new_eligible_loss_fraction),
             "cr_eligible_gates":int(cr.sum()),"cr_uncertain_gates":int((unresolved&obs).sum()),
             "budget_behavior":"retain_isolation_require_review", "operational_eligible":False}


def validate_fields(group, observed, reject=None, prior_quarantine=None):
    """Called before the legacy final trust invariant. Returns only NEW isolation."""
    def get(k):
        if k not in group:
            raise ValueError("missing volume-review field "+k)
        return np.asarray(group[k][:])
    shape=observed.shape
    required={"VOR_STATE":"uint8", "VOR_REASON":"uint16", "VOR_OBJECT_ID":"uint32",
              "VOR_CANDIDATE_MASK":"uint8", "VOR_PROPOSAL_MASK":"uint8", "VOR_QUARANTINE_MASK":"uint8",
              "VOR_BASELINE_TRUST_MASK":"uint8", "VOR_BASELINE_QPE_MASK":"uint8", "VOR_BASELINE_ACTION":"uint8",
              "VOR_WEATHER_MASK":"uint8", "VOR_UNKNOWN_MASK":"uint8", "REFLECTIVITY_ELIGIBLE_FOR_CR":"uint8",
              "CR_UNCERTAIN_MASK":"uint8", "CR_QUALIFICATION_REASON":"uint16"}
    for k,dt in required.items():
        a=get(k)
        if a.shape!=shape or a.dtype!=np.dtype(dt):
            raise ValueError("invalid volume field "+k)
        if k.endswith("_MASK") or k=="REFLECTIVITY_ELIGIBLE_FOR_CR":
            checked_mask(a,shape,k)
    obs=np.asarray(observed,bool)
    for key in group:
        if key.startswith("VOR_"):
            v=np.asarray(group[key][:])
            if v.shape!=shape:raise ValueError("volume field geometry differs: "+key)
            if key.endswith("_MASK"):
                checked_mask(v,shape,key)
                if np.any((v==1)&~obs):raise ValueError("volume mask created observations: "+key)
    source_state=(get("VOR_STATE")==4)|(get("VOR_STATE")==5)
    if source_state.any():
        for key in ("VOR_SOURCE_MATCH_MASK", "VOR_SOURCE_CORROBORATED_MASK", "VOR_CANDIDATE_MASK"):
            if np.any(source_state & (get(key)!=1)):raise ValueError("source state lacks measured evidence: "+key)
        for key in ("VOR_MODEL_ID",):
            if np.any(source_state & (get(key)==0)):raise ValueError("source state lacks reference model")
        for key in ("VOR_DONOR_SWEEP", "VOR_DONOR_RAY", "VOR_DONOR_GATE"):
            if np.any(source_state & (get(key)<0)):raise ValueError("source state lacks donor receipt")
    if np.any((get("VOR_CANDIDATE_MASK")==1)&~obs):raise ValueError("candidate created observations")
    q=get("VOR_QUARANTINE_MASK")==1; proposal=get("VOR_PROPOSAL_MASK")==1
    wx=get("VOR_WEATHER_MASK")==1; state=get("VOR_STATE")
    old=get("VOR_BASELINE_TRUST_MASK")==1
    if np.any(state>5) or not np.array_equal(state==0,~obs):
        raise ValueError("volume state changed missing semantics")
    if np.any(q & (~obs|~proposal|~old|wx|(state!=4)|(get("QC_ACTION")!=1))):
        raise ValueError("unsupported volume isolation")
    if np.any(q & ((get("QPE_ELIGIBLE_MASK")==1)|(get("REFLECTIVITY_TRUST_MASK")==1))):
        raise ValueError("volume quarantine leaked")
    if not np.array_equal(get("REFLECTIVITY_TRUST_MASK")==1,old & ~q):
        raise ValueError("unexplained trust changes")
    if not np.array_equal(get("QPE_ELIGIBLE_MASK")==1,(get("VOR_BASELINE_QPE_MASK")==1)&~q):
        raise ValueError("unexplained QPE changes")
    cr=get("REFLECTIVITY_ELIGIBLE_FOR_CR")==1
    if np.any(cr & (~obs|~old|q|(state==4))):
        raise ValueError("unsafe CR admission")
    if reject is not None and np.any(q & reject):
        raise ValueError("new quarantine overlaps legacy rejection")
    return q
