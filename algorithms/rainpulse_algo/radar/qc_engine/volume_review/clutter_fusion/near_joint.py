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


class Reason(IntFlag):
    CURRENT_PARTIAL_POLAR=1
    PAST_MEASURED_SUPPORT=2
    BACKGROUND_MATCH=4
    BACKGROUND_UNAVAILABLE=8
    LOCAL_TERRAIN_INTERSECTION=16
    SEVERE_CUMULATIVE_BLOCKAGE=32
    WEATHER_PROTECTED=64
    FEATURE_UNAVAILABLE=128


def empty(s):
    a=partial_moments.empty(s.shape)
    a.update(causal_temporal.empty(s.shape));a.update(terrain_admission.empty(s.shape))
    return a


def evidence(s,cfg,base,runtime=None):
    if runtime is not None and runtime.abstention_reason:
        from ..data import ResourceLimit
        raise ResourceLimit(runtime.abstention_reason)
    a=partial_moments.extract(s,cfg)
    if runtime is None:
        temporal=causal_temporal.empty(s.shape);tr={'status':'NO_FROZEN_TEMPORAL_CONTEXT','sources':[]}
        terrain=terrain_admission.empty(s.shape);dr={'status':'NO_TERRAIN_CONTEXT'}
    else:
        temporal,tr=causal_temporal.derive(s,a,runtime.past,cfg,radar_id=runtime.radar_id,
            processing_id=runtime.processing_id,scan_id=runtime.scan_id)
        terrain,dr=terrain_admission.from_sampler(s,cfg,runtime.beam,runtime.terrain,runtime.dem_version)
    a.update(temporal);a.update(terrain)
    return a,{'temporal':tr,'terrain':dr,'revision_version':cfg.near_revision.version,
        'partial_polar_is_one_family':True,'new_partial_actions_are_cr_only':True}


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
    return {'CF_NR_ACTION_MASK':nm.astype('uint8'),'CF_NR_PROTECTED_MASK':(protected&obs).astype('uint8'),
            'CF_NR_DEM_ACTION_MASK':dem.astype('uint8'),'CF_NR_STATE':state,'CF_NR_REASON':reason}


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
