"""Near revision evidence/decision within the existing CF disposition owner."""
from enum import IntEnum,IntFlag
import numpy as np
from . import partial_moments,causal_temporal,terrain_admission


class State(IntEnum):
    MISSING=0
    OUTSIDE_DOMAIN=1
    CURRENT_NONMET_RISK=2
    CAUSAL_NONMET_RISK=3
    SEVERE_OBSTRUCTION=4
    WEATHER_OR_LEGACY_PROTECTED=5
    EVIDENCE_INSUFFICIENT=6
    STRONG_NEAR_PROTECTED=7
    STRONG_NEAR_INSUFFICIENT=8
    STRONG_NEAR_CANDIDATE=9


class Reason(IntFlag):
    CURRENT_PARTIAL_POLAR=1
    PAST_MEASURED_SUPPORT=2
    BACKGROUND_MATCH=4
    BACKGROUND_UNAVAILABLE=8
    LOCAL_TERRAIN_INTERSECTION=16
    SEVERE_CUMULATIVE_BLOCKAGE=32
    WEATHER_PROTECTED=64
    FEATURE_UNAVAILABLE=128
    STRONG_LOW_RHOHV=256
    STRONG_TEXTURE_SECOND_FAMILY=512
    STRONG_WEATHER_OR_MIXED=1024
    STRONG_OUTSIDE_BOUND=2048


def _strong_empty(shape):
    return {
        "CF_NR_STRONG_CANDIDATE_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_ACTION_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_PROTECTED_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_STATE": np.full(shape, State.STRONG_NEAR_INSUFFICIENT, "uint8"),
        "CF_NR_STRONG_REASON": np.zeros(shape, "uint16"),
        "CF_NR_STRONG_RHOHV": np.full(shape, np.nan, "float32"),
        "CF_NR_STRONG_SNR_DB": np.full(shape, np.nan, "float32"),
        "CF_NR_STRONG_RANGE_M": np.full(shape, np.nan, "float32"),
    }


def empty(s, strong=False):
    a=partial_moments.empty(s.shape)
    a.update(causal_temporal.empty(s.shape));a.update(terrain_admission.empty(s.shape))
    if strong:
        a.update(_strong_empty(s.shape))
    return a


def evidence(s,cfg,base,runtime=None):
    if runtime is not None and runtime.abstention_reason:
        from ..data import ResourceLimit
        raise ResourceLimit(runtime.abstention_reason)
    a=partial_moments.extract(s,cfg)
    strong=None
    if cfg.near_revision.strong_near is not None:
        strong=_strong_evidence(s,cfg,base)
    if runtime is None:
        temporal=causal_temporal.empty(s.shape);tr={'status':'NO_FROZEN_TEMPORAL_CONTEXT','sources':[]}
        terrain=terrain_admission.empty(s.shape);dr={'status':'NO_TERRAIN_CONTEXT'}
    else:
        temporal,tr=causal_temporal.derive(s,a,runtime.past,cfg,radar_id=runtime.radar_id,
            processing_id=runtime.processing_id,scan_id=runtime.scan_id)
        terrain,dr=terrain_admission.from_sampler(s,cfg,runtime.beam,runtime.terrain,runtime.dem_version)
    a.update(temporal);a.update(terrain)
    if strong is not None:
        strong_summary=strong.pop("summary")
        a.update(strong)
    else:
        strong_summary=None
    return a,{'temporal':tr,'terrain':dr,'revision_version':cfg.near_revision.version,
        'partial_polar_is_one_family':True,'new_partial_actions_are_cr_only':True,
        'strong_near':None if strong_summary is None else {**strong_summary,
            'actions_are_quarantine_only':cfg.near_revision.strong_near.mode=='quarantine'}}


