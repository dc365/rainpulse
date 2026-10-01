"""Frozen RAW radial backbones and their measured fringes; evidence only.

A near weather attachment does not redefine a persistent radial core's bearing.
Keep its complete original distance history, and test the lower-level parent's
width history before judging safe distance segments. No source-ID inheritance,
fragment chaining, missing-data filling, or automatic product actions.
"""
import numpy as np
from ..arrays import mask, moment, native_geometry, runs
from .geometry import ResourceLimit
from .morphology_objects import SCALES_M

PREFIX='RV2_BACKBONE_'


def detect(native, blocked, *, beam_width=None, maximum_objects=10000):
    r,az,dr,good,gaps=native_geometry(native)
    if beam_width is not None and (not np.isfinite(beam_width) or beam_width<=0):
        raise ValueError('positive finite beam width required')
    if not isinstance(maximum_objects,int) or not 1<=maximum_objects<=10000:
        raise ValueError('bounded positive object budget required')
    if np.prod(native.shape)>2000000:raise ResourceLimit('backbone gate budget exceeded')
    z,observed=moment(native,'DBZH');snr,sa=moment(native,'SNR');rho,ra=moment(native,'RHOHV')
    barred=mask(blocked,native.shape,'backbone barriers')|~good[:,None]
    raw=observed&good[:,None]&(z>=0)&(r[None,:]>=2000)
    known=observed|(sa&(snr<=3))
    weather=raw&ra&sa&(rho>=.95)&(snr>=10)
    out={PREFIX+'MASK':np.zeros(native.shape,'uint8'),
         PREFIX+'STRONG_MASK':np.zeros(native.shape,'uint8'),
         PREFIX+'ID':np.zeros(native.shape,'uint32'),
         PREFIX+'WEATHER_VETO_MASK':weather.astype('uint8')}
    records=[];work=0
    def charge(amount):
        nonlocal work
        work+=int(amount)
        if work>50000000:raise ResourceLimit('backbone work budget exceeded; no partial result')
    for rows in np.split(np.arange(len(az)),np.flatnonzero(gaps[:-1])+1):
        if len(rows)<3:continue
        angles=np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        steps=np.diff(angles);spacing=float(np.median(steps))
        if spacing<=0 or np.any(steps<=0) or np.any(steps>1.5*spacing):continue
        footprints=(np.r_[steps[0],steps]+np.r_[steps,steps[-1]])/2
        beam=max(spacing,beam_width or spacing)
        for scale in SCALES_M:
            blocks=(r//scale).astype(int);unique=np.unique(blocks)
            columns=[np.flatnonzero(blocks==b) for b in unique]
            for level in (10.,20.,35.):
                occupied=[];context=[]
                for cols in columns:
                    charge(2*len(rows)*len(cols))
                    ix=np.ix_(rows,cols)
                    count=(raw[ix]&(z[ix]>=level)).sum(axis=1)
                    occupied.append((count>=2)&(count*dr>=500))
                    count=(raw[ix]&(z[ix]>=min(level,10.))).sum(axis=1)
                    context.append((count>=2)&(count*dr>=500))
                occupied=np.asarray(occupied).T;context=np.asarray(context).T
                persistent=np.zeros(len(rows),bool);bounds={}
                for row in range(len(rows)):
                    use=np.flatnonzero(occupied[row])
                    if len(use)<6:continue
                    first,last=int(use[0]),int(use[-1])
                    span=r[columns[last][-1]]+dr-r[columns[first][0]]
                    # Keep the whole history: no restart after a failed near part.
                    if span<100000 or len(use)/(last-first+1)<.7 or np.any(np.diff(use)>2):continue
                    if r[columns[first][0]]<=0 or (r[columns[last][-1]]+dr)/r[columns[first][0]]<1.7:continue
                    persistent[row]=True;bounds[row]=(first,last)
                for a,b in runs(persistent):
                    if len(records)>=maximum_objects:raise ResourceLimit('backbone object budget exceeded; no partial result')
                    first=min(bounds[k][0] for k in range(a,b));last=max(bounds[k][1] for k in range(a,b))
                    # One fixed antenna-footprint fringe, never new linked seeds.
                    fringe=max(beam,float(footprints[max(0,a-1):min(len(rows),b+1)].max()))
                    left=a
                    while left>0 and angles[a]-angles[left-1]<=fringe+1e-6:left-=1
                    right=b
                    while right<len(rows) and angles[right]-angles[b-1]<=fringe+1e-6:right+=1
                    start=float(r[columns[first][0]]);end=float(r[columns[last][-1]]+dr)
                    holds=[];width=float(angles[right-1]-angles[left]+spacing)
                    if width>90:holds.append('broad_backbone_domain')
                    history=[];widths=[];distances=[]
                    for j in range(first,last+1):
                        parents=[(x,y) for x,y in runs(context[:,j]) if max(a,x)<min(b,y)]
                        if parents:
                            x=min(x for x,y in parents);y=max(y for x,y in parents)
                            pw=float(angles[y-1]-angles[x]+spacing)
                            widths.append(pw);distances.append(float(r[columns[j]].mean()))
                        else:pw=None
                        history.append(dict(block=int(unique[j]),parent_width_deg=pw,
                            core_occupied_fraction=float(occupied[a:b,j].mean())))
                    slope=correlation=None
                    if len(widths)<6:holds.append('insufficient_original_parent_history')
                    elif np.max(widths)>90:holds.append('broad_original_parent')
                    elif np.ptp(widths)>beam and max(distances)/min(distances)>=1.7:
                        slope=float(np.polyfit(np.log(distances),np.log(widths),1)[0])
                        correlation=float(np.corrcoef(np.log(distances),np.log(widths))[0,1])
                        if slope<=-.4 and correlation<=-.7:holds.append('narrowing_physical_width_weather_counterexample')
                    # Hard barriers split eligibility, but do not erase original
                    # parent history or create a newly qualified far-tail parent.
                    window_ok=[];acceptances=[];targets=[];support_cols=[]
                    for j in range(first,last+1):
                        cols=columns[j];ix=np.ix_(rows[left:right],cols)
                        charge((right-left+2)*len(cols))
                        rr,cc=np.where(raw[ix]);rr=rows[left+rr];cc=cols[cc]
                        accept=~barred[rr,cc]&~weather[rr,cc]
                        # Mean original core, never a mean diluted by fringe noise.
                        body=z[np.ix_(rows[a:b],cols)];valid=observed[np.ix_(rows[a:b],cols)]
                        mean=np.divide(np.where(valid,body,0).sum(axis=0),valid.sum(axis=0),
                            out=np.full(len(cols),np.nan),where=valid.sum(axis=0)>0)
                        clear=np.ones(len(cols),bool)
                        for side in (left-1,right):
                            if side<0 or side>=len(rows):clear[:]=False;accept[:]=False;break
                            k=rows[side]
                            clear &= ~barred[k,cols]&known[k,cols]&((observed[k,cols]&(z[k,cols]<=mean-6))|(~observed[k,cols]&sa[k,cols]&(snr[k,cols]<=3)))
                            quiet=~observed[k,cc]&sa[k,cc]&(snr[k,cc]<=3)
                            accept &= ~barred[k,cc]&known[k,cc]&((observed[k,cc]&(z[k,cc]<=z[rr,cc]-6))|quiet)
                        # A protected gate blocks its entire distance column,
                        # not unrelated observed columns in this fixed stencil.
                        # Full range barriers still terminate qualified windows.
                        safe_columns=~barred[ix].any(axis=0)
                        # A weather core cannot serve as shape authority for
                        # its weak, polarization-missing fringe at this range.
                        safe_columns &= ~weather[np.ix_(rows[a:b],cols)].any(axis=0)
                        clear &= safe_columns
                        accept &= safe_columns[np.searchsorted(cols,cc)]
                        history[j-first].update(blocked_columns=int((~safe_columns).sum()),
                            measured_boundary_fraction=float(clear.mean()))
                        window_ok.append(bool(clear.mean()>=.8 and occupied[a:b,j].mean()>=.6))
                        acceptances.append(accept);targets.append((rr,cc))
                        anchors=raw[np.ix_(rows[a:b],cols)]&(z[np.ix_(rows[a:b],cols)]>=level)
                        support_cols.append(cols[anchors.any(axis=0)&clear])
                    strong_windows=np.zeros(last-first+1,bool)
                    for x,y in runs(window_ok):
                        segment_start=float(r[columns[first+x][0]]);segment_end=float(r[columns[first+y-1][-1]]+dr)
                        support=len(np.unique(np.concatenate(support_cols[x:y])))*dr
                        if y-x>=6 and segment_end-segment_start>=100000 and support>=20000:
                            strong_windows[x:y]=True
                    if not strong_windows.any():holds.append('insufficient_unbroken_measured_boundary_support')
                    identity=len(records)+1;qualified=0
                    for j,((rr,cc),accept) in enumerate(zip(targets,acceptances,strict=True)):
                        out[PREFIX+'MASK'][rr,cc]=1
                        empty=out[PREFIX+'ID'][rr,cc]==0;out[PREFIX+'ID'][rr[empty],cc[empty]]=identity
                        if not holds and strong_windows[j]:
                            out[PREFIX+'STRONG_MASK'][rr[accept],cc[accept]]=1
                            out[PREFIX+'ID'][rr[accept],cc[accept]]=identity;qualified+=int(accept.sum())
                    records.append(dict(id=identity,scale_m=scale,level_dbz=level,start_m=start,end_m=end,
                        core_left_deg=float(angles[a]),core_right_deg=float(angles[b-1]),
                        frozen_width_deg=width,history=history,narrowing_log_slope=slope,
                        frozen_fringe_deg=fringe,
                        narrowing_correlation=correlation,holds=holds,strong=not holds,
                        qualified_gates=qualified,qualified_windows=int(strong_windows.sum())))
    return out,dict(version='frozen-radial-backbone-v1',objects=records,action_gates=0,
        product_writes=False,source_claim=False,filled_gates=0,recursive_growth=False,
        independent_weather_truth=False,strong_evidence_gates=int(out[PREFIX+'STRONG_MASK'].sum()))


def validate(arrays,native,blocked,**kwargs):
    expected,_=detect(native,blocked,**kwargs)
    if set(arrays)!=set(expected):raise ValueError('backbone field set differs')
    for key,value in expected.items():
        actual=np.asarray(arrays[key])
        if actual.dtype!=value.dtype or actual.shape!=value.shape or not np.array_equal(actual,value):
            raise ValueError('backbone proof differs: '+key)


def evidence(native,blocked,**kwargs):
    from .morphology_objects import evidence as native_evidence
    return native_evidence(native,blocked,prefix=PREFIX,**kwargs)


def validate_serialized(group,observed,blocked):
    from .morphology_objects import validate_serialized as replay_native_evidence
    replay_native_evidence(group,observed,blocked,prefix=PREFIX,replay=validate)
