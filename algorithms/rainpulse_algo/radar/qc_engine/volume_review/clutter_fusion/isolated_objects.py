"""Isolated-object qualification inside CF. Structure/weakness never acts alone.

This module deliberately does NOT use parent removal masks as structure, nor the
legacy strong-near propagation or retrospective disappearance as source evidence.
A quantitative action needs current target evidence plus independent context.
"""
from enum import IntEnum, IntFlag
import numpy as np
from ..data import ResourceLimit
from . import isolation_geometry as geometry


class State(IntEnum):
    MISSING=0
    OUTSIDE_DOMAIN=1
    NOT_SMALL=2
    SUPPORT_UNRESOLVED=3
    NOT_ISOLATED=4
    WEATHER_PROTECTED=5
    MEASUREMENT_INSUFFICIENT=6
    CR_CANDIDATE=7
    CONTEXT_SUPPORTED_CANDIDATE=8
    RESOURCE_ABSTAINED=9


class Reason(IntFlag):
    SMALL_PHYSICAL_OBJECT=1
    KNOWN_SURROUNDINGS=2
    ISOLATED_STRUCTURE=4
    CURRENT_POLAR_SUPPORT=8
    OBJECT_EVIDENCE_FRACTION=16
    STABLE_BACKGROUND=32
    CAUSAL_MEASURED_TEMPORAL=64
    VERIFIED_DOPPLER=128
    VERIFIED_VERTICAL=256
    WEATHER_OR_STRONG=512
    UNKNOWN_SUPPORT=1024
    NATIVE_RESOLUTION_OR_EDGE=2048
    RESOURCE_LIMIT=4096
    VERIFIED_OBSTRUCTION=8192


DECISION_DTYPES={"STATE":"uint8","REASON":"uint32","CONTEXT_BITS":"uint8",
    "OBJECT_EVIDENCE_FRACTION":"float32",
    **{k+"_MASK":"uint8" for k in ("CURRENT_NONMET","CONTEXT","CANDIDATE","QUARANTINE_CANDIDATE")}}
APPLIED={"CF_ISO_CR_WITHHELD_MASK", "CF_ISO_QUARANTINE_MASK"}


def empty(s):
    a=geometry.empty(s.shape)
    for k in ("RAW_SNR_DB","RAW_RHOHV"):
        a["CF_ISO_"+k]=np.full(s.shape,np.nan,"float32")
    a["CF_ISO_ABORTED_MASK"]=np.zeros(s.shape,"uint8")
    return a


def _mask(a,k,shape):
    value=a.get(k)
    if value is None:return np.zeros(shape,bool)
    x=np.asarray(value)
    if x.shape!=shape or not np.isin(x,(0,1)).all():raise ValueError("invalid isolation mask "+k)
    return x==1


def _field(a,k,shape):
    v=np.asarray(a[k],float) if k in a else np.full(shape,np.nan)
    if v.shape!=shape or np.isinf(v).any():raise ValueError("invalid isolation numeric field "+k)
    return v