def _strong_evidence(s,cfg,base):
    c=cfg.near_revision.strong_near
    shape=s.shape
    z,az=s.moment("DBZH");rho,ar=s.moment("RHOHV");snr,asr=s.moment("SNR")
    observed=base["CF_OBSERVED_MASK"]==1
    bounds=(observed & az & np.isfinite(z) & (z>=c.minimum_dbz) & (z<=c.maximum_dbz)
            & (s.ranges[None,:] <= c.maximum_range_m))
    features=(bounds & (base["CF_STRONG_MASK"]==1) & (base["CF_POLAR_AVAILABLE_MASK"]==1)
              & ar & asr & np.isfinite(rho) & (rho<=c.maximum_rhohv)
              & np.isfinite(snr) & (snr>=c.minimum_snr_db)
              & np.isfinite(base["CF_TEXTURE_SCORE"])
              & (base["CF_TEXTURE_SCORE"]>=c.minimum_texture_score)
              & (base["CF_FAMILY_COUNT"]>=c.minimum_family_count))
    protected=(bounds & ((base["CF_HARD_WEATHER_MASK"]==1)|(base["CF_LOCAL_WEATHER_MASK"]==1)|
               (base["CF_LEGACY_PROTECTED_MASK"]==1)|(base["CF_WEATHER_PROXY_MASK"]==1)|
               (base["CF_MIXED_MASK"]==1)))
    candidate=features & ~protected
    state=np.full(shape,State.STRONG_NEAR_INSUFFICIENT,"uint8")
    state[~observed]=State.MISSING
    state[observed & ~bounds]=State.OUTSIDE_DOMAIN
    state[protected]=State.STRONG_NEAR_PROTECTED
    state[candidate]=State.STRONG_NEAR_CANDIDATE
    reason=np.zeros(shape,"uint16")
    for mask,bit in ((features,Reason.STRONG_LOW_RHOHV),(features,Reason.STRONG_TEXTURE_SECOND_FAMILY),
                     (protected,Reason.STRONG_WEATHER_OR_MIXED),(observed&~bounds,Reason.STRONG_OUTSIDE_BOUND),
                     (bounds&~features,Reason.FEATURE_UNAVAILABLE)):
        reason[mask & observed]|=int(bit)
    return {
        "CF_NR_STRONG_CANDIDATE_MASK":candidate.astype("uint8"),
        "CF_NR_STRONG_ACTION_MASK":(candidate & (c.mode=="quarantine")).astype("uint8"),
        "CF_NR_STRONG_PROTECTED_MASK":protected.astype("uint8"),
        "CF_NR_STRONG_STATE":state,
        "CF_NR_STRONG_REASON":reason,
        "CF_NR_STRONG_RHOHV":np.where(observed,np.asarray(rho,dtype="float32"),np.nan).astype("float32"),
        "CF_NR_STRONG_SNR_DB":np.where(observed,np.asarray(snr,dtype="float32"),np.nan).astype("float32"),
        "CF_NR_STRONG_RANGE_M":np.broadcast_to(s.ranges,shape).astype("float32"),
        "summary":{"candidate_gates":int(candidate.sum()),
                   "protected_gates":int(protected.sum()),
                   "mode":c.mode,"weather_protection_retained":True,
                   "background_enhancement_alone_is_not_weather":True},
    }


def decision(a,cfg):
    if cfg.near_revision is None:return {}
    m=lambda k:np.asarray(a[k])==1
    obs=m('CF_OBSERVED_MASK');dom=m('CF_DOMAIN_MASK')
    c=cfg.near_revision
    protected=(m('CF_HARD_WEATHER_MASK')|m('CF_LOCAL_WEATHER_MASK')|m('CF_LEGACY_PROTECTED_MASK')|
               m('CF_WEATHER_PROXY_MASK')|m('CF_BG_ENHANCEMENT_MASK')|m('CF_STRONG_MASK'))
    current=dom&m('CF_NR_STRICT_MASK');past=dom&m('CF_NR_TEMPORAL_SUPPORT_MASK')
    nm=(current|past)&~protected
    dem=m('CF_NR_DEM_SEVERE_MASK')&m('CF_NR_DEM_ACTION_AVAILABLE_MASK')&obs
    state=np.full(obs.shape,State.EVIDENCE_INSUFFICIENT,'uint8')
    state[obs&~dom]=State.OUTSIDE_DOMAIN
    state[current&~protected]=State.CURRENT_NONMET_RISK
    state[past&~current&~protected]=State.CAUSAL_NONMET_RISK
    state[dom&protected]=State.WEATHER_OR_LEGACY_PROTECTED
    # Obstruction is a measurement reliability constraint, even when weather exists.
    state[dem]=State.SEVERE_OBSTRUCTION;state[~obs]=State.MISSING
    reason=np.zeros(obs.shape,'uint16')
    for mask,bit in ((current,Reason.CURRENT_PARTIAL_POLAR),(past,Reason.PAST_MEASURED_SUPPORT),
                    (dom&m('CF_BG_MATCH_MASK'),Reason.BACKGROUND_MATCH),
                    (dom&~m('CF_BG_AVAILABLE_MASK'),Reason.BACKGROUND_UNAVAILABLE),
                    (m('CF_NR_DEM_LOCAL_INTERCEPTION_MASK'),Reason.LOCAL_TERRAIN_INTERSECTION),
                    (dem,Reason.SEVERE_CUMULATIVE_BLOCKAGE),(dom&protected,Reason.WEATHER_PROTECTED),
                    (dom&~m('CF_NR_READY_MASK'),Reason.FEATURE_UNAVAILABLE)):
        reason[mask&obs]|=int(bit)
    result={'CF_NR_ACTION_MASK':nm.astype('uint8'),'CF_NR_PROTECTED_MASK':(protected&obs).astype('uint8'),
            'CF_NR_DEM_ACTION_MASK':dem.astype('uint8'),'CF_NR_STATE':state,'CF_NR_REASON':reason}
    if c.strong_near is not None:
        result['CF_NR_STRONG_ACTION_MASK']=((a['CF_NR_STRONG_CANDIDATE_MASK']==1)&
            (c.strong_near.mode=='quarantine')&~(a['CF_NR_STRONG_PROTECTED_MASK']==1)).astype('uint8')
    return result


