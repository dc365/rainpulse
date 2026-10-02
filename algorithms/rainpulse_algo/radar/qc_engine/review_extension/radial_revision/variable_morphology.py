"""Complete variable-width RAW envelopes; measured evidence, never QC actions.

Join overlapping original angular runs before judging shape. Keep forks and
failed near-range windows in the object's history: a narrower far tail cannot
restart as a newly qualified object. No station, time or source-ID assumptions.
"""
import numpy as np
from ..arrays import mask, moment, native_geometry, runs
from .geometry import ResourceLimit
from .morphology_objects import _shoulders, SCALES_M, LEVELS_DBZ

PREFIX = 'RV2_VARIABLE_OBJECT_'


def _branch_edges(rows, a, b, columns, angles, beam, z, observed, snr, sa, barred, charge):
    """Find measured shoulders in a fixed two-beam RAW stencil, never grow body.

    Every intervening coordinate must be measured and unbarred. Prefer the
    nearest pair, then require the entire object's exterior edges to be stable.
    Unknown inner flanks cannot be skipped in pursuit of a convenient quiet edge.
    """
    best=(a-1,b)
    candidates=[]
    for left in range(a-1,-1,-1):
        if angles[a]-angles[left]>2*beam+1e-6:break
        for right in range(b,len(rows)):
            if angles[right]-angles[b-1]>2*beam+1e-6:break
            charge((right-left+1)*len(columns))
            stencil=np.ix_(rows[left:right+1],columns)
            known=observed[stencil]|(sa[stencil]&(snr[stencil]<=3))
            measured_paths=(known&~barred[stencil]).all(axis=0)
            if measured_paths.mean()<.8:continue
            # Compare against the original branch, not a diluted expanded body.
            body=z[np.ix_(rows[a:b],columns)]
            present=observed[np.ix_(rows[a:b],columns)]
            centre=np.divide(np.where(present,body,0).sum(axis=0),present.sum(axis=0),
                out=np.full(len(columns),np.nan),where=present.sum(axis=0)>0)
            clear=measured_paths.copy()
            for side in (left,right):
                k=rows[side]
                clear &= ((observed[k,columns]&(z[k,columns]<=centre-6))|
                          (~observed[k,columns]&sa[k,columns]&(snr[k,columns]<=3)))
            if clear.mean()>=.8:
                candidates.append((angles[a]-angles[left]+angles[right]-angles[b-1],left,right))
        # Work is bounded by the fixed native two-beam stencil.
    if candidates:
        _,left,right=min(candidates);best=(left,right)
    return best