def decision(a,cfg):
    c=cfg.isolated_objects
    if c is None:return {}
    shape=np.shape(a['CF_OBSERVED_MASK'])
    m=lambda k:_mask(a,k,shape)
    f=lambda k:_field(a,k,shape)
    obs=m('CF_OBSERVED_MASK');domain=m('CF_ISO_DOMAIN_MASK')
    abort=m('CF_ISO_ABORTED_MASK')
    raw_snr=f('CF_ISO_RAW_SNR_DB');raw_rho=f('CF_ISO_RAW_RHOHV')
    ready=obs&np.isfinite(raw_snr)&np.isfinite(raw_rho)&(raw_snr>=c.minimum_nonmet_snr_db)&(raw_rho>=0)&(raw_rho<=1)
    ordinary=(m('CF_POLAR_AVAILABLE_MASK')&(f('CF_POLAR_SCORE')>=c.minimum_polar_score)&
              ((f('CF_RHO_SCORE')>=c.minimum_nonmet_rho_score)|
               (m('CF_DR_AVAILABLE_MASK')&(f('CF_DR_DB')>=-15.))))
    partial=m('CF_NR_STRICT_MASK')&m('CF_NR_READY_MASK')
    current=ready&(ordinary|partial)&~abort
    bg=m('CF_BG_AVAILABLE_MASK')&m('CF_BG_MATCH_MASK')&m('CF_BG_STABLE_MASK')&m('CF_BG_CURRENT_NONMET_MASK')
    age=f('CF_NR_TEMPORAL_AGE_S')
    temporal=(m('CF_NR_TEMPORAL_SUPPORT_MASK')&m('CF_NR_TEMPORAL_MEASURED_MASK')&
              np.isfinite(age)&(age>0)&(age<=900.) &
              (f('CF_NR_TEMPORAL_SOURCE')>=0)&(f('CF_NR_TEMPORAL_RAY')>=0)&(f('CF_NR_TEMPORAL_GATE')>=0))
    if cfg.near_revision is None:temporal[:]=False
    else:temporal &= age<=cfg.near_revision.maximum_age_seconds
    dop=(m('CF_DOPPLER_ACTION_AVAILABLE_MASK')&m('CF_DOPPLER_MEASURED_MASK')&
         (abs(f('CF_DOPPLER_V_MS'))<=.5)&(f('CF_DOPPLER_SW_MS')>=0)&(f('CF_DOPPLER_SW_MS')<=1.))
    vert=(m('CF_UPPER_ACTION_AVAILABLE_MASK')&m('CF_UPPER_MEASURED_MASK')&(f('CF_UPPER_DROP_DB')>=8.))
    bits=np.zeros(shape,'uint8')
    for flag,bit in ((bg,1),(temporal,2),(dop,4),(vert,8)):bits[flag&obs]|=bit
    ids=np.asarray(a['CF_ISO_OBJECT_ID'])
    if ids.shape!=shape or ids.dtype!=np.dtype('uint32'):raise ValueError('invalid original object identity')
    max_id=int(ids.max())
    if max_id>c.maximum_objects:raise ValueError('isolation object identity exceeds frozen budget')
    cnt=np.bincount(ids.ravel(),minlength=max_id+1)
    num=np.bincount(ids.ravel(),weights=current.ravel(),minlength=max_id+1)
    frac=np.divide(num,cnt,out=np.zeros_like(num),where=cnt>0);frac[0]=0
    fraction=frac[ids].astype('float32')
    protected=m('CF_ISO_OBJECT_PROTECTED_MASK')|m('CF_ISO_WEATHER_NEARBY_MASK')
    for k in ('CF_HARD_WEATHER_MASK','CF_LOCAL_WEATHER_MASK','CF_LEGACY_PROTECTED_MASK',
              'CF_WEATHER_PROXY_MASK','CF_BG_ENHANCEMENT_MASK','CF_STRONG_MASK','CF_MIXED_MASK'):
        protected |= m(k)
    geometry_ok=m('CF_ISO_ISOLATED_MASK')&m('CF_ISO_SMALL_MASK')&m('CF_ISO_RING_AVAILABLE_MASK')&~m('CF_ISO_GEOMETRY_LIMITED_MASK')&~m('CF_ISO_OBSTRUCTION_NEARBY_MASK')
    supported=domain&geometry_ok&current&(fraction>=c.minimum_object_evidence_fraction)&~protected&~abort
    quarantine=supported&(bits>0)
    state=np.full(shape,State.NOT_SMALL,'uint8')
    state[m('CF_ISO_SMALL_MASK')]=State.NOT_ISOLATED
    state[m('CF_ISO_SMALL_MASK')&(~m('CF_ISO_RING_AVAILABLE_MASK')|m('CF_ISO_GEOMETRY_LIMITED_MASK'))]=State.SUPPORT_UNRESOLVED
    state[domain&geometry_ok]=State.MEASUREMENT_INSUFFICIENT
    state[domain&protected]=State.WEATHER_PROTECTED
    state[supported]=State.CR_CANDIDATE;state[quarantine]=State.CONTEXT_SUPPORTED_CANDIDATE
    state[~domain]=State.OUTSIDE_DOMAIN;state[abort]=State.RESOURCE_ABSTAINED;state[~obs]=State.MISSING
    reason=np.zeros(shape,'uint32')
    for mask,bit in ((m('CF_ISO_SMALL_MASK'),Reason.SMALL_PHYSICAL_OBJECT),
        (m('CF_ISO_RING_AVAILABLE_MASK'),Reason.KNOWN_SURROUNDINGS),(geometry_ok,Reason.ISOLATED_STRUCTURE),
        (current,Reason.CURRENT_POLAR_SUPPORT),(fraction>=c.minimum_object_evidence_fraction,Reason.OBJECT_EVIDENCE_FRACTION),
        (bg,Reason.STABLE_BACKGROUND),(temporal,Reason.CAUSAL_MEASURED_TEMPORAL),
        (dop,Reason.VERIFIED_DOPPLER),(vert,Reason.VERIFIED_VERTICAL),(protected,Reason.WEATHER_OR_STRONG),
        (m('CF_ISO_SMALL_MASK')&~m('CF_ISO_RING_AVAILABLE_MASK'),Reason.UNKNOWN_SUPPORT),
        (m('CF_ISO_GEOMETRY_LIMITED_MASK'),Reason.NATIVE_RESOLUTION_OR_EDGE),
        (m('CF_ISO_OBSTRUCTION_NEARBY_MASK'),Reason.VERIFIED_OBSTRUCTION),(abort,Reason.RESOURCE_LIMIT)):
        reason[mask&obs]|=int(bit)
    values={'STATE':state,'REASON':reason,'CONTEXT_BITS':bits,'OBJECT_EVIDENCE_FRACTION':fraction,
            'CURRENT_NONMET_MASK':current,'CONTEXT_MASK':(bits>0)&obs,
            'CANDIDATE_MASK':supported,'QUARANTINE_CANDIDATE_MASK':quarantine}
    return {'CF_ISO_'+k:np.asarray(v,dtype=DECISION_DTYPES[k]) for k,v in values.items()}