def validate(a,cfg):
    if cfg.near_revision is None:return
    c=cfg.near_revision;obs=a['CF_OBSERVED_MASK']==1
    ready=a['CF_NR_READY_MASK']==1;strict=a['CF_NR_STRICT_MASK']==1;relaxed=a['CF_NR_RELAXED_MASK']==1
    if np.any(strict&~relaxed) or np.any(relaxed&~ready):raise ValueError('partial moment support nesting differs')
    for mask,limit,fraction in ((strict,c.minimum_jitter_deg,'CF_NR_STRICT_FRACTION'),
                               (relaxed,c.temporal_minimum_jitter_deg,'CF_NR_RELAXED_FRACTION')):
        if np.any(mask&((a['CF_NR_SAMPLE_COUNT']<c.minimum_samples)|(a['CF_NR_PHASE_PAIR_COUNT']<c.minimum_samples)|
                       ~np.isfinite(a['CF_NR_JITTER_DEG'])|(a['CF_NR_JITTER_DEG']<limit-1e-4)|
                       ~np.isfinite(a[fraction])|(a[fraction]<c.minimum_neighbour_fraction-1e-6))):
            raise ValueError('near candidate lacks measured support')
    measured=a['CF_NR_TEMPORAL_MEASURED_MASK']==1;support=a['CF_NR_TEMPORAL_SUPPORT_MASK']==1
    if np.any(support&(~measured|~relaxed)):raise ValueError('temporal action lacks current and previous measurement')
    for key in ('SOURCE','RAY','GATE'):
        x=a['CF_NR_TEMPORAL_'+key]
        if np.any(measured&(x<0)) or np.any(~measured&(x!=-1)):raise ValueError('invalid temporal source index')
    age=a['CF_NR_TEMPORAL_AGE_S']
    if np.any(measured&(~np.isfinite(age)|(age<=0)|(age>c.maximum_age_seconds))):raise ValueError('noncausal temporal evidence')
    for key,limit in (('DBZH_DELTA_DB',c.maximum_dbzh_change_db),('SNR_DELTA_DB',c.maximum_snr_change_db)):
        x=a['CF_NR_TEMPORAL_'+key]
        if np.any(support&(~np.isfinite(x)|(abs(x)>limit+1e-5))):raise ValueError('temporal measurement conflict')
    for key,limit in (('DZ_M',c.maximum_vertical_error_m),('HORIZONTAL_ERROR_M',c.maximum_horizontal_error_m)):
        x=a['CF_NR_TEMPORAL_'+key]
        if np.any(measured&(~np.isfinite(x)|(abs(x)>limit+.1))):raise ValueError('temporal geometry incompatible')
    av=a['CF_NR_DEM_AVAILABLE_MASK']==1;act=a['CF_NR_DEM_ACTION_AVAILABLE_MASK']==1
    if np.any(act&~av):raise ValueError('DEM action lacks comparable terrain')
    b=a['CF_NR_DEM_CBB'];p=a['CF_NR_DEM_PBB']
    if np.any(av&(~np.isfinite(b)|~np.isfinite(p)|(b<0)|(b>1)|(p<0)|(p>1)|(b+1e-6<p))):
        raise ValueError('invalid DEM blockage evidence')
    if not np.array_equal(a['CF_NR_DEM_SEVERE_MASK']==1,act&(b>c.maximum_usable_cbb)):
        raise ValueError('DEM severity differs from policy')
    if c.strong_near is not None:
        strong=c.strong_near;obs=a['CF_OBSERVED_MASK']==1
        candidate=a['CF_NR_STRONG_CANDIDATE_MASK']==1
        protected=a['CF_NR_STRONG_PROTECTED_MASK']==1
        action=a['CF_NR_STRONG_ACTION_MASK']==1
        if np.any(candidate&protected):raise ValueError('strong near candidate crossed a protection barrier')
        if not np.array_equal(action,candidate&(strong.mode=='quarantine')):
            raise ValueError('strong near action differs from audited measured support')
        feature=(obs&(a['CF_STRONG_MASK']==1)&(a['CF_POLAR_AVAILABLE_MASK']==1)
                 &np.isfinite(a['CF_RAW_DBZH'])&(a['CF_RAW_DBZH']>=strong.minimum_dbz)
                 &(a['CF_RAW_DBZH']<=strong.maximum_dbz)&np.isfinite(a['CF_NR_STRONG_RHOHV'])
                 &(a['CF_NR_STRONG_RHOHV']<=strong.maximum_rhohv)
                 &np.isfinite(a['CF_NR_STRONG_SNR_DB'])&(a['CF_NR_STRONG_SNR_DB']>=strong.minimum_snr_db)
                 &np.isfinite(a['CF_NR_STRONG_RANGE_M'])&(a['CF_NR_STRONG_RANGE_M']<=strong.maximum_range_m)
                 &np.isfinite(a['CF_TEXTURE_SCORE'])&(a['CF_TEXTURE_SCORE']>=strong.minimum_texture_score)
                 &(a['CF_FAMILY_COUNT']>=strong.minimum_family_count)&~protected)
        if not np.array_equal(candidate,feature):raise ValueError('strong near candidate lacks measured two-family support')
