"""Independent source/polar evidence on frozen RAW fragment families.

No recursive source promotion, no missing-as-clear-air, no phase-only action.
Thresholds for reliable polar gates reuse the existing grouped-strip policy.
"""
from enum import IntFlag
import numpy as np
from scipy.ndimage import uniform_filter1d
from ..arrays import mask, moment, native_geometry, runs
from .raw_families import PREFIX as RAW

PREFIX = 'RV2_FAMILY_JOINT_'
MASKS = ('CANDIDATE_MASK','QUALIFIED_MASK','ANCHORED_MASK','POLAR_MASK',
         'POLAR_AVAILABLE_MASK','ORIGINAL_SOURCE_MASK')
FLOATS = ('ANCHOR_DISTANCE_M','RHOHV_TEXTURE','PHIDP_CIRCULAR_VARIANCE')


class Hold(IntFlag):
    NO_SOURCE_OR_POLAR = 1
    GEOMETRY = 2
    BILATERAL = 4
    SOURCE_UNSUPPORTED = 8
    POLAR_UNAVAILABLE = 16


def _texture(native, blocked, candidates):
    """Actual native samples in 5km windows, clipped at explicit barriers."""
    _,_,dr,good,_ = native_geometry(native)
    out = {key:np.full(native.shape,np.nan,'float32') for key in ('RHOHV_TEXTURE','PHIDP_CIRCULAR_VARIANCE')}
    counts = {key:np.zeros(native.shape,'uint16') for key in ('RHOHV_SAMPLE_COUNT','PHIDP_SAMPLE_COUNT')}
    size = max(3,int(round(5000./dr))) | 1
    for moment_name,key in (('RHOHV','RHOHV_TEXTURE'),('PHIDP','PHIDP_CIRCULAR_VARIANCE')):
        values,available = moment(native,moment_name)
        available &= ~blocked & good[:,None]
        if moment_name=='RHOHV':available &= (values>=0)&(values<=1)
        for row in np.flatnonzero(candidates.any(axis=1)):
            for lo,hi in runs(~blocked[row]):
                if not candidates[row,lo:hi].any():continue
                valid=available[row,lo:hi]
                avg=lambda a:uniform_filter1d(a.astype(float),size,mode='constant')*size
                count=np.rint(avg(valid)).astype(int)
                if moment_name=='RHOHV':
                    v=np.where(valid,values[row,lo:hi],0.)
                    mean=np.divide(avg(v),count,out=np.zeros(hi-lo),where=count>0)
                    second=np.divide(avg(v*v),count,out=np.zeros(hi-lo),where=count>0)
                    stat=np.sqrt(np.maximum(0.,second-mean*mean))
                else:
                    angle=np.deg2rad(np.where(valid,values[row,lo:hi],0.))
                    cosine=np.divide(avg(np.where(valid,np.cos(angle),0.)),count,out=np.zeros(hi-lo),where=count>0)
                    sine=np.divide(avg(np.where(valid,np.sin(angle),0.)),count,out=np.zeros(hi-lo),where=count>0)
                    stat=np.clip(1.-np.hypot(cosine,sine),0.,1.)
                target=candidates[row,lo:hi]
                ck=moment_name+'_SAMPLE_COUNT'
                counts[ck][row,lo:hi][target]=np.minimum(count[target],65535)
                measured=target&(count>=8)&(count*dr>=2000.-1e-6)
                out[key][row,lo:hi][measured]=stat[measured]
    return out,counts