def attach_volume(sweeps,results,cfg):
    """One raw pass per sweep. Optional failure withdraws this WHOLE volume only."""
    c=cfg.isolated_objects
    if c is None:return
    budget=[0]
    try:
        if any(r.summary.get('status')=='RESOURCE_LIMIT_ABSTAINED' for r in results):
            raise ResourceLimit('parent evidence is unavailable')
        additions=[]
        for s,r in zip(sweeps,results,strict=True):
            g=geometry.inspect(s,cfg,r.arrays,budget=budget)
            a=g.arrays
            for key,moment in (('RAW_SNR_DB','SNR'),('RAW_RHOHV','RHOHV')):
                v,ok=s.moment(moment)
                a['CF_ISO_'+key]=np.where(ok&s.observed,v,np.nan).astype('float32')
            a['CF_ISO_ABORTED_MASK']=np.zeros(s.shape,'uint8')
            additions.append((a,g.summary))
    except ResourceLimit as exc:
        additions=[]
        for s in sweeps:
            a=empty(s);a['CF_ISO_ABORTED_MASK']=s.observed.astype('uint8')
            additions.append((a,{'status':'RESOURCE_LIMIT_ABSTAINED','reason':str(exc),
                                  'candidate_gates':0,'raw_sha256':s.digest}))
    for r,(a,summary) in zip(results,additions,strict=True):
        r.arrays.update(a);r.arrays.update(decision(r.arrays,cfg))
        summary=dict(summary,candidate_gates=int(r.arrays['CF_ISO_CANDIDATE_MASK'].sum()),
                     quantitative_candidate_gates=int(r.arrays['CF_ISO_QUARANTINE_CANDIDATE_MASK'].sum()))
        r.summary['isolated_objects']=summary


