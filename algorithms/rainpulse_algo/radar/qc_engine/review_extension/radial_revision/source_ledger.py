"""Freeze complete original source tracks independently of RAW-family tiling.

This ledger provides bounded geometric lineage, never a new action/source vote.
All original seeds are recorded, including sources with no narrow-strip proof.
"""
from enum import IntFlag
import numpy as np
from ..arrays import mask, moment, native_geometry, runs
from .source_envelope import _corridor

PREFIX = 'RV2_SOURCE_LEDGER_'
FLOATS = ('START_M','END_M','SUPPORT_M','RAW_START_M','RAW_END_M',
          'LEFT_DEG','RIGHT_DEG','BEAM_DEG','REFERENCE_AZ_DEG',
          'RANGE_M','SPACING_M','LINK_DISTANCE_M')


class Hold(IntFlag):
    SHORT_SOURCE = 1
    NO_NARROW_BOUNDARY = 2
    UNSTABLE_BOUNDARY = 4
    NO_PARENT = 8
    AMBIGUOUS_PARENT = 16


def freeze(native, blocked, original_source, nominations, *, beam_width=None, source_kind=None):
    r,az,dr,good,gaps = native_geometry(native)
    z,observed = moment(native,'DBZH')
    snr,snr_ok = moment(native,'SNR')
    blocked = mask(blocked,native.shape,'source ledger barriers') | ~good[:,None]
    seeds = mask(original_source,native.shape,'original ledger sources') & observed & ~blocked
    candidates = mask(nominations,native.shape,'ledger nominations') & observed & ~blocked
    kinds = seeds.astype('uint8') if source_kind is None else np.asarray(source_kind)
    if kinds.shape!=native.shape or kinds.dtype!=np.dtype('uint8') or np.any(seeds&(kinds==0)):
        raise ValueError('original source kind missing or malformed')
    out={PREFIX+k:np.zeros(native.shape,'uint8') for k in ('SEED_MASK','KIND','CANDIDATE_MASK','LINK_MASK')}
    out.update({PREFIX+k:np.zeros(native.shape,'uint32') for k in ('SEED_ID','RAW_PARENT_ID','LINK_PARENT_ID')})
    out.update({PREFIX+k:np.zeros(native.shape,'uint16') for k in ('SOURCE_HOLD','LINK_HOLD')})
    out.update({PREFIX+k:np.full(native.shape,np.nan,'float32') for k in FLOATS})
    out[PREFIX+'SEED_MASK'][:]=seeds
    out[PREFIX+'KIND'][seeds]=kinds[seeds]
    out[PREFIX+'CANDIDATE_MASK'][:]=candidates
    out[PREFIX+'LINK_HOLD'][candidates]=int(Hold.NO_PARENT)
    measured=seeds|candidates
    out[PREFIX+'RANGE_M'][measured]=np.broadcast_to(r,native.shape)[measured]
    out[PREFIX+'SPACING_M'][measured]=dr
    objects=0; eligible=0
    raw_conflict=np.zeros(native.shape,bool)
    for rows in np.split(np.arange(len(az)),np.flatnonzero(gaps[:-1])+1):
        angles=np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing=float(np.median(np.diff(angles))) if len(rows)>1 else np.nan
        beam=max(spacing,beam_width or spacing) if spacing>0 else np.nan
        cache={}
        def corridor(i):
            if i not in cache:
                if len(rows)<5 or not 2<=i<len(rows)-2 or not np.isfinite(beam):
                    cache[i]=(np.zeros(len(r),bool),np.full(len(r),np.nan),np.full(len(r),np.nan),~blocked[rows[i]])
                else:cache[i]=_corridor(rows,angles,i,beam,z,observed,blocked,r,
                                      snr=snr,snr_ok=snr_ok)
            return cache[i]
        for i,row in enumerate(rows):
            if not seeds[row].any():continue
            bounded,left,right,safe=corridor(i)
            # Record every source, even one lacking corridor evidence. Range
            # barriers split identity before gaps/RAW family nominations matter.
            for lo,hi in runs(~blocked[row]):
                source_gates=np.flatnonzero(seeds[row,lo:hi])+lo
                if not len(source_gates):continue
                chains=np.split(source_gates,np.flatnonzero(np.diff(source_gates)*dr>60000.+dr)+1)
                for original in chains:
                    objects+=1;identity=objects
                    out[PREFIX+'SEED_ID'][row,original]=identity
                    support=len(original)*dr
                    start,end=r[original[0]],r[original[-1]]+dr
                    hold=0
                    if support<10000.-1e-6:hold|=int(Hold.SHORT_SOURCE)
                    if not bounded[original].all():hold|=int(Hold.NO_NARROW_BOUNDARY)
                    ref_left,ref_right=np.nan,np.nan
                    if bounded[original].all():
                        ref_left,ref_right=float(np.median(left[original])),float(np.median(right[original]))
                        if np.ptp(left[original])>beam+1e-6 or np.ptp(right[original])>beam+1e-6:
                            hold|=int(Hold.UNSTABLE_BOUNDARY)
                    # Freeze RAW extent once, from original seeds only. A
                    # nominated/linked fragment never extends this search.
                    raw_start,raw_end=start,end
                    frozen_rows=[]
                    if not hold:
                        section=next(((a,b) for a,b in runs(safe) if a<=original[0] and original[-1]<b),None)
                        if section is None:hold|=int(Hold.NO_NARROW_BOUNDARY)
                        else:
                            a,b=section
                            # All compatible one-hop RAW rays define the frozen
                            # object extent, independently of candidate masks.
                            for j,rr in enumerate(rows):
                                if abs(angles[j]-angles[i])>beam+1e-6:continue
                                local,ll,hh,local_safe=corridor(j)
                                bounds=next(((a,b) for a,b in runs(safe&local_safe) if a<=original[0] and original[-1]<b),None)
                                if bounds is None:continue
                                a,b=bounds
                                gates=np.flatnonzero(local & (r>=r[a]) & (r<r[b-1]+dr) &
                                    (abs(ll-ref_left)<=beam+1e-6)&(abs(hh-ref_right)<=beam+1e-6))
                                pos=np.searchsorted(original,gates)
                                distance=np.minimum(abs(r[gates]-r[original[np.clip(pos,0,len(original)-1)]]),
                                                    abs(r[gates]-r[original[np.clip(pos-1,0,len(original)-1)]]))
                                frozen=gates[distance<=120000.]
                                if len(frozen):frozen_rows.append((rr,frozen))
                            if frozen_rows:
                                raw_start=min(float(r[gs[0]]) for _,gs in frozen_rows)
                                raw_end=max(float(r[gs[-1]]+dr) for _,gs in frozen_rows)
                    out[PREFIX+'SOURCE_HOLD'][row,original]=hold
                    for key,value in (('START_M',start),('END_M',end),('SUPPORT_M',support),
                                      ('RAW_START_M',raw_start),('RAW_END_M',raw_end),
                                      ('LEFT_DEG',ref_left),('RIGHT_DEG',ref_right),
                                      ('BEAM_DEG',beam),('REFERENCE_AZ_DEG',angles[i])):
                        out[PREFIX+key][row,original]=value
                    if hold:continue
                    eligible+=1
                    for rr,frozen in frozen_rows:
                        raw_ids=out[PREFIX+'RAW_PARENT_ID'][rr,frozen]
                        # Keep ambiguity permanent, including a third parent.
                        ambiguous_raw=(raw_ids>0)|raw_conflict[rr,frozen]
                        raw_conflict[rr,frozen]|=ambiguous_raw
                        out[PREFIX+'RAW_PARENT_ID'][rr,frozen]=np.where(ambiguous_raw,0,identity)
                    for j,rr in enumerate(rows):
                        if abs(angles[j]-angles[i])>beam+1e-6 or not candidates[rr].any():continue
                        local,ll,hh,local_safe=corridor(j)
                        combined=safe&local_safe
                        section=next(((a,b) for a,b in runs(combined) if a<=original[0] and original[-1]<b),None)
                        if section is None:continue
                        a,b=section
                        targets=np.flatnonzero(candidates[rr]&local&~seeds[rr]&
                            (r>=max(raw_start,r[a]))&(r<min(raw_end,r[b-1]+dr))&
                            (abs(ll-ref_left)<=beam+1e-6)&(abs(hh-ref_right)<=beam+1e-6))
                        if not len(targets):continue
                        pos=np.searchsorted(original,targets)
                        distance=np.minimum(abs(r[targets]-r[original[np.clip(pos,0,len(original)-1)]]),
                                            abs(r[targets]-r[original[np.clip(pos-1,0,len(original)-1)]]))
                        valid=distance<=120000.
                        targets,distance=targets[valid],distance[valid]
                        previous=out[PREFIX+'LINK_PARENT_ID'][rr,targets]
                        ambiguous=(previous>0)|(out[PREFIX+'LINK_HOLD'][rr,targets]==int(Hold.AMBIGUOUS_PARENT))
                        out[PREFIX+'LINK_HOLD'][rr,targets]=np.where(ambiguous,int(Hold.AMBIGUOUS_PARENT),0)
                        out[PREFIX+'LINK_PARENT_ID'][rr,targets]=np.where(ambiguous,0,identity)
                        out[PREFIX+'LINK_DISTANCE_M'][rr,targets]=np.where(ambiguous,np.nan,distance)
    out[PREFIX+'LINK_MASK'][:]=out[PREFIX+'LINK_PARENT_ID']>0
    return out,{'version':'complete-source-ledger-v2-measured-nearest-flanks',
                'objects':objects,'narrow_supported_objects':eligible,
                'original_seed_gates':int(seeds.sum()),'linked_gates':int(out[PREFIX+'LINK_MASK'].sum()),
                'ambiguous_gates':int((out[PREFIX+'LINK_HOLD']==int(Hold.AMBIGUOUS_PARENT)).sum()),
                'action_gates':0,'filled_gates':0,'recursive_growth':False,'source_claim':False}


