"""Windowed variable-width continuation of frozen original sources.

No new fragment is a source. Unknown flanks remain explicit geometric unknowns;
this path is experimental quarantine, not confirmed receiver classification.
"""
import numpy as np
from scipy.ndimage import uniform_filter1d
from ..arrays import mask,moment,native_geometry,runs
from .source_ledger import PREFIX as LEDGER

PREFIX='RV2_SOURCE_WINDOW_'
STATS=tuple(f'WINDOW{s}_{k}' for s in (20,60) for k in
            ('SUPPORT_M','INTERIOR_FRACTION','LEFT_STRONG_FRACTION','RIGHT_STRONG_FRACTION',
             'LEFT_AVAILABLE_FRACTION','RIGHT_AVAILABLE_FRACTION'))
FLOATS=('LEFT_DEG','RIGHT_DEG','START_M','END_M','SOURCE_SUPPORT_M','SOURCE_SPAN_M',
        'ANCHOR_DISTANCE_M','RANGE_M','SPACING_M','BEAM_DEG','REFERENCE_AZ_DEG','TARGET_AZ_DEG',*STATS)


def window_bounds(native,blocked,rows,angles,index,beam):
    """Pool actual observations within safe physical windows, not binary filling."""
    r,_,dr,good,_=native_geometry(native)
    z,obs=moment(native,'DBZH');row=rows[index]
    valid=obs&~blocked&good[:,None]&(z>=0.)&(r[None,:]>=2000.)
    empty={k:np.full(len(r),np.nan,'float32') for k in ('LEFT_DEG','RIGHT_DEG',*STATS)}
    if not 2<=index<len(rows)-2:return empty,np.zeros(len(r),bool)
    base_safe=~blocked[rows[index-2:index+3]].any(axis=0)
    decided=np.zeros(len(r),bool)
    pairs=set()
    for half_left in (1.,2.,3.,4.):
        for half_right in (1.,2.,3.,4.):
            a=np.searchsorted(angles,angles[index]-half_left*beam+1e-8,side='right')-1
            b=np.searchsorted(angles,angles[index]+half_right*beam-1e-8,side='left')
            if 0<=a<index<b<len(rows) and angles[b]-angles[a]<=8.+1e-6:
                if abs((angles[b]+angles[a])/2-angles[index])<=beam+1e-6:pairs.add((a,b))
    for a,b in sorted(pairs,key=lambda pair:(angles[pair[1]]-angles[pair[0]],pair)):
        stencil=rows[a:b+1]
        if not good[stencil].all():continue
        safe=base_safe&~blocked[stencil].any(axis=0)
        inside=(valid[rows[a+1:b]]&(z[rows[a+1:b]]>=z[row]-6.)).mean(axis=0)
        left_strong=obs[rows[a]]&(z[rows[a]]>z[row]-6.)
        right_strong=obs[rows[b]]&(z[rows[b]]>z[row]-6.)
        for lo,hi in runs(safe):
            centre=valid[row,lo:hi]
            if not centre.any():continue
            accepted=centre.copy();stats={}
            for scale in (20,60):
                size=max(3,int(round(scale*1000./dr)))|1
                total=lambda v:uniform_filter1d(np.asarray(v,float),size,mode='constant')*size
                count=np.rint(total(centre))
                fraction=lambda v:np.clip(np.divide(total(np.where(centre,v,0.)),count,
                    out=np.zeros(hi-lo),where=count>0),0.,1.)
                values={'SUPPORT_M':count*dr,'INTERIOR_FRACTION':fraction(inside[lo:hi]),
                    'LEFT_STRONG_FRACTION':fraction(left_strong[lo:hi]),
                    'RIGHT_STRONG_FRACTION':fraction(right_strong[lo:hi]),
                    'LEFT_AVAILABLE_FRACTION':fraction(obs[rows[a],lo:hi]),
                    'RIGHT_AVAILABLE_FRACTION':fraction(obs[rows[b],lo:hi])}
                accepted&=(count>=3)&(values['SUPPORT_M']>=500.-1e-6)&(values['INTERIOR_FRACTION']>=.6-1e-6)
                accepted&=(values['LEFT_STRONG_FRACTION']<=.2+1e-6)&(values['RIGHT_STRONG_FRACTION']<=.2+1e-6)
                stats.update({f'WINDOW{scale}_{key}':value for key,value in values.items()})
            # Freeze the shortest pooled corridor BEFORE the local veto. A
            # wider alternative cannot absorb a polluting flank into its band.
            accepted&=~decided[lo:hi]
            decided[lo:hi][accepted]=True
            accepted&=~left_strong[lo:hi]&~right_strong[lo:hi]
            empty['LEFT_DEG'][lo:hi][accepted]=angles[a]
            empty['RIGHT_DEG'][lo:hi][accepted]=angles[b]
            for key,value in stats.items():empty[key][lo:hi][accepted]=value[accepted]
    return empty,base_safe