def qualify(native, families, blocked, original_source):
    r,_,dr,good,_=native_geometry(native)
    _,observed=moment(native,'DBZH')
    blocked=mask(blocked,native.shape,'family barriers')|~good[:,None]
    candidate=mask(families[RAW+'MASK'],native.shape,'RAW family nominations')&observed&~blocked
    source=mask(original_source,native.shape,'original independent source')&candidate
    ids=np.asarray(families[RAW+'ID'])
    snr,snr_ok=moment(native,'SNR');rho,rho_ok=moment(native,'RHOHV');zdr,zdr_ok=moment(native,'ZDR')
    reliable=candidate&snr_ok&(snr>=10.)&rho_ok&(rho>=0.)&(rho<=1.)
    polar=reliable&((rho<.7)|((rho<.85)&zdr_ok&(abs(zdr)>3.)))
    out={PREFIX+k:np.zeros(native.shape,'uint8') for k in MASKS}
    out.update({PREFIX+k:np.zeros(native.shape,'uint32') for k in ('PARENT_ID','SOURCE_SEED_ID')})
    out[PREFIX+'HOLD_REASON']=np.zeros(native.shape,'uint16')
    out[PREFIX+'ANCHOR_DISTANCE_M']=np.full(native.shape,np.nan,'float32')
    out[PREFIX+'CANDIDATE_MASK']=candidate.astype('uint8')
    out[PREFIX+'POLAR_AVAILABLE_MASK']=reliable.astype('uint8')
    out[PREFIX+'POLAR_MASK']=polar.astype('uint8')
    out[PREFIX+'ORIGINAL_SOURCE_MASK']=source.astype('uint8')
    for key,value,available in (('SNR_DB',snr,snr_ok),('RHOHV_VALUE',rho,rho_ok),('ZDR_DB',zdr,zdr_ok)):
        out[PREFIX+key]=np.where(candidate&available,value,np.nan).astype('float32')
    texture,counts=_texture(native,blocked,candidate)
    out.update({PREFIX+k:v for k,v in {**texture,**counts}.items()})
    flat=np.flatnonzero(candidate)
    flat=flat[np.argsort(ids.flat[flat],kind='stable')]
    chunks=np.split(flat,np.flatnonzero(np.diff(ids.flat[flat]))+1) if len(flat) else []
    anchored_objects=0
    for indices in chunks:
        rows,gates=np.unravel_index(indices,native.shape)
        identity=int(ids[rows[0],gates[0]])
        seeds=source[rows,gates]
        seed_gates=np.unique(gates[seeds])
        stable=not np.any(families[RAW+'HOLD_REASON'][rows,gates]&8)
        support=float(families[RAW+'SUPPORT_M'][rows[0],gates[0]])
        span=float(families[RAW+'SPAN_M'][rows[0],gates[0]])
        width=float(np.max(families[RAW+'WIDTH_M'][rows,gates]))
        geometry=stable and support>=8000. and span>=80000. and span/max(dr,width)>=12.
        bilateral=families[RAW+'WINDOW_BITS'][rows,gates]==3
        unanchored=geometry&bilateral&polar[rows,gates]
        linked=np.zeros(len(gates),bool)
        if stable and len(seed_gates)*dr>=10000.-1e-6:
            pos=np.searchsorted(seed_gates,gates)
            near=np.minimum(abs(r[gates]-r[seed_gates[np.clip(pos,0,len(seed_gates)-1)]]),
                            abs(r[gates]-r[seed_gates[np.clip(pos-1,0,len(seed_gates)-1)]]))
            linked=near<=120000.
            out[PREFIX+'SOURCE_SEED_ID'][rows[seeds],gates[seeds]]=identity
            out[PREFIX+'PARENT_ID'][rows[linked],gates[linked]]=identity
            out[PREFIX+'ANCHOR_DISTANCE_M'][rows[linked],gates[linked]]=near[linked]
            anchored_objects+=1
        accepted=linked|unanchored
        hold=np.zeros(len(gates),'uint16')
        hold[~linked&~polar[rows,gates]]|=int(Hold.NO_SOURCE_OR_POLAR)
        hold[~linked&~reliable[rows,gates]]|=int(Hold.POLAR_UNAVAILABLE)
        if not geometry:hold[~linked]|=int(Hold.GEOMETRY)
        hold[~linked&~bilateral]|=int(Hold.BILATERAL)
        if len(seed_gates):hold[~linked]|=int(Hold.SOURCE_UNSUPPORTED)
        hold[accepted]=0
        out[PREFIX+'HOLD_REASON'][rows,gates]=hold
        out[PREFIX+'ANCHORED_MASK'][rows,gates]=linked
        out[PREFIX+'QUALIFIED_MASK'][rows,gates]=accepted
    return out,{'version':'family-joint-v1','candidate_gates':int(candidate.sum()),
                'qualified_gates':int(out[PREFIX+'QUALIFIED_MASK'].sum()),
                'anchored_gates':int(out[PREFIX+'ANCHORED_MASK'].sum()),'anchored_objects':anchored_objects,
                'unanchored_polar_gates':int(((out[PREFIX+'QUALIFIED_MASK']==1)&(out[PREFIX+'ANCHORED_MASK']==0)).sum()),
                'source_claim':False,'filled_gates':0,'recursive_growth':False,
                'phase_texture_action_enabled':False}