def validate(group,observed,blocked,original_source):
    get=lambda k:np.asarray(group[PREFIX+k][:])
    shape=observed.shape
    for key in ('SEED_MASK','CANDIDATE_MASK','LINK_MASK','KIND'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint8'):raise ValueError('invalid ledger byte field')
    seeds=mask(get('SEED_MASK'),shape,'ledger seeds')
    candidates=mask(get('CANDIDATE_MASK'),shape,'ledger candidates')
    linked=mask(get('LINK_MASK'),shape,'ledger links')
    if (not np.array_equal(seeds,original_source&observed&~blocked) or
            np.any((seeds|candidates)&(~observed|blocked)) or np.any(linked&(~candidates|seeds)) or
            np.any(seeds&(get('KIND')==0)) or np.any(~seeds&(get('KIND')!=0))):
        raise ValueError('ledger source/target observation or lineage changed')
    for key in ('SEED_ID','RAW_PARENT_ID','LINK_PARENT_ID'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint32'):raise ValueError('invalid ledger identity')
    for key in ('SOURCE_HOLD','LINK_HOLD'):
        if get(key).shape!=shape or get(key).dtype!=np.dtype('uint16'):raise ValueError('invalid ledger hold')
    for key in FLOATS:
        value=get(key)
        if value.shape!=shape or value.dtype!=np.dtype('float32') or np.isinf(value).any():raise ValueError('invalid ledger physical evidence')
    for key in ('START_M','END_M','SUPPORT_M','RAW_START_M','RAW_END_M','REFERENCE_AZ_DEG'):
        if not np.array_equal(np.isfinite(get(key)),seeds):raise ValueError('ledger source extent lacks complete observations')
    for key in ('RANGE_M','SPACING_M'):
        if not np.array_equal(np.isfinite(get(key)),seeds|candidates):raise ValueError('ledger coordinate availability changed')
    if (np.any(get('SOURCE_HOLD')[~seeds]!=0) or np.any(get('SOURCE_HOLD')>7) or
            np.any(get('LINK_HOLD')[~candidates]!=0) or not np.isin(get('LINK_HOLD'),[0,8,16]).all()):
        raise ValueError('invalid ledger hold bit set')
    sid,parent=get('SEED_ID'),get('LINK_PARENT_ID')
    if (not np.array_equal(sid>0,seeds) or not np.array_equal(parent>0,linked) or
            not np.array_equal(np.isfinite(get('LINK_DISTANCE_M')),linked) or
            np.any(linked&(get('LINK_HOLD')!=0)) or np.any(candidates&~linked&(get('LINK_HOLD')==0))):
        raise ValueError('ledger IDs or hold inconsistent')
    seed_flat=np.flatnonzero(seeds)
    seed_flat=seed_flat[np.argsort(sid.flat[seed_flat],kind='stable')]
    linked_flat=np.flatnonzero(linked)
    linked_flat=linked_flat[np.argsort(parent.flat[linked_flat],kind='stable')]
    targets_by_parent={int(parent.flat[chunk[0]]):chunk for chunk in
        (np.split(linked_flat,np.flatnonzero(np.diff(parent.flat[linked_flat]))+1) if len(linked_flat) else [])}
    unique_ids=set()
    for indices in (np.split(seed_flat,np.flatnonzero(np.diff(sid.flat[seed_flat]))+1) if len(seed_flat) else []):
        rows,gates=np.unravel_index(indices,shape);identity=int(sid[rows[0],gates[0]])
        unique_ids.add(identity)
        if len(np.unique(rows))!=1:raise ValueError('ledger original ray identity changed')
        spacing=get('SPACING_M')[rows,gates]
        ranges=get('RANGE_M')[rows,gates]
        support=get('SUPPORT_M')[rows,gates]
        start,end=get('START_M')[rows,gates],get('END_M')[rows,gates]
        if (not np.isfinite(ranges).all() or not np.isfinite(spacing).all() or np.any(spacing<=0) or
            np.ptp(spacing)>1e-3 or not np.allclose(support,len(gates)*spacing) or
            not np.allclose(start,ranges.min()) or not np.allclose(end,ranges.max()+spacing)):
            raise ValueError('ledger support/bounds differ from original seeds')
        targets=targets_by_parent.get(identity)
        if targets is None:continue
        first=(rows[0],gates[0]);beam=float(get('BEAM_DEG')[first])
        if (np.any(get('SOURCE_HOLD')[rows,gates]!=0) or support[0]<10000. or
            not np.isfinite(beam) or beam<=0 or
            not np.isfinite(get('LEFT_DEG')[first]) or get('RIGHT_DEG')[first]<=get('LEFT_DEG')[first]):
            raise ValueError('ledger parent lacks original support/boundary')
        target_range=get('RANGE_M').flat[targets]
        pos=np.searchsorted(ranges,target_range)
        nearest=np.minimum(abs(target_range-ranges[np.clip(pos,0,len(ranges)-1)]),
                           abs(target_range-ranges[np.clip(pos-1,0,len(ranges)-1)]))
        if (np.any(~np.isfinite(target_range)) or np.any(nearest>120000.) or
            not np.allclose(nearest,get('LINK_DISTANCE_M').flat[targets],rtol=1e-5,atol=.05) or
            np.any(target_range<get('RAW_START_M')[first]) or np.any(target_range>=get('RAW_END_M')[first])):
            raise ValueError('ledger link advanced beyond original RAW extent/distance')
    if not set(np.unique(parent[linked])).issubset(unique_ids):raise ValueError('ledger parent has no original seeds')
    raw=get('RAW_PARENT_ID')
    if np.any((raw>0)&(~observed|blocked)) or not set(np.unique(raw[raw>0])).issubset(unique_ids):
        raise ValueError('ledger RAW ownership lacks original source')
