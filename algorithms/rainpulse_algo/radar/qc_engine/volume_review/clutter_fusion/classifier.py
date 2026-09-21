"""One typed decision, not an OR of independently deleting image filters.

Ground/biological names describe feature patterns, never verified object species
or clutter causes. Scores and family counts are not calibrated probabilities.
"""
from enum import IntEnum, IntFlag
import numpy as np
from .features import ramp


class EchoClass(IntEnum):
    MISSING=0
    WEATHER_COMPATIBLE=1
    GROUND_LIKE=2
    BIOLOGICAL_LIKE=3
    NONMET_UNTYPED=4
    MIXED=5
    INSUFFICIENT=6
    BACKGROUND_COMPATIBLE=7
    OUTSIDE_DOMAIN=8


class Family(IntFlag):
    POLARIZATION=1
    REFLECTIVITY_TEXTURE=2
    DOPPLER=4
    VERTICAL_MEASURED=8
    BACKGROUND=16


class Reason(IntFlag):
    POLAR_STRUCTURE=1
    POLAR_VERTICAL=2
    GROUND_PATTERN=4
    BIOLOGICAL_PATTERN=8
    BACKGROUND_CURRENT_NONMET=16
    HARD_WEATHER=32
    LEGACY_PROTECTION=64
    LOCAL_WEATHER=128
    MIXED=256
    BG_ENHANCEMENT=512
    PARTIAL_INPUT=1024
    NONMET_SUPPORTED=2048
    QUARANTINE_SUPPORTED=4096


DECISION_DTYPES={**{k:"uint8" for k in ("CLASS","NONMET_SUPPORTED_MASK","QUARANTINE_SUPPORTED_MASK",
    "MIXED_MASK","MIXED_ACTION_MASK","FAMILY_COUNT","GROUND_PATTERN_MASK","BIO_PATTERN_MASK",
    "LOCAL_REVIEW_MASK")},"FAMILY_BITS":"uint16","REASON":"uint16",
    "GROUND_SCORE":"float32","POLAR_STRUCTURE_SCORE":"float32","POLAR_VERTICAL_SCORE":"float32"}


