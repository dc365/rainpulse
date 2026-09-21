"""Near revision evidence/decision within the existing CF disposition owner."""
from enum import IntEnum,IntFlag
import numpy as np
from scipy.ndimage import binary_dilation,label
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
    STRONG_DILATED=4096


def _strong_empty(shape):
    return {
        "CF_NR_STRONG_CANDIDATE_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_CORE_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_OBJECT_PROPAGATED_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_DILATED_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_DILATION_DOMAIN_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_SAFE_ROW_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_ROW_ID": np.broadcast_to(
            np.arange(shape[0], dtype="int32")[:, None], shape).copy(),
        "CF_NR_STRONG_LEFT_NEIGHBOR_ROW": np.broadcast_to(
            np.full(shape[0], -1, "int32")[:, None], shape).copy(),
        "CF_NR_STRONG_RIGHT_NEIGHBOR_ROW": np.broadcast_to(
            np.full(shape[0], -1, "int32")[:, None], shape).copy(),
        "CF_NR_STRONG_ACTION_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_PROTECTED_MASK": np.zeros(shape, "uint8"),
        "CF_NR_STRONG_STATE": np.full(shape, State.STRONG_NEAR_INSUFFICIENT, "uint8"),
        "CF_NR_STRONG_REASON": np.zeros(shape, "uint16"),
        "CF_NR_STRONG_OBJECT_ID": np.zeros(shape, "int32"),
        "CF_NR_STRONG_OBJECT_SIZE": np.zeros(shape, "uint32"),
        "CF_NR_STRONG_OBJECT_SEED_FRACTION": np.full(shape, np.nan, "float32"),
        "CF_NR_STRONG_RHOHV": np.full(shape, np.nan, "float32"),
        "CF_NR_STRONG_SNR_DB": np.full(shape, np.nan, "float32"),
        "CF_NR_STRONG_RANGE_M": np.full(shape, np.nan, "float32"),
    }


def _physical_neighbor_rows(s):
    rows=np.arange(len(s.azimuth),dtype="int32")
    right=np.roll(rows,-1);left=np.roll(rows,1)
    delta=(np.roll(s.azimuth,-1)-s.azimuth)%360
    edges=s.gap_after|(delta<=0)|(delta>2)
    safe=s.good&~edges&~np.roll(edges,1)
    # The explicit loops make the wrap boundary and unavailable rows explicit.
    right=np.roll(rows,-1);left=np.roll(rows,1)
    for value in range(len(rows)):
        if not safe[value]:
            right[value]=-1;left[value]=-1;continue
        nxt=int(right[value]);prv=int(left[value])
        if nxt>=0 and not safe[nxt]:right[value]=-1
        if prv>=0 and not safe[prv]:left[value]=-1
    return left,right,safe


