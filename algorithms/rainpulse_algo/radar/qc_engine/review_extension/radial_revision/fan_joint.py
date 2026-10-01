"""Guarded original-source models for frozen RAW fan targets.

No RAW membership is a source vote; no target enters its reference model.
"""
import numpy as np
from ..arrays import mask, moment, native_geometry, runs
from .raw_fans import PREFIX as FAN
from .source_ledger import PREFIX as LEDGER

PREFIX='RV2_FAN_JOINT_'
TARGET_FLOATS=('RANGE_M','SPACING_M','TARGET_DBZH')
MODEL_FLOATS=('INTERCEPT_DB','RESIDUAL_DB','REFERENCE_SUPPORT_M','REFERENCE_SPAN_M',
              'REFERENCE_BLOCK_COUNT','REFERENCE_P90_DB','REFERENCE_SPLIT_DELTA_DB','ANCHOR_DISTANCE_M')


def reference_model(r,dr,z,gates,block):
    ref=gates[abs((r[gates]//20000.).astype(int)-block)>1]
    blocks=(r[ref]//20000.).astype(int);unique=np.unique(blocks)
    if len(ref)*dr<10000.-.01 or len(unique)<3 or np.ptp(r[ref])+dr<60000.-.01:
        return None
    power=z[ref]-20.*np.log10(np.maximum(r[ref],1.)/50000.)
    centre=float(np.median(power))
    medians=np.array([np.median(power[blocks==b]) for b in unique])
    return {'INTERCEPT_DB':centre,'REFERENCE_SUPPORT_M':len(ref)*dr,
            'REFERENCE_SPAN_M':float(np.ptp(r[ref])+dr),'REFERENCE_BLOCK_COUNT':len(unique),
            'REFERENCE_P90_DB':float(np.percentile(abs(power-centre),90)),
            'REFERENCE_SPLIT_DELTA_DB':float(abs(np.median(medians[::2])-np.median(medians[1::2])))}


def qualify(native,blocked,group):
    r,_,dr,good,_=native_geometry(native);z,obs=moment(native,'DBZH')
    blocked=mask(blocked,native.shape,'fan joint barriers')|~good[:,None]
    seed=np.asarray(group[LEDGER+'SEED_ID']);family=np.asarray(group[FAN+'ID'])
    candidate=(family>0)&(seed==0)&obs&~blocked
    out={PREFIX+k:np.zeros(native.shape,'uint8') for k in
         ('CANDIDATE_MASK','QUALIFIED_MASK','MODEL_AVAILABLE_MASK','ORIGINAL_SOURCE_MASK')}
    out.update({PREFIX+k:np.zeros(native.shape,'uint32') for k in ('SOURCE_ID','MODEL_SOURCE_ID','RAW_PARENT_ID')})
    out[PREFIX+'HOLD_REASON']=np.zeros(native.shape,'uint16')
    out.update({PREFIX+k:np.full(native.shape,np.nan,'float32') for k in (*TARGET_FLOATS,*MODEL_FLOATS,'ORIGINAL_SOURCE_DBZH')})
    out[PREFIX+'CANDIDATE_MASK'][:]=candidate
    out[PREFIX+'ORIGINAL_SOURCE_MASK'][:]=seed>0
    out[PREFIX+'ORIGINAL_SOURCE_DBZH'][seed>0]=z[seed>0]
    out[PREFIX+'RAW_PARENT_ID'][candidate]=family[candidate]
    out[PREFIX+'RANGE_M'][candidate]=np.broadcast_to(r,native.shape)[candidate]
    out[PREFIX+'SPACING_M'][candidate]=dr
    out[PREFIX+'TARGET_DBZH'][candidate]=z[candidate]
    out[PREFIX+'HOLD_REASON'][candidate]=1 # No original model qualified.
    for row in np.flatnonzero(candidate.any(axis=1)):
        for lo,hi in runs(~blocked[row]):
            targets=np.flatnonzero(candidate[row,lo:hi])+lo
            for identity in np.unique(family[row,targets]):
                local=targets[family[row,targets]==identity]
                # Source clues must be original gates inside this RAW family,
                # but full reference support comes from their complete ledger ID.
                originals=np.unique(seed[row,(family[row]==identity)&(seed[row]>0)])
                for source in originals:
                    gates=np.flatnonzero((seed[row]==source)&(np.arange(len(r))>=lo)&(np.arange(len(r))<hi))
                    if not len(gates):continue
                    pos=np.searchsorted(gates,local)
                    nearest=np.minimum(abs(r[local]-r[gates[np.clip(pos,0,len(gates)-1)]]),
                        abs(r[local]-r[gates[np.clip(pos-1,0,len(gates)-1)]]))
                    for block in np.unique((r[local]//20000.).astype(int)):
                        select=((r[local]//20000.).astype(int)==block)&(nearest<=120000.)
                        gg=local[select]
                        if not len(gg):continue
                        model=reference_model(r,dr,z[row],gates,block)
                        if model is None:continue
                        residual=z[row,gg]-20.*np.log10(np.maximum(r[gg],1.)/50000.)-model['INTERCEPT_DB']
                        consistent=model['REFERENCE_P90_DB']<=2.5 and model['REFERENCE_SPLIT_DELTA_DB']<=1.5
                        accepted=consistent&(abs(residual)<=2.5)
                        previous=out[PREFIX+'SOURCE_ID'][row,gg]
                        ambiguous=((previous>0)&(previous!=source)&accepted)|(out[PREFIX+'HOLD_REASON'][row,gg]==32)
                        # A failed alternative must not overwrite accepted proof.
                        save=(out[PREFIX+'MODEL_AVAILABLE_MASK'][row,gg]==0)|accepted
                        use=gg[save]
                        out[PREFIX+'MODEL_AVAILABLE_MASK'][row,use]=1
                        out[PREFIX+'MODEL_SOURCE_ID'][row,use]=source
                        for key,value in {**model,'RESIDUAL_DB':residual[save],
                                'ANCHOR_DISTANCE_M':nearest[select][save]}.items():out[PREFIX+key][row,use]=value
                        out[PREFIX+'SOURCE_ID'][row,gg[accepted]]=source
                        out[PREFIX+'HOLD_REASON'][row,use]=np.where(accepted[save],0,2)
                        out[PREFIX+'HOLD_REASON'][row,gg[ambiguous]]=32
                        out[PREFIX+'SOURCE_ID'][row,gg[ambiguous]]=0
    out[PREFIX+'QUALIFIED_MASK'][:]=out[PREFIX+'SOURCE_ID']>0
    return out,{'version':'fan-joint-v1','candidate_gates':int(candidate.sum()),
        'model_available_gates':int(out[PREFIX+'MODEL_AVAILABLE_MASK'].sum()),
        'qualified_gates':int(out[PREFIX+'QUALIFIED_MASK'].sum()),'source_claim':False,
        'filled_gates':0,'recursive_growth':False,'cross_ray_gain_enabled':False}


def validate(group,observed,blocked):
    get=lambda key:np.asarray(group[PREFIX+key][:]);shape=observed.shape
    seed=np.asarray(group[LEDGER+'SEED_ID'][:]);family=np.asarray(group[FAN+'ID'][:])
    flags={}
    for key in ('CANDIDATE_MASK','QUALIFIED_MASK','MODEL_AVAILABLE_MASK','ORIGINAL_SOURCE_MASK'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint8'):raise ValueError('invalid fan joint mask')
        flags[key]=mask(get(key),shape,key)
    c,q,m=flags['CANDIDATE_MASK'],flags['QUALIFIED_MASK'],flags['MODEL_AVAILABLE_MASK']
    if (not np.array_equal(c,(family>0)&(seed==0)&observed&~blocked) or np.any(q&~m) or
        np.any((c|flags['ORIGINAL_SOURCE_MASK'])&(~observed|blocked)) or
        not np.array_equal(flags['ORIGINAL_SOURCE_MASK'],seed>0)):
        raise ValueError('fan joint source/candidate crossed barrier')
    for key in ('SOURCE_ID','MODEL_SOURCE_ID','RAW_PARENT_ID'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint32'):raise ValueError('invalid fan joint ID')
    hold=get('HOLD_REASON')
    if (hold.shape!=shape or hold.dtype!=np.dtype('uint16') or not np.isin(hold,[0,1,2,32]).all() or
        not np.array_equal(get('SOURCE_ID')>0,q) or not np.array_equal(get('MODEL_SOURCE_ID')>0,m) or
        np.any(q&(hold!=0)) or np.any(c&~q&(hold==0)) or np.any(~c&(hold!=0)) or
        not np.array_equal(get('RAW_PARENT_ID'),np.where(c,family,0))):raise ValueError('fan joint identity/hold mismatch')
    for keys,available in ((TARGET_FLOATS,c),(MODEL_FLOATS,m),(('ORIGINAL_SOURCE_DBZH',),seed>0)):
        for key in keys:
            v=get(key)
            if v.shape!=shape or v.dtype!=np.dtype('float32') or not np.array_equal(np.isfinite(v),available):
                raise ValueError('invalid fan joint model availability')
    if np.any(q&((get('SOURCE_ID')!=get('MODEL_SOURCE_ID'))|(get('REFERENCE_P90_DB')>2.5)|
            (get('REFERENCE_SPLIT_DELTA_DB')>1.5)|(abs(get('RESIDUAL_DB'))>2.5))):
        raise ValueError('fan joint action lacks consistent original model')
    for key,fan_key in (('RANGE_M','RANGE_M'),('SPACING_M','SPACING_M')):
        if not np.allclose(get(key)[c],np.asarray(group[FAN+fan_key][:])[c],atol=.01):
            raise ValueError('fan joint target coordinates differ from frozen RAW parent')
    coordinates=np.asarray(group[LEDGER+'RANGE_M'][:]);spacing=np.asarray(group[LEDGER+'SPACING_M'][:])
    source_values=get('ORIGINAL_SOURCE_DBZH')
    for row in np.flatnonzero(m.any(axis=1)):
        for lo,hi in runs(~blocked[row]):
            gg=np.flatnonzero(m[row,lo:hi])+lo
            for source in np.unique(get('MODEL_SOURCE_ID')[row,gg]):
                original=np.flatnonzero((seed[row]==source)&(np.arange(shape[1])>=lo)&(np.arange(shape[1])<hi))
                if not len(original):raise ValueError('fan joint original source absent')
                targets=gg[get('MODEL_SOURCE_ID')[row,gg]==source]
                if not np.isin(family[row,targets],family[row,original]).all():
                    raise ValueError('fan joint original source was outside RAW parent')
                r=np.full(shape[1],np.nan);r[original]=coordinates[row,original];r[targets]=get('RANGE_M')[row,targets]
                dr=float(spacing[row,original[0]])
                for block in np.unique((r[targets]//20000.).astype(int)):
                    use=targets[(r[targets]//20000.).astype(int)==block]
                    model=reference_model(r,dr,source_values[row],original,block)
                    if model is None:raise ValueError('fan joint target/guard supplied reference support')
                    for key,value in model.items():
                        if not np.allclose(get(key)[row,use],value,rtol=1e-5,atol=.02):raise ValueError('fan joint original reference differs')
                    near=np.min(abs(r[use,None]-r[original][None,:]),axis=1)
                    residual=get('TARGET_DBZH')[row,use]-20.*np.log10(np.maximum(r[use],1.)/50000.)-model['INTERCEPT_DB']
                    if (np.any(near>120000.) or not np.allclose(get('ANCHOR_DISTANCE_M')[row,use],near,atol=.05) or
                        not np.allclose(get('RESIDUAL_DB')[row,use],residual,atol=.02)):
                        raise ValueError('fan joint source distance/residual differs')