def validate(group,observed,blocked,original_source):
    get=lambda k:np.asarray(group[PREFIX+k][:])
    raw=lambda k:np.asarray(group[RAW+k][:])
    shape=observed.shape
    masks={}
    for key in MASKS:
        value=get(key)
        if value.shape!=shape or value.dtype!=np.dtype('uint8'):raise ValueError('invalid joint mask dtype/shape')
        masks[key]=mask(value,shape,key)
    candidate=masks['CANDIDATE_MASK'];qualified=masks['QUALIFIED_MASK'];anchored=masks['ANCHORED_MASK']
    if (not np.array_equal(candidate,raw('MASK')==1) or
            np.any(candidate&(~observed|blocked)) or np.any(qualified&~candidate) or np.any(anchored&~qualified)):
        raise ValueError('joint qualification crossed original family/barrier')
    for key in ('PARENT_ID','SOURCE_SEED_ID'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint32'):raise ValueError('invalid joint lineage')
    distance=get('ANCHOR_DISTANCE_M');seed=get('SOURCE_SEED_ID');parent=get('PARENT_ID')
    if (distance.shape!=shape or distance.dtype!=np.dtype('float32') or
        not np.array_equal(np.isfinite(distance),anchored) or not np.array_equal(parent>0,anchored) or
        np.any(anchored&((distance<0)|(distance>120000.)|(parent!=raw('ID')))) or
        np.any((seed>0)&(~original_source|~candidate|(seed!=raw('ID')))) or
        not np.isin(parent[anchored],seed[seed>0]).all()):raise ValueError('joint action lacks frozen original seed')
    flat=np.flatnonzero(candidate)
    flat=flat[np.argsort(raw('ID').flat[flat],kind='stable')]
    for chunk in (np.split(flat,np.flatnonzero(np.diff(raw('ID').flat[flat]))+1) if len(flat) else []):
        rows,gates=np.unravel_index(chunk,shape)
        linked=anchored[rows,gates]
        if not linked.any():continue
        seed_gates=np.unique(gates[seed[rows,gates]>0])
        spacing=float(raw('SUPPORT_M')[rows[0],gates[0]])/len(np.unique(gates))
        if len(seed_gates)*spacing<10000.-.01 or np.any(raw('HOLD_REASON')[rows,gates]&8):
            raise ValueError('joint source support/boundary insufficient')
        pos=np.searchsorted(seed_gates,gates[linked])
        nearest=np.minimum(abs(gates[linked]-seed_gates[np.clip(pos,0,len(seed_gates)-1)]),
                           abs(gates[linked]-seed_gates[np.clip(pos-1,0,len(seed_gates)-1)]))*spacing
        if not np.allclose(distance[rows[linked],gates[linked]],nearest,rtol=1e-5,atol=.05):
            raise ValueError('joint distance differs from original source gates')
    if not np.array_equal(masks['ORIGINAL_SOURCE_MASK'],original_source&candidate):raise ValueError('joint original source mismatch')
    for key in ('SNR_DB','RHOHV_VALUE','ZDR_DB'):
        value=get(key)
        if value.shape!=shape or value.dtype!=np.dtype('float32') or np.isinf(value).any() or np.any(np.isfinite(value)&~candidate):raise ValueError('invalid joint polar measurement')
    snr,rho,zdr=get('SNR_DB'),get('RHOHV_VALUE'),get('ZDR_DB')
    reliable=candidate&np.isfinite(snr)&(snr>=10.)&np.isfinite(rho)&(rho>=0.)&(rho<=1.)
    polar=reliable&((rho<.7)|((rho<.85)&np.isfinite(zdr)&(abs(zdr)>3.)))
    if not np.array_equal(masks['POLAR_MASK'],polar) or not np.array_equal(masks['POLAR_AVAILABLE_MASK'],reliable):raise ValueError('joint polar vote differs from measurements')
    if np.any(masks['POLAR_MASK']&~masks['POLAR_AVAILABLE_MASK']) or np.any(masks['POLAR_AVAILABLE_MASK']&~candidate):raise ValueError('joint polar exceeds availability')
    independent=qualified&~anchored
    if np.any(independent&(~masks['POLAR_MASK']|(raw('WINDOW_BITS')!=3)|
                          ((raw('HOLD_REASON')&8)!=0)|(raw('SUPPORT_M')<8000.)|
                          (raw('SPAN_M')<80000.)|(raw('SPAN_M')/raw('WIDTH_M')<12.))):
        raise ValueError('unanchored joint action lacks independent multiscale evidence')
    hold=get('HOLD_REASON')
    if hold.shape!=shape or hold.dtype!=np.dtype('uint16') or np.any(qualified&(hold!=0)) or np.any(candidate&~qualified&(hold==0)):
        raise ValueError('joint hold reasons inconsistent')
    for key in ('RHOHV_TEXTURE','PHIDP_CIRCULAR_VARIANCE'):
        value=get(key)
        if value.shape!=shape or value.dtype!=np.dtype('float32') or np.isinf(value).any() or np.any(np.isfinite(value)&~candidate) or np.any(np.isfinite(value)&(value<0)) or (key=='PHIDP_CIRCULAR_VARIANCE' and np.any(value>1)):raise ValueError('invalid joint texture')
    for key in ('RHOHV_SAMPLE_COUNT','PHIDP_SAMPLE_COUNT'):
        value=get(key)
        if value.shape!=shape or value.dtype!=np.dtype('uint16') or np.any((value>0)&~candidate):raise ValueError('invalid joint sample count')