def _neighbor_dilation(core,mask,left,right,gates,iterations):
    current=np.asarray(core,dtype=bool).copy()
    structure=np.ones((1,gates),dtype=bool)
    for _ in range(int(iterations)):
        source=current.copy()
        valid_left=left>=0;valid_right=right>=0
        source[valid_left]|=current[left[valid_left]]
        source[valid_right]|=current[right[valid_right]]
        current=binary_dilation(source,structure=structure,mask=mask)
    return current


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
    barriers=((base["CF_HARD_WEATHER_MASK"]==1)|(base["CF_LOCAL_WEATHER_MASK"]==1)|
              (base["CF_LEGACY_PROTECTED_MASK"]==1)|(base["CF_WEATHER_PROXY_MASK"]==1)|
              (base["CF_MIXED_MASK"]==1))
    protected=bounds & barriers
    core=features & ~protected
    propagated=np.zeros(shape,bool)
    dilated=np.zeros(shape,bool);dilation_domain=np.zeros(shape,bool)
    safe_rows=np.zeros(shape[0],bool)
    left_rows=np.full(shape[0],-1,"int32");right_rows=np.full(shape[0],-1,"int32")
    row_ids=np.arange(shape[0],dtype="int32")
    left_serial=np.full(shape[0],-1,"int32");right_serial=np.full(shape[0],-1,"int32")
    object_id=np.zeros(shape,"int32");object_size=np.zeros(shape,"uint32")
    object_fraction=np.full(shape,np.nan,"float32")
    if c.object_propagation or c.object_dilation_iterations:
        left_rows,right_rows,safe_rows=_physical_neighbor_rows(s)
        row_ids=(np.arange(shape[0],dtype="int32") if s.original_indices is None
                 else np.asarray(s.original_indices,dtype="int32"))
        left_serial=np.full(shape[0],-1,"int32");right_serial=np.full(shape[0],-1,"int32")
        left_valid=left_rows>=0;right_valid=right_rows>=0
        left_serial[left_valid]=row_ids[left_rows[left_valid]]
        right_serial[right_valid]=row_ids[right_rows[right_valid]]
        object_domain=(observed&safe_rows[:,None]&ar&az
                       &np.isfinite(z)&(z>=c.minimum_object_dbz)&(z<=c.maximum_object_dbz)
                       &(s.ranges[None,:] <=c.maximum_range_m)&~barriers)
        dilation_domain=object_domain
        labels,count=label(object_domain,structure=np.ones((3,3),dtype=np.uint8))
        if count>c.maximum_strong_objects:
            from ..data import ResourceLimit
            raise ResourceLimit("strong near object count budget")
        for value in range(1,int(count)+1):
            component=labels==value;size=int(component.sum());seeds=int((component&core).sum())
            fraction=seeds/size if size else 0.
            object_id[component]=value;object_size[component]=size;object_fraction[component]=fraction
            if (c.object_propagation
                    and seeds>=c.minimum_object_seed_gates
                    and fraction>=c.minimum_object_seed_fraction
                    and size<=c.maximum_object_gates):
                propagated|=component&~core
        if c.object_dilation_iterations:
            dilated=_neighbor_dilation(core,object_domain,left_rows,right_rows,
                                       c.object_dilation_gates,c.object_dilation_iterations)
            dilated &= object_domain
            dilated &= ~(core|propagated)
    candidate=core|propagated|dilated
    state=np.full(shape,State.STRONG_NEAR_INSUFFICIENT,"uint8")
    state[~observed]=State.MISSING
    state[observed & ~bounds]=State.OUTSIDE_DOMAIN
    state[protected]=State.STRONG_NEAR_PROTECTED
    state[candidate]=State.STRONG_NEAR_CANDIDATE
    reason=np.zeros(shape,"uint16")
    for mask,bit in ((features,Reason.STRONG_LOW_RHOHV),(features,Reason.STRONG_TEXTURE_SECOND_FAMILY),
                     (protected,Reason.STRONG_WEATHER_OR_MIXED),(observed&~bounds,Reason.STRONG_OUTSIDE_BOUND),
                     (bounds&~features,Reason.FEATURE_UNAVAILABLE),(dilated,Reason.STRONG_DILATED)):
        reason[mask & observed]|=int(bit)
    return {
        "CF_NR_STRONG_CANDIDATE_MASK":candidate.astype("uint8"),
        "CF_NR_STRONG_CORE_MASK":core.astype("uint8"),
        "CF_NR_STRONG_OBJECT_PROPAGATED_MASK":propagated.astype("uint8"),
        "CF_NR_STRONG_DILATED_MASK":dilated.astype("uint8"),
        "CF_NR_STRONG_DILATION_DOMAIN_MASK":dilation_domain.astype("uint8"),
        "CF_NR_STRONG_SAFE_ROW_MASK":np.broadcast_to(
            safe_rows[:,None],shape).astype("uint8") & observed,
        "CF_NR_STRONG_ROW_ID":np.broadcast_to(row_ids[:,None],shape).copy(),
        "CF_NR_STRONG_LEFT_NEIGHBOR_ROW":np.broadcast_to(left_serial[:,None],shape).copy(),
        "CF_NR_STRONG_RIGHT_NEIGHBOR_ROW":np.broadcast_to(right_serial[:,None],shape).copy(),
        "CF_NR_STRONG_ACTION_MASK":(candidate & (c.mode=="quarantine")).astype("uint8"),
        "CF_NR_STRONG_PROTECTED_MASK":protected.astype("uint8"),
        "CF_NR_STRONG_STATE":state,
        "CF_NR_STRONG_REASON":reason,
        "CF_NR_STRONG_OBJECT_ID":object_id,
        "CF_NR_STRONG_OBJECT_SIZE":object_size,
        "CF_NR_STRONG_OBJECT_SEED_FRACTION":object_fraction,
        "CF_NR_STRONG_RHOHV":np.where(observed,np.asarray(rho,dtype="float32"),np.nan).astype("float32"),
        "CF_NR_STRONG_SNR_DB":np.where(observed,np.asarray(snr,dtype="float32"),np.nan).astype("float32"),
        "CF_NR_STRONG_RANGE_M":np.broadcast_to(s.ranges,shape).astype("float32"),
        "summary":{"candidate_gates":int(candidate.sum()),
                   "protected_gates":int(protected.sum()),
                   "mode":c.mode,"propagated_gates":int(propagated.sum()),
                   "dilated_gates":int(dilated.sum()),
                   "weather_protection_retained":True,
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
        core=a['CF_NR_STRONG_CORE_MASK']==1
        propagated=a['CF_NR_STRONG_OBJECT_PROPAGATED_MASK']==1
        dilated=a['CF_NR_STRONG_DILATED_MASK']==1
        dilation_domain=a['CF_NR_STRONG_DILATION_DOMAIN_MASK']==1
        protected=a['CF_NR_STRONG_PROTECTED_MASK']==1
        barriers=((a['CF_HARD_WEATHER_MASK']==1)|(a['CF_LOCAL_WEATHER_MASK']==1)|
                  (a['CF_LEGACY_PROTECTED_MASK']==1)|(a['CF_WEATHER_PROXY_MASK']==1)|
                  (a['CF_MIXED_MASK']==1))
        action=a['CF_NR_STRONG_ACTION_MASK']==1
        if np.any((candidate|core|propagated|dilated|dilation_domain)&barriers):
            raise ValueError('strong near candidate crossed a protection barrier')
        if not np.array_equal(action,candidate&(strong.mode=='quarantine')):
            raise ValueError('strong near action differs from audited measured support')
        feature=(obs&(a['CF_STRONG_MASK']==1)&(a['CF_POLAR_AVAILABLE_MASK']==1)
                 &np.isfinite(a['CF_RAW_DBZH'])&(a['CF_RAW_DBZH']>=strong.minimum_dbz)
                 &(a['CF_RAW_DBZH']<=strong.maximum_dbz)&np.isfinite(a['CF_NR_STRONG_RHOHV'])
                 &(a['CF_NR_STRONG_RHOHV']<=strong.maximum_rhohv)
                 &np.isfinite(a['CF_NR_STRONG_SNR_DB'])&(a['CF_NR_STRONG_SNR_DB']>=strong.minimum_snr_db)
                 &np.isfinite(a['CF_NR_STRONG_RANGE_M'])&(a['CF_NR_STRONG_RANGE_M']<=strong.maximum_range_m)
                 &np.isfinite(a['CF_TEXTURE_SCORE'])&(a['CF_TEXTURE_SCORE']>=strong.minimum_texture_score)
                 &(a['CF_FAMILY_COUNT']>=strong.minimum_family_count)&~barriers)
        if not np.array_equal(core,feature):raise ValueError('strong near core lacks measured two-family support')
        if (np.any(core&(propagated|dilated)) or np.any(propagated&dilated)
                or not np.array_equal(candidate,core|propagated|dilated)):
            raise ValueError('strong near object propagation identity differs')
        oid=a['CF_NR_STRONG_OBJECT_ID'];size=a['CF_NR_STRONG_OBJECT_SIZE']
        fraction=a['CF_NR_STRONG_OBJECT_SEED_FRACTION']
        if np.any((oid==0)&((size!=0)|np.isfinite(fraction))) or np.any((oid!=0)&((size==0)|~np.isfinite(fraction))):
            raise ValueError('strong near object bookkeeping differs')
        if np.any(propagated&(oid==0)):
            raise ValueError('strong near propagation lacks an object identity')
        for value in np.unique(oid[propagated]):
            component=oid==value;members=int(component.sum());seeds=int((component&core).sum())
            if members!=int(size[component][0]) or not np.allclose(fraction[component],seeds/members):
                raise ValueError('strong near object size or seed fraction differs')
            if members>strong.maximum_object_gates or seeds<strong.minimum_object_seed_gates or seeds/members<strong.minimum_object_seed_fraction:
                raise ValueError('strong near object lacks sufficient bounded seed support')
        if strong.object_propagation or strong.object_dilation_iterations:
            expected_domain=(obs&(a['CF_NR_STRONG_SAFE_ROW_MASK']==1)
                &np.isfinite(a['CF_RAW_DBZH'])
                &np.isfinite(a['CF_NR_STRONG_RHOHV'])
                &(a['CF_RAW_DBZH']>=strong.minimum_object_dbz)
                &(a['CF_RAW_DBZH']<=strong.maximum_object_dbz)
                &np.isfinite(a['CF_NR_STRONG_RANGE_M'])
                &(a['CF_NR_STRONG_RANGE_M']<=strong.maximum_range_m)&~barriers)
            if np.any(dilation_domain&~expected_domain):
                raise ValueError('strong near dilation domain differs from measured safe support')
            if np.any((core|propagated|dilated)&~dilation_domain):
                raise ValueError('strong near candidate leaves its measured safe domain')
        if strong.object_dilation_iterations:
            row_id=a['CF_NR_STRONG_ROW_ID'][:,0]
            left=a['CF_NR_STRONG_LEFT_NEIGHBOR_ROW'][:,0]
            right=a['CF_NR_STRONG_RIGHT_NEIGHBOR_ROW'][:,0]
            if not np.array_equal(row_id,np.arange(len(row_id))):
                raise ValueError('strong near serialized row identity differs')
            valid=(left>=0)&(left<len(left))&(right>=0)&(right<len(right))
            if np.any(valid&((left==right)|(left==row_id)|(right==row_id))):
                raise ValueError('strong near physical neighbor identity differs')
            expected=_neighbor_dilation(core,dilation_domain,left,right,
                strong.object_dilation_gates,strong.object_dilation_iterations)&~(core|propagated)
            expected &= dilation_domain
            if not np.array_equal(dilated,expected):
                raise ValueError('strong near dilation differs from bounded measured support')
        elif np.any(dilated):
            raise ValueError('strong near dilation is disabled')