def detect(native, blocked, *, beam_width=None, maximum_objects=50000, branch_shoulders=False,
           boundary_hypotheses=False,enclosed_branch_hypotheses=False):
    if enclosed_branch_hypotheses and not boundary_hypotheses:
        raise ValueError('enclosed branches require full original boundary hypotheses')
    r, az, dr, good, gaps = native_geometry(native)
    if beam_width is not None and (not np.isfinite(beam_width) or beam_width <= 0):
        raise ValueError('positive finite antenna beam width required')
    if not isinstance(maximum_objects, int) or not 1<=maximum_objects<=50000:
        raise ValueError('bounded positive integer object budget required')
    if np.prod(native.shape)>2000000:
        raise ResourceLimit('variable morphology native gate budget exceeded')
    z, observed = moment(native, 'DBZH'); snr, sa = moment(native, 'SNR')
    rho, ra = moment(native, 'RHOHV')
    barred = mask(blocked, native.shape, 'variable object barriers') | ~good[:, None]
    # Preserve protected echoes in the shape history; masking them before
    # tracking could turn a weather object's far tail into an isolated streak.
    raw = observed & good[:, None] & (z >= 0) & (r[None, :] >= 2000)
    weather = raw & ra & sa & (rho >= .95) & (snr >= 10)
    result = {PREFIX+'MASK':np.zeros(native.shape,'uint8'),
              PREFIX+'STRONG_MASK':np.zeros(native.shape,'uint8'),
              PREFIX+'ID':np.zeros(native.shape,'uint32'),
              PREFIX+'WEATHER_VETO_MASK':weather.astype('uint8')}
    records = []; trials = 0;work=0
    boundary_records=[]
    if boundary_hypotheses:
        result[PREFIX+'BOUNDARY_HYPOTHESIS_MASK']=np.zeros(native.shape,'uint8')
        result[PREFIX+'BOUNDARY_QUALIFIED_RESEARCH_MASK']=np.zeros(native.shape,'uint8')
    def charge(amount):
        nonlocal work
        work+=int(amount)
        if work>50000000:
            raise ResourceLimit('variable morphology work budget exceeded; no partial result')
    # Sector endpoints and true gaps are never joined by this prototype.
    for rows in np.split(np.arange(len(az)), np.flatnonzero(gaps[:-1])+1):
        if len(rows)<3:continue
        angles=np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing=float(np.median(np.diff(angles)))
        if spacing<=0 or np.any(np.diff(angles)<=0):continue
        steps=np.diff(angles)
        # A declared gap is a segment boundary. An undeclared large jump must
        # not enlarge angular tolerance and manufacture reliable coverage.
        if np.any(steps>1.5*spacing):continue
        left_edges=angles-np.r_[steps[0],steps]/2
        right_edges=angles+np.r_[steps,steps[-1]]/2
        footprints=right_edges-left_edges
        beam=max(spacing, beam_width or spacing)
        if boundary_hypotheses:
            from scipy.ndimage import label,find_objects
            charge(len(rows)*len(r))
            lower_labels,_=label(raw[rows]&(z[rows]>=10),np.ones((3,3)))
            lower_boxes=find_objects(lower_labels);lower_cache={}
        for scale in SCALES_M:
            blocks=(r//scale).astype(int)
            for level in LEVELS_DBZ:
                nodes=[]; parent=[]; edges=[]; recent=[]
                def root(i):
                    while parent[i]!=i:
                        parent[i]=parent[parent[i]];i=parent[i]
                    return i
                for block in np.unique(blocks):
                    cols=np.flatnonzero(blocks==block)
                    charge(len(rows)*len(cols))
                    signal=raw[np.ix_(rows,cols)] & (z[np.ix_(rows,cols)]>=level)
                    count=signal.sum(axis=1)
                    occupied=(count>=2)&(count*dr>=500)
                    recent=[i for i in recent if block-nodes[i]['block']<=2]
                    current=[]
                    for a,b in runs(occupied):
                        trials+=1
                        if trials>maximum_objects:
                            raise ResourceLimit('variable object budget exceeded; no partial result')
                        rr,cc=np.where(signal[a:b]);rr=rows[a+rr];cc=cols[cc]
                        node=dict(a=a,b=b,block=int(block),cols=cols,anchor=np.unique(cc),
                                  rr=rr,cc=cc,left=left_edges[a],right=right_edges[b-1],
                                  footprint=float(footprints[a:b].max()))
                        i=len(nodes);nodes.append(node);parent.append(i);current.append(i)
                        charge(len(recent))
                        matches=[j for j in recent if max(a,nodes[j]['a'])<min(b,nodes[j]['b'])]
                        adjacent=[j for j in matches if block-nodes[j]['block']==1]
                        # Prefer real adjacent windows; a redundant skip edge
                        # is not a fork in the underlying measured envelope.
                        for j in adjacent or matches:
                            charge(1)
                            prior=nodes[j]
                            if max(a,prior['a'])>=min(b,prior['b']):continue
                            # A genuine unavailable distance window is not an
                            # empty bridge. Require observed DBZH or measured
                            # noise throughout the overlap when skipping it.
                            if block-prior['block']==2:
                                bridge=np.flatnonzero(blocks==block-1)
                                overlap=rows[max(a,prior['a']):min(b,prior['b'])]
                                ix=np.ix_(overlap,bridge)
                                known=observed[ix] | (sa[ix] & (snr[ix]<=3))
                                if not known.all() or barred[ix].any():continue
                            parent[root(i)]=root(j);edges.append((j,i))
                    recent.extend(current)
                groups={}
                for i in range(len(nodes)):groups.setdefault(root(i),[]).append(i)
                for indices in groups.values():
                    entries=[nodes[i] for i in indices]
                    if boundary_hypotheses:
                        from .branch_boundaries import measure
                        for hr,hc,hypothesis in measure(entries,rows,left_edges,right_edges,beam,r,dr,
                                z,observed,snr,sa,barred,weather,charge,
                                lower_labels,lower_boxes,lower_cache,
                                enclosed_branches=enclosed_branch_hypotheses):
                            if len(boundary_records)>=maximum_objects:
                                raise ResourceLimit('boundary hypothesis budget exceeded; no partial result')
                            hypothesis.update(parent_id=len(records)+1,scale_m=scale,level_dbz=level,
                                              native_segment_start=int(rows[0]))
                            boundary_records.append(hypothesis)
                            result[PREFIX+'BOUNDARY_HYPOTHESIS_MASK'][hr,hc]=1
                            if hypothesis['geometry_qualified']:
                                result[PREFIX+'BOUNDARY_QUALIFIED_RESEARCH_MASK'][hr,hc]=1
                    widths=np.array([e['right']-e['left'] for e in entries])
                    centres=np.array([(e['right']+e['left'])/2 for e in entries])
                    midrange=np.array([r[e['anchor']].mean() for e in entries])
                    anchor=np.unique(np.concatenate([e['anchor'] for e in entries]))
                    start,end=float(r[anchor[0]]),float(r[anchor[-1]]+dr)
                    span=end-start;support=len(anchor)*dr
                    windows=len(set(e['block'] for e in entries))
                    holds=[]
                    local=set(indices)
                    incoming={i:0 for i in indices};outgoing=incoming.copy()
                    charge(len(edges))
                    for j,i in edges:
                        if i in local:
                            incoming[i]+=1;outgoing[j]+=1
                    if max((*incoming.values(),*outgoing.values()))>1:holds.append('ambiguous_fork_or_merge')
                    if widths.max()>90:holds.append('broad_connected_domain')
                    tolerance=max(beam,max(e['footprint'] for e in entries),.25*float(np.median(widths)))
                    excursion=float(np.max(np.abs(centres-np.median(centres))))
                    if excursion>tolerance:holds.append('curved_or_drifting_centre')
                    # A narrowing constant-km rain ribbon is a required
                    # counterexample. Fit the WHOLE history, not its far tail.
                    slope=None; correlation=None
                    if windows>=4 and midrange.max()/midrange.min()>=1.7 and np.ptp(widths)>beam:
                        x=np.log(midrange);y=np.log(widths)
                        slope=float(np.polyfit(x,y,1)[0])
                        correlation=float(np.corrcoef(x,y)[0,1])
                        if slope<=-.4 and correlation<=-.7:holds.append('narrowing_physical_width_weather_counterexample')
                    if widths.max()-widths.min()>2*beam+.75*np.median(widths):holds.append('unstable_width')
                    kind='line' if widths.max()<=2*beam else 'fan'
                    aspect=span/max(end*np.deg2rad(widths.max()),dr)
                    shape=(span>=80000 and support>=10000 and windows>=4 and aspect>=3) if kind=='line' else (span>=100000 and support>=20000 and windows>=6)
                    if not shape:holds.append('insufficient_object_geometry')
                    branch_candidate=branch_shoulders and not holds
                    if branch_candidate:
                        # Already qualified objects keep their exact original
                        # evidence. Exterior search only addresses failed contrast.
                        baseline=[]
                        for e in entries:
                            charge((e['b']-e['a']+2)*len(e['anchor']))
                            baseline.append(_shoulders(rows,e['a'],e['b'],e['anchor'],z,observed,snr,sa,barred))
                        baseline=np.asarray(baseline)
                        if (baseline[:,0].mean()>=.8 and baseline[:,1].mean()>=.8
                                and ((baseline[:,0]>=.8)&(baseline[:,1]>=.8)).mean()>=.8):
                            branch_candidate=False
                    clear=[];known=[];target_rows=[];target_cols=[];allowed=[];exterior=[]
                    for e in entries:
                        a,b,cols=e['a'],e['b'],e['cols']
                        charge((b-a+2)*len(cols))
                        left,right=(a-1,b)
                        if branch_candidate:
                            left,right=_branch_edges(rows,a,b,e['anchor'],angles,beam,z,observed,snr,sa,barred,charge)
                        # Original branch mean is the contrast reference even
                        # when measured exterior shoulders are farther away.
                        body=z[np.ix_(rows[a:b],e['anchor'])];present=observed[np.ix_(rows[a:b],e['anchor'])]
                        mean=np.divide(np.where(present,body,0).sum(axis=0),present.sum(axis=0),
                            out=np.full(len(e['anchor']),np.nan),where=present.sum(axis=0)>0)
                        kk=np.ones(len(e['anchor']),bool);cll=kk.copy()
                        for side in (left,right):
                            if side<0 or side>=len(rows):kk[:]=False;cll[:]=False;break
                            k=rows[side];c=e['anchor'];noise=sa[k,c]&(snr[k,c]<=3)
                            kk &= ~barred[k,c]&(observed[k,c]|noise)
                            cll &= ~barred[k,c]&((observed[k,c]&(z[k,c]<=mean-6))|(~observed[k,c]&noise))
                        kn,cl=float(kk.mean()),float(cll.mean())
                        if 0<=left<right<len(rows):exterior.append((angles[left],angles[right]))
                        known.append(kn);clear.append(cl)
                        domain=raw[np.ix_(rows[a:b],cols)]
                        rr,cc=np.where(domain);rr=rows[a+rr];cc=cols[cc]
                        inside=(r[cc]>=start)&(r[cc]<end);rr=rr[inside];cc=cc[inside]
                        if barred[np.ix_(rows[a:b],cols)].any():holds.append('original_object_barrier')
                        accept=~barred[rr,cc]&~weather[rr,cc]
                        if branch_candidate and 0<=left<right<len(rows):
                            unique_cc,inverse=np.unique(cc,return_inverse=True)
                            stencil=np.ix_(rows[left:right+1],unique_cc)
                            charge((right-left+1)*len(unique_cc))
                            valid=((observed[stencil]|(sa[stencil]&(snr[stencil]<=3)))&~barred[stencil]).all(axis=0)
                            accept &= valid[inverse]
                        for side in (left,right):
                            if side<0 or side>=len(rows):accept[:]=False;break
                            k=rows[side];quiet=sa[k,cc]&(snr[k,cc]<=3)
                            accept &= ~barred[k,cc]&((observed[k,cc]&(z[k,cc]<=z[rr,cc]-6))|(~observed[k,cc]&quiet))
                        target_rows.append(rr);target_cols.append(cc);allowed.append(accept)
                    if branch_candidate and len(exterior)==len(entries):
                        edges_array=np.asarray(exterior)
                        if np.max(edges_array[:,1]-edges_array[:,0])>90:
                            holds.append('broad_branch_exterior')
                        if np.ptp(edges_array[:,0])>2*beam or np.ptp(edges_array[:,1])>2*beam:
                            holds.append('unstable_branch_exterior')
                    if np.mean(known)<.8:holds.append('unknown_shoulders')
                    confirmed=np.array(known)>=.8;confirmed &= np.array(clear)>=.8
                    if np.mean(clear)<.8 or confirmed.mean()<.8:holds.append('insufficient_repeated_edge_contrast')
                    rr=np.concatenate(target_rows);cc=np.concatenate(target_cols);accept=np.concatenate(allowed)
                    identity=len(records)+1;result[PREFIX+'MASK'][rr,cc]=1
                    prior=result[PREFIX+'STRONG_MASK'][rr,cc]==1
                    result[PREFIX+'ID'][rr[~prior],cc[~prior]]=identity
                    if not holds:
                        result[PREFIX+'STRONG_MASK'][rr[accept],cc[accept]]=1
                        result[PREFIX+'ID'][rr[accept],cc[accept]]=identity
                    records.append(dict(id=identity,kind=kind,scale_m=scale,level_dbz=level,start_m=start,end_m=end,
                        span_m=span,support_m=float(support),windows=windows,width_min_deg=float(widths.min()),
                        width_max_deg=float(widths.max()),centre_excursion_deg=excursion,centre_tolerance_deg=tolerance,
                        centre_median_deg=float(np.median(centres)),left_min_deg=float(min(e['left'] for e in entries)),
                        right_max_deg=float(max(e['right'] for e in entries)),
                        known_shoulders_fraction=float(np.mean(known)),clear_shoulders_fraction=float(np.mean(clear)),
                        bounded_branch_exterior_searched=bool(branch_candidate),
                        exterior_left_min_deg=float(min(x[0] for x in exterior)) if exterior else None,
                        exterior_right_max_deg=float(max(x[1] for x in exterior)) if exterior else None,
                        narrowing_log_slope=slope,narrowing_correlation=correlation,strong=not holds,
                        holds=sorted(set(holds)),member_gates=len(rr),qualified_gates=int(accept.sum()) if not holds else 0))
    report=dict(version='variable-native-morphology-v1',branch_shoulders=bool(branch_shoulders),objects=records,action_gates=0,
        product_writes=False,source_claim=False,filled_gates=0,recursive_growth=False,
        independent_weather_truth=False,strong_evidence_gates=int(result[PREFIX+'STRONG_MASK'].sum()))
    if boundary_hypotheses:
        report['boundary_hypotheses']=boundary_records
        report['boundary_hypothesis_mode']='enclosed_measured_original_runs' if enclosed_branch_hypotheses else 'single_original_run'
    return result,report


def validate(arrays,native,blocked,**kwargs):
    """Read-only replay proves measurement-bound evidence; IDs alone prove nothing."""
    expected,_=detect(native,blocked,**kwargs)
    if set(arrays)!=set(expected):raise ValueError('variable morphology field set differs')
    for key,value in expected.items():
        actual=np.asarray(arrays[key])
        if actual.dtype!=value.dtype or actual.shape!=value.shape or not np.array_equal(actual,value):
            raise ValueError('variable morphology proof differs: '+key)