def detect(native,blocked,ledger,*,beam_width=None):
    r,az,dr,good,gaps=native_geometry(native);_,obs=moment(native,'DBZH')
    blocked=mask(blocked,native.shape,'window source barriers')|~good[:,None]
    source_id=np.asarray(ledger[LEDGER+'SEED_ID'])
    out={PREFIX+k:np.zeros(native.shape,'uint8') for k in ('CANDIDATE_MASK','QUALIFIED_MASK')}
    out.update({PREFIX+k:np.zeros(native.shape,'uint32') for k in ('PARENT_ID','SEED_ID','ORIGINAL_ID')})
    out[PREFIX+'HOLD_REASON']=np.zeros(native.shape,'uint16')
    out.update({PREFIX+k:np.full(native.shape,np.nan,'float32') for k in FLOATS})
    objects=0;qualified_sources=0
    for rows in np.split(np.arange(len(az)),np.flatnonzero(gaps[:-1])+1):
        if len(rows)<5:continue
        angles=np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing=float(np.median(np.diff(angles)))
        if spacing<=0:continue
        beam=max(spacing,beam_width or spacing);cache={}
        def bounds(i):
            if i not in cache:cache[i]=window_bounds(native,blocked,rows,angles,i,beam)
            return cache[i]
        for i in range(2,len(rows)-2):
            row=rows[i]
            if not source_id[row].any():continue
            own_evidence,safe=bounds(i)
            evidence={key:value.copy() if key in ('LEFT_DEG','RIGHT_DEG') else value for key,value in own_evidence.items()}
            # Freeze the RAW object's one-hop neighbourhood before any target
            # qualification, including fragments absent from the original ray.
            for j in range(2,len(rows)-2):
                if j==i or abs(angles[j]-angles[i])>beam+1e-6:continue
                neighbour,neighbour_safe=bounds(j)
                add=(~np.isfinite(evidence['LEFT_DEG'])&np.isfinite(neighbour['LEFT_DEG'])&safe&neighbour_safe&
                     (abs((neighbour['LEFT_DEG']+neighbour['RIGHT_DEG'])/2-angles[i])<=beam+1e-6))
                for key in ('LEFT_DEG','RIGHT_DEG'):evidence[key][add]=neighbour[key][add]
            bounded=np.isfinite(evidence['LEFT_DEG'])
            for lo,hi in runs(safe):
                gates=np.flatnonzero(bounded[lo:hi])+lo
                if not len(gates):continue
                # Each original RAW block supplies its robust boundaries; a
                # selected tail never updates this frozen block sequence.
                blocks=np.unique((r[gates]//20000.).astype(int));chains=[]
                previous=None
                for block in blocks:
                    local=gates[(r[gates]//20000.).astype(int)==block]
                    left=float(np.median(evidence['LEFT_DEG'][local]));right=float(np.median(evidence['RIGHT_DEG'][local]))
                    stable=(np.ptp(evidence['LEFT_DEG'][local])<=beam+1e-6 and np.ptp(evidence['RIGHT_DEG'][local])<=beam+1e-6)
                    if not stable:previous=None;continue
                    join=(previous is not None and (block-previous[0])<=3 and
                          abs(left-previous[1])<=min(2.,block-previous[0])*beam+1e-6 and
                          abs(right-previous[2])<=min(2.,block-previous[0])*beam+1e-6)
                    if not join:chains.append([])
                    chains[-1].append((block,local,left,right));previous=(block,left,right)
                for chain in chains:
                    frozen=np.concatenate([entry[1] for entry in chain])
                    raw_start,raw_end=float(r[frozen[0]]),float(r[frozen[-1]]+dr)
                    ids=np.unique(source_id[row,frozen]);ids=ids[ids>0]
                    for identity in ids:
                        original=frozen[(source_id[row,frozen]==identity)&np.isfinite(own_evidence['LEFT_DEG'][frozen])]
                        if not len(original):continue
                        support=len(original)*dr;span=float(r[original[-1]]-r[original[0]]+dr)
                        strong=support>=10000.-1e-6 and span>=60000.-1e-6 and len(np.unique((r[original]//20000.).astype(int)))>=3
                        objects+=1
                        track_id=objects
                        if strong:
                            out[PREFIX+'SEED_ID'][row,original]=track_id
                            out[PREFIX+'ORIGINAL_ID'][row,original]=identity;qualified_sources+=1
                        # One-hop angular search only from this original ray.
                        for j,rr in enumerate(rows):
                            if abs(angles[j]-angles[i])>beam+1e-6:continue
                            target_ev,target_safe=bounds(j)
                            direct_safe=safe&target_safe
                            section=next(((a,b) for a,b in runs(direct_safe) if a<=original[0] and original[-1]<b),None)
                            if section is None:continue
                            a,b=section
                            target=np.flatnonzero(np.isfinite(target_ev['LEFT_DEG'])&(r>=max(raw_start,r[a]))&
                                (r<min(raw_end,r[b-1]+dr))&(source_id[rr]==0))
                            if not len(target):continue
                            # Neighbour boundaries must match the ORIGINAL RAW
                            # block, not boundaries inherited from another tail.
                            block_ids=np.array([entry[0] for entry in chain])
                            block_target=(r[target]//20000.).astype(int)
                            pos=np.searchsorted(block_ids,block_target)
                            exact=(pos<len(block_ids))&(block_ids[np.minimum(pos,len(block_ids)-1)]==block_target)
                            left=np.array([entry[2] for entry in chain])[np.minimum(pos,len(chain)-1)]
                            right=np.array([entry[3] for entry in chain])[np.minimum(pos,len(chain)-1)]
                            compat=exact&(abs(target_ev['LEFT_DEG'][target]-left)<=beam+1e-6)&(abs(target_ev['RIGHT_DEG'][target]-right)<=beam+1e-6)
                            p=np.searchsorted(original,target)
                            distance=np.minimum(abs(r[target]-r[original[np.clip(p,0,len(original)-1)]]),
                                                abs(r[target]-r[original[np.clip(p-1,0,len(original)-1)]]))
                            target,distance=target[compat&(distance<=120000.)],distance[compat&(distance<=120000.)]
                            if not len(target):continue
                            previous=out[PREFIX+'ORIGINAL_ID'][rr,target]
                            ambiguous=((previous>0)&(previous!=identity))|(out[PREFIX+'HOLD_REASON'][rr,target]==2)
                            out[PREFIX+'CANDIDATE_MASK'][rr,target]=1
                            out[PREFIX+'HOLD_REASON'][rr,target]=np.where(ambiguous,2,0 if strong else 1)
                            out[PREFIX+'PARENT_ID'][rr,target]=np.where(ambiguous,0,track_id if strong else 0)
                            out[PREFIX+'ORIGINAL_ID'][rr,target]=identity
                            values={'START_M':raw_start,'END_M':raw_end,'SOURCE_SUPPORT_M':support,'SOURCE_SPAN_M':span,
                                    'ANCHOR_DISTANCE_M':distance,'RANGE_M':r[target],'SPACING_M':dr,
                                    'BEAM_DEG':beam,'REFERENCE_AZ_DEG':angles[i],'TARGET_AZ_DEG':angles[j]}
                            values.update({key:target_ev[key][target] for key in ('LEFT_DEG','RIGHT_DEG',*STATS)})
                            for key,value in values.items():out[PREFIX+key][rr,target]=value
    out[PREFIX+'QUALIFIED_MASK'][:]=out[PREFIX+'PARENT_ID']>0
    return out,{'version':'source-window-v1','objects':objects,'qualified_sources':qualified_sources,
        'candidate_gates':int(out[PREFIX+'CANDIDATE_MASK'].sum()),
        'qualified_gates':int(out[PREFIX+'QUALIFIED_MASK'].sum()),
        'ambiguous_gates':int((out[PREFIX+'HOLD_REASON']==2).sum()),
        'filled_gates':0,'recursive_growth':False,'confirmed_gates':0,'source_claim':False}


def validate(group,observed,blocked):
    get=lambda key:np.asarray(group[PREFIX+key][:]);shape=observed.shape
    for key in ('CANDIDATE_MASK','QUALIFIED_MASK'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint8'):raise ValueError('invalid source window mask')
    candidate=mask(get('CANDIDATE_MASK'),shape,'source window candidates')
    qualified=mask(get('QUALIFIED_MASK'),shape,'source window qualification')
    for key in ('PARENT_ID','SEED_ID','ORIGINAL_ID'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint32'):raise ValueError('invalid source window ID')
    hold=get('HOLD_REASON');parent=get('PARENT_ID');seed=get('SEED_ID')
    if (hold.shape!=shape or hold.dtype!=np.dtype('uint16') or not np.isin(hold,[0,1,2]).all() or
        not np.array_equal(parent>0,qualified) or np.any(qualified&(~candidate|(hold!=0))) or
        np.any(candidate&~qualified&(hold==0)) or np.any((candidate|(seed>0))&(~observed|blocked))):
        raise ValueError('source window crossed barrier or lost hold/identity')
    original=np.asarray(group[LEDGER+'SEED_ID'][:])
    if np.any((seed>0)&((get('ORIGINAL_ID')!=original)|(original==0))) or np.any(candidate&(original>0)):
        raise ValueError('source window promoted a new fragment to source')
    for key in FLOATS:
        value=get(key)
        if value.shape!=shape or value.dtype!=np.dtype('float32') or not np.array_equal(np.isfinite(value),candidate):
            raise ValueError('invalid source window physical evidence')
    r=get('RANGE_M');dr=get('SPACING_M');left=get('LEFT_DEG');right=get('RIGHT_DEG')
    if np.any(candidate&((right<=left)|(right-left>8.00001)|(dr<=0)|(r<get('START_M'))|(r>=get('END_M'))|
            (get('ANCHOR_DISTANCE_M')<0)|(get('ANCHOR_DISTANCE_M')>120000.)|
            (abs(get('TARGET_AZ_DEG')-get('REFERENCE_AZ_DEG'))>get('BEAM_DEG')+1e-4))):
        raise ValueError('source window advanced beyond original bounds')
    for scale in (20,60):
        support=get(f'WINDOW{scale}_SUPPORT_M')
        if np.any(candidate&((support<500.-.01)|(support/dr<3.-1e-4))):raise ValueError('source window sample support insufficient')
        for key in ('INTERIOR_FRACTION','LEFT_STRONG_FRACTION','RIGHT_STRONG_FRACTION','LEFT_AVAILABLE_FRACTION','RIGHT_AVAILABLE_FRACTION'):
            fraction=get(f'WINDOW{scale}_{key}')
            if np.any(candidate&((fraction<0)|(fraction>1))):raise ValueError('source window fraction invalid')
        if np.any(candidate&((get(f'WINDOW{scale}_INTERIOR_FRACTION')<.6-1e-5)|
                (get(f'WINDOW{scale}_LEFT_STRONG_FRACTION')>.2+1e-5)|
                (get(f'WINDOW{scale}_RIGHT_STRONG_FRACTION')>.2+1e-5))):raise ValueError('source window lacks pooled geometry')
    coordinates=np.asarray(group[LEDGER+'RANGE_M'][:])
    spacing=np.asarray(group[LEDGER+'SPACING_M'][:])
    for identity in np.unique(parent[qualified]):
        origin=seed==identity
        rr,gg=np.where(origin)
        if len(np.unique(rr))!=1:raise ValueError('source window lacks original ray')
        ranges=coordinates[origin];actual_dr=spacing[origin]
        support=len(ranges)*actual_dr[0];span=float(ranges.max()-ranges.min()+actual_dr[0])
        use=parent==identity
        original_ids=get('ORIGINAL_ID')[origin]
        if len(np.unique(original_ids))!=1 or np.any(get('ORIGINAL_ID')[use]!=original_ids[0]):
            raise ValueError('source window mixed original parent identities')
        if support<10000.-.01 or span<60000.-.01 or len(np.unique((ranges//20000.).astype(int)))<3:
            raise ValueError('source window lacks original source support')
        pos=np.searchsorted(ranges,r[use])
        nearest=np.minimum(abs(r[use]-ranges[np.clip(pos,0,len(ranges)-1)]),abs(r[use]-ranges[np.clip(pos-1,0,len(ranges)-1)]))
        if (not np.allclose(get('SOURCE_SUPPORT_M')[use],support,atol=.05) or
            not np.allclose(get('SOURCE_SPAN_M')[use],span,atol=.05) or
            not np.allclose(get('ANCHOR_DISTANCE_M')[use],nearest,atol=.05,rtol=1e-5)):
            raise ValueError('source window lineage differs from original seed measurements')