def decide(a,cfg):
    def m(k):return np.asarray(a["CF_"+k+"_MASK"])==1
    def f(k):return np.nan_to_num(np.asarray(a["CF_"+k]),nan=0.,posinf=0.,neginf=0.)
    obs=m("OBSERVED");domain=m("DOMAIN")
    p=f("POLAR_SCORE");t=f("TEXTURE_SCORE")
    neighbour=(a["CF_POLAR_SAMPLE_COUNT"]>=cfg.minimum_samples)&(a["CF_NEIGHBOUR_FRACTION"]>=cfg.neighbour_nonmet_fraction)
    da=m("DOPPLER_ACTION_AVAILABLE")
    velocity=a["CF_DOPPLER_V_MS"];width=a["CF_DOPPLER_SW_MS"]
    dop=np.where(da,(1-ramp(abs(velocity),.2,1.))*(1-ramp(width,.5,2.)),0.)
    va=m("UPPER_ACTION_AVAILABLE")
    vert=np.where(va,ramp(a["CF_UPPER_DROP_DB"],4.,12.),0.)
    st=.55*p+.45*t;sv=.55*p+.45*vert
    structure=m("POLAR_AVAILABLE")&(p>=.65)&(t>=.4)&(st>=cfg.polar_structure_threshold)
    vertical=m("POLAR_AVAILABLE")&va&(p>=.65)&(vert>=.4)&(sv>=cfg.polar_structure_threshold)
    other=np.maximum(t,vert);gs=.5*p+.3*dop+.2*other
    ground=m("POLAR_AVAILABLE")&da&(p>=.65)&(dop>=.7)&(other>=.25)&(gs>=cfg.ground_threshold)
    bio=(m("BIO_AVAILABLE")&(f("RHO_SCORE")>=.6)&(f("BIO_ZDR_SCORE")>=.5)&
         (f("BIO_PHASE_SCORE")>=.3)&(a["CF_BIO_SCORE"]>=cfg.biological_threshold)&
         (a["CF_RAW_DBZH"]<cfg.biological_maximum_dbz))
    bg=m("BG_CURRENT_NONMET")&m("BG_MATCH")&m("BG_STABLE")
    # All polar expressions, including phase jitter, stay within one family.
    bits=np.zeros(obs.shape,"uint16");families=np.zeros(obs.shape,"uint8")
    supports=((m("POLAR_AVAILABLE")&(p>=.65),Family.POLARIZATION),
              (np.isfinite(a["CF_Z_TEXTURE_DB"])&(t>=.25),Family.REFLECTIVITY_TEXTURE),
              (da&(dop>=.7),Family.DOPPLER),(va&(vert>=.25),Family.VERTICAL_MEASURED),
              (m("BG_MATCH")&m("BG_STABLE"),Family.BACKGROUND))
    for mask,family in supports:
        bits[mask&obs]|=int(family);families+=(mask&obs).astype("uint8")
    # Episode CURRENT_NONMET may use rho without precise ZDR; no DR requirement.
    shape_ready=neighbour&(structure|vertical|ground|bio)
    supported_measurement=domain&(shape_ready|bg)
    hard=m("HARD_WEATHER");prior=m("LEGACY_PROTECTED")
    local=m("LOCAL_WEATHER")|m("WEATHER_PROXY")
    enhancement=m("BG_ENHANCEMENT")
    barred=hard|prior|m("STRONG")|enhancement
    candidate=supported_measurement&~barred&~local
    mixed=supported_measurement&(barred|local)
    # Background match alone never withholds mixed weather. It still needs
    # current nonmet evidence and at least two supported families.
    mixed_action=(mixed&local&~barred&(families>=cfg.minimum_quarantine_families)&
                  (cfg.local_conflict_policy=="cr_withhold"))
    quarantine=candidate&(families>=cfg.minimum_quarantine_families)
    code=np.full(obs.shape,EchoClass.INSUFFICIENT,"uint8")
    code[obs&~domain]=EchoClass.OUTSIDE_DOMAIN
    code[domain&m("BG_MATCH")]=EchoClass.BACKGROUND_COMPATIBLE
    code[domain&(hard|prior|local|enhancement)]=EchoClass.WEATHER_COMPATIBLE
    code[candidate]=EchoClass.NONMET_UNTYPED
    code[candidate&ground&~bio]=EchoClass.GROUND_LIKE
    code[candidate&bio&~ground]=EchoClass.BIOLOGICAL_LIKE
    code[mixed]=EchoClass.MIXED
    code[~obs]=EchoClass.MISSING
    reason=np.zeros(obs.shape,"uint16")
    for mask,bit in ((domain&neighbour&structure,Reason.POLAR_STRUCTURE),(domain&neighbour&vertical,Reason.POLAR_VERTICAL),
        (domain&neighbour&ground,Reason.GROUND_PATTERN),(domain&neighbour&bio,Reason.BIOLOGICAL_PATTERN),
        (domain&bg,Reason.BACKGROUND_CURRENT_NONMET),(hard,Reason.HARD_WEATHER),(prior,Reason.LEGACY_PROTECTION),
        (local,Reason.LOCAL_WEATHER),(mixed,Reason.MIXED),(enhancement,Reason.BG_ENHANCEMENT),
        (domain&~m("DR_AVAILABLE"),Reason.PARTIAL_INPUT),(candidate,Reason.NONMET_SUPPORTED),
        (quarantine,Reason.QUARANTINE_SUPPORTED)):
        reason[mask&obs]|=int(bit)
    result={"CLASS":code,"FAMILY_BITS":bits,"FAMILY_COUNT":families,"REASON":reason,
        "NONMET_SUPPORTED_MASK":candidate,"QUARANTINE_SUPPORTED_MASK":quarantine,"MIXED_MASK":mixed,
        "MIXED_ACTION_MASK":mixed_action,"LOCAL_REVIEW_MASK":mixed_action,
        "GROUND_PATTERN_MASK":domain&neighbour&ground,"BIO_PATTERN_MASK":domain&neighbour&bio,
        "GROUND_SCORE":np.where(obs,gs,np.nan),"POLAR_STRUCTURE_SCORE":np.where(obs,st,np.nan),
        "POLAR_VERTICAL_SCORE":np.where(obs,sv,np.nan)}
    out = {"CF_"+k:np.asarray(v,dtype=DECISION_DTYPES[k]) for k,v in result.items()}
    if cfg.near_revision is not None:
        from .near_joint import decision
        out.update(decision(a,cfg))
    return out


def protections(group,threshold):
    """Never unprotect a legacy mask whose origin cannot be attributed."""
    shape=np.shape(group["VALID_MASK"])
    hard=np.zeros(shape,bool);local=hard.copy();prior=hard.copy()
    for key in ("V7_VERTICAL_SUPPORT_SCORE","V7_CROSS_RADAR_SUPPORT_SCORE"):
        if key in group:
            v=np.asarray(group[key]);hard|=np.isfinite(v)&(v>=threshold)
    if "VOR_REASON" in group:
        v=np.asarray(group["VOR_REASON"],"uint32");hard|=(v&(16|32))!=0;local|=(v&8)!=0
    for key in ("NMR_WEATHER_PROXY_MASK","RDR_LOCAL_COHERENCE_MASK"):
        if key in group:local|=np.asarray(group[key])==1
    for key in ("RDR_INDEPENDENT_WEATHER_MASK",):
        if key in group:hard|=np.asarray(group[key])==1
    for key in ("NP_WEATHER_PROTECTED_MASK","NP_MIXED_MASK","RDR_UNKNOWN_PROTECTION_MASK"):
        if key in group:prior|=np.asarray(group[key])==1
    legacy=np.zeros(shape,bool)
    if "VOR_STATE" in group:legacy|=np.isin(group["VOR_STATE"],(3,5))
    if "NMR_PREVIOUS_WEATHER_MASK" in group:legacy|=np.asarray(group["NMR_PREVIOUS_WEATHER_MASK"])==1
    prior|=legacy&~local&~hard
    return hard,local,prior