def validate(a,cfg):
    c=cfg.isolated_objects
    if c is None:return
    shape=np.shape(a['CF_OBSERVED_MASK'])
    required=set(empty(type('Shape',(),{'shape':shape})()))|{'CF_ISO_'+k for k in DECISION_DTYPES}
    if not required.issubset(a):raise ValueError('isolation evidence incomplete: '+str(sorted(required-set(a))))
    for k,expected in decision(a,cfg).items():
        if np.asarray(a[k]).dtype!=expected.dtype or not np.array_equal(a[k],expected,equal_nan=True):
            raise ValueError('isolation decision differs: '+k)
    m=lambda k:_mask(a,k,shape)
    small=m('CF_ISO_SMALL_MASK');available=m('CF_ISO_RING_AVAILABLE_MASK');isolated=m('CF_ISO_ISOLATED_MASK')
    z=_field(a,'CF_RAW_DBZH',shape)
    domain=m('CF_ISO_DOMAIN_MASK')
    if np.any(domain & (~m('CF_OBSERVED_MASK')|~np.isfinite(z)|(z<c.echo_threshold_dbz)|(z>=cfg.protected_dbz))):
        raise ValueError('isolation domain crosses raw reflectivity/protection bounds')
    for k in ('CF_ISO_OBJECT_ID','CF_ISO_NATIVE_GATE_COUNT','CF_ISO_RING_UNIQUE_GATES'):
        if np.asarray(a[k]).dtype!=np.dtype('uint32'):
            raise ValueError('invalid isolation count dtype')
    if np.any(available&(~small|m('CF_ISO_GEOMETRY_LIMITED_MASK'))):raise ValueError('isolation support/domain mismatch')
    if np.any(isolated&(~available|m('CF_ISO_OBJECT_PROTECTED_MASK')|m('CF_ISO_WEATHER_NEARBY_MASK')|m('CF_ISO_OBSTRUCTION_NEARBY_MASK'))):
        raise ValueError('unprotected measured isolation required')
    for k,lower,upper in (('KNOWN_FRACTION',c.minimum_known_fraction,1.),
                         ('QUADRANT_KNOWN_MIN',c.minimum_quadrant_known_fraction,1.)):
        v=_field(a,'CF_ISO_'+k,shape)
        if np.any(available&(~np.isfinite(v)|(v<lower-1e-6)|(v>upper+1e-6))):raise ValueError('unmeasured surroundings')
    v=_field(a,'CF_ISO_POSSIBLE_ECHO_FRACTION',shape)
    if np.any(isolated&(~np.isfinite(v)|(v<0)|(v>c.maximum_possible_echo_fraction+1e-6))):raise ValueError('isolation echo upper bound')
    if np.any(available&(a['CF_ISO_RING_UNIQUE_GATES']<c.minimum_unique_ring_gates)):raise ValueError('duplicated supports are not samples')
    for k,upper in (('AREA_KM2',c.maximum_area_km2),('DIAMETER_M',c.maximum_diameter_m)):
        v=_field(a,'CF_ISO_'+k,shape)
        if np.any(small&(~np.isfinite(v)|(v<=0)|(v>upper+1e-4))):raise ValueError('unbounded isolated object')
    ids=np.asarray(a['CF_ISO_OBJECT_ID']);cnt=np.bincount(ids.ravel(),minlength=int(ids.max())+1)
    if np.any((ids>0)&(a['CF_ISO_NATIVE_GATE_COUNT']!=cnt[ids])):raise ValueError('original object count mismatch')
    # Every member inherits only an object PROTECTION, never a deletion.
    for key in ('CF_ISO_OBJECT_PROTECTED_MASK','CF_ISO_WEATHER_NEARBY_MASK'):
        member=m(key);bad_ids=np.unique(ids[member]);bad_ids=bad_ids[bad_ids>0]
        if np.any(np.isin(ids,bad_ids)&~member):raise ValueError('partial object protection')
