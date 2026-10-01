"""Measured transverse RAW fragments in a frozen radial envelope; research only.

An original fragment anchors one bounded angular neighborhood. Newly associated
fragments never become anchors. No residual/ROI/source ledger enters detection.
"""
import numpy as np
from scipy.ndimage import label, find_objects
from ..arrays import native_geometry, moment, mask
from .geometry import ResourceLimit

PREFIX = 'RV2_CONSTELLATION_'


def shoulder_failure_samples(row, fr, fc, segment, ranges, z, observed, snr, snr_available,
                             barred, *, limit=32):
    """Bounded measurement diagnostics; missing DBZH is never called dry air."""
    quiet=~observed[row,fc]&snr_available[row,fc]&(snr[row,fc]<=3)
    contrast=observed[row,fc]&(z[row,fc]<=z[segment[fr],fc]-6)
    failed=barred[row,fc]|~(quiet|contrast)
    indices=np.flatnonzero(failed)
    finite=lambda value:float(value) if np.isfinite(value) else None
    samples=[]
    for i in indices[:limit]:
        col=int(fc[i])
        kind=('observed_dbzh' if observed[row,col] else 'measured_quiet_snr' if quiet[i] else
              'measured_nonquiet_snr' if snr_available[row,col] else 'unknown')
        samples.append(dict(target_row=int(segment[fr[i]]),column=col,opposing_row=int(row),
            range_m=float(ranges[col]),target_dbzh=finite(z[segment[fr[i]],col]),
            opposing_dbzh=finite(z[row,col]) if observed[row,col] else None,
            opposing_snr=finite(snr[row,col]) if snr_available[row,col] else None,
            observation_state=kind,protected=bool(barred[row,col])))
    return dict(failed_gate_count=int(failed.sum()),samples=samples,
                samples_truncated=len(indices)>limit,action_authority=False)


def original_distance_partitions(group, history, parents, ranges, dr, beam, *, measured_accept=None):
    """Research-only RAW topology partitions; never cut a shared lower parent.

    A 20km gap separates candidates, not evidence of dry air. Full lower-parent
    intervals extend each member before partitioning, so a weak/weather bridge
    cannot be discarded by splitting its higher-intensity fragments.
    """
    lookup = {p['component_id']:p for p in parents}
    intervals = []
    for i, member in enumerate(history):
        original = [lookup[p] for p in member['lower_parent_ids']]
        lo = min([member['range_min_m']] + [p['range_min_m'] for p in original])
        hi = max([member['range_max_m']] + [p['range_max_m'] for p in original])
        intervals.append((lo, hi, i))
    buckets = []; end = None
    for lo, hi, i in sorted(intervals):
        if end is None or lo-end > 20000:
            buckets.append([])
            end = hi
        else:
            end = max(end, hi)
        buckets[-1].append(i)
    result = []
    for indices in buckets:
        members = [history[i] for i in indices]
        ids = sorted({p for m in members for p in m['lower_parent_ids']})
        local = [lookup[p] for p in ids]
        hold = any(p['angular_width_deg'] > 4*beam for p in local)
        distance = np.array([p['mean_range_m'] for p in local])
        width = np.array([p['angular_width_deg'] for p in local])
        if len(local) >= 4 and distance.min()>0 and distance.max()/distance.min()>=1.7 and np.ptp(width)>beam:
            hold |= (np.polyfit(np.log(distance),np.log(width),1)[0] <= -.4 and
                     np.corrcoef(np.log(distance),np.log(width))[0,1] <= -.7)
        columns = np.unique(np.concatenate([group[i]['cols'] for i in indices]))
        assessment=short_segment_assessment(members,ranges[columns],dr,beam,hold)
        record=dict(original_components=[group[i]['ident'] for i in indices],
            original_lower_parent_ids=ids, original_lower_parent_geometry_hold=bool(hold),
            parent_start_m=min(p['range_min_m'] for p in local),
            parent_end_m=max(p['range_max_m'] for p in local),
            assessment=assessment,
            gap_is_not_dry_evidence=True, action_authority=False)
        if measured_accept is not None:
            # Full original geometry and every weather member remain in scope.
            # Only independently confirmed observations contribute support.
            selected=[i for i in indices if np.asarray(measured_accept[i],bool).any()]
            accepted_columns=np.unique(np.concatenate([group[i]['cols'][measured_accept[i]]
                for i in selected])) if selected else np.array([],dtype=int)
            windows=[len(np.unique((ranges[accepted_columns]-ranges[columns[0]])//scale))
                     for scale in (1000,2000,5000)]
            holds=[h for h in assessment['hold_reasons'] if h!='short_requires_complete_bilateral_observations']
            if len(selected)<4 or len(accepted_columns)*dr<5000 or min(windows)<4:
                holds.append('insufficient_independently_measured_short_support')
            record['measured_subset']=dict(research_only=True,action_authority=False,
                qualified=not holds,original_geometry_unchanged=True,
                measured_original_components=[group[i]['ident'] for i in selected],
                measured_range_support_m=float(len(accepted_columns)*dr),
                measured_window_counts=windows,hold_reasons=holds)
        result.append(record)
    return result


def short_segment_assessment(history, ranges, dr, beam, parent_hold):
    """Stricter short-object research evidence; never supplies action authority."""
    distance=np.asarray(ranges,dtype=float)
    widths=np.array([m['angular_width_deg'] for m in history])
    bearings=np.rad2deg(np.unwrap(np.deg2rad([m['bearing_deg'] for m in history])))
    has_edges=any('original_left_deg' in m or 'original_right_deg' in m for m in history)
    if has_edges:
        if not all('original_left_deg' in m and 'original_right_deg' in m for m in history):
            raise ValueError('complete finite original edges required')
        left=np.array([m['original_left_deg'] for m in history])
        right=np.array([m['original_right_deg'] for m in history])
        if (not np.isfinite(left).all() or not np.isfinite(right).all() or
            np.any(right <= left) or not np.allclose(right-left,widths,rtol=0,atol=1e-6)):
            raise ValueError('complete finite original edges required')
        bearings=np.rad2deg(np.unwrap(np.deg2rad((left+right)/2)))
    span=float(distance[-1]-distance[0]+dr)
    support=float(len(distance)*dr)
    windows=[len(np.unique((distance-distance[0])//scale)) for scale in (1000,2000,5000)]
    holds=[]
    if span>=60000:holds.append('outside_short_object_scale')
    if len(history)<4:holds.append('fewer_than_four_original_members')
    if span<15000 or support<5000 or min(windows)<4:holds.append('insufficient_short_multiscale_support')
    if any(m['bilateral_fraction']<1. for m in history):holds.append('short_requires_complete_bilateral_observations')
    if any(m['observed_weather_gates'] or m['lower_parent_weather_gates'] or
           m['protected_gates'] or m['lower_parent_protected_gates'] for m in history):
        holds.append('weather_or_protected_original_member')
    if parent_hold:holds.append('full_parent_geometry_hold')
    center_drift=float(np.ptp(bearings));width_drift=float(np.ptp(widths))
    if center_drift>.25*beam:holds.append('unstable_short_center')
    if width_drift>.5*beam:holds.append('unstable_short_width')
    return dict(research_only=True,action_authority=False,qualified=not holds,
        radial_span_m=span,actual_range_support_m=support,range_window_counts=windows,
        center_drift_deg=center_drift,width_drift_deg=width_drift,hold_reasons=holds,
        center_basis='complete_original_edges' if has_edges else 'legacy_population_centroid')


def measured_shoulder_windows(r,dr,columns,known,quiet,protected,charge):
    """Bounded physical windows; unknown samples never contribute quiet support."""
    decisions=np.ones(len(columns),bool);records=[]
    charge(3*len(r)+3*len(columns))
    cumulative=[np.r_[0,np.cumsum(x,dtype='float64')] for x in (known,quiet,protected)]
    for width in (1000.,2000.,5000.):
        lo=r[columns]-width/2;hi=r[columns]+width/2
        left=np.searchsorted(r,lo);right=np.searchsorted(r,hi,side='right')
        size=right-left
        known_fraction=(cumulative[0][right]-cumulative[0][left])/size
        quiet_fraction=(cumulative[1][right]-cumulative[1][left])/size
        protected_count=cumulative[2][right]-cumulative[2][left]
        complete=(lo>=r[0]-dr/2)&(hi<=r[-1]+dr/2)&(size>=5)
        contamination=known_fraction-quiet_fraction
        decisions &= complete&(known_fraction>=.9)&(quiet_fraction>=.8)&(contamination<=.1+1e-12)&(protected_count==0)
        records.append(dict(width_m=width,minimum_known_fraction=float(known_fraction.min()),
            minimum_quiet_fraction=float(quiet_fraction.min()),maximum_unknown_fraction=float((1-known_fraction).max()),
            maximum_contamination_fraction=float(contamination.max()),protected_windows=int((protected_count>0).sum()),
            incomplete_windows=int((~complete).sum())))
    return decisions,records


def detect(native, blocked, *, beam_width=None, maximum_objects=10000, segment_evidence=False,
           shoulder_windows=False,shoulder_band=False, partition_evidence=False, short_subset_evidence=False):
    if shoulder_band and not shoulder_windows:
        raise ValueError('bounded shoulder band requires measured window research')
    if short_subset_evidence and not (partition_evidence and shoulder_windows):
        raise ValueError('short subset requires complete partition and measured window evidence')
    r, az, dr, good, gaps = native_geometry(native)
    if native.shape[0] < 3:
        raise ValueError('at least three native rays required')
    if np.prod(native.shape) > 2000000:
        raise ResourceLimit('constellation gate budget exceeded')
    if not isinstance(maximum_objects, int) or not 1 <= maximum_objects <= 10000:
        raise ValueError('bounded positive object budget required')
    if beam_width is not None and (not np.isfinite(beam_width) or beam_width <= 0):
        raise ValueError('positive finite beam width required')
    z, observed = moment(native, 'DBZH'); snr, sa = moment(native, 'SNR'); rho, ra = moment(native, 'RHOHV')
    barred = mask(blocked, native.shape, 'constellation barriers') | ~good[:, None]
    weather = observed & ra & sa & (rho >= .95) & (snr >= 10)
    out = {PREFIX+'MASK': np.zeros(native.shape, 'uint8'),
           PREFIX+'STRONG_MASK': np.zeros(native.shape, 'uint8'),
           PREFIX+'ID': np.zeros(native.shape, 'uint32'),
           PREFIX+'WEATHER_VETO_MASK': weather.astype('uint8')}
    if short_subset_evidence:
        out[PREFIX+'SHORT_RESEARCH_MASK']=np.zeros(native.shape,'uint8')
    steps = (np.diff(az) + 180) % 360 - 180
    positive = steps[steps > 0]
    if not len(positive):
        raise ValueError('increasing native azimuth required')
    spacing = float(np.median(positive)); beam = max(spacing, beam_width or spacing)
    if not np.isfinite(beam) or beam <= 0:
        raise ValueError('positive finite beam width required')
    breaks = gaps[:-1] | (steps <= 0) | (steps > 1.5 * spacing)
    records = []; work = 0
    def charge(amount):
        nonlocal work
        work += int(amount)
        if work > 50000000:
            raise ResourceLimit('constellation work budget exceeded; no partial output')
    for segment in np.split(np.arange(len(az)), np.flatnonzero(breaks)+1):
        angle = np.rad2deg(np.unwrap(np.deg2rad(az[segment])))
        lower_labels=None; lower_parents={}
        for level in (10., 20., 35.):
            charge(len(segment)*len(r))
            use = observed[segment] & good[segment, None] & (z[segment] >= level)
            labels, count = label(use, np.ones((3, 3)))
            if count > 20000:
                raise ResourceLimit('constellation component budget exceeded')
            if level==10.:lower_labels=labels
            fragments = []
            for ident, box in enumerate(find_objects(labels), 1):
                if box is None:
                    continue
                rr, cc = np.where(labels[box] == ident)
                rr += box[0].start; cc += box[1].start
                charge(len(rr))
                if level==10.:
                    lower_parents[ident]=dict(component_id=ident,
                        range_min_m=float(r[cc].min()),range_max_m=float(r[cc].max()+dr),
                        mean_range_m=float(r[cc].mean()),angular_width_deg=float(np.ptp(angle[rr])+spacing),
                        weather_gates=int(weather[segment[rr],cc].sum()),
                        protected_gates=int(barred[segment[rr],cc].sum()))
                if len(rr) < 3 or np.ptp(r[cc])+dr > 20000 or np.ptp(angle[rr])+spacing > 4*beam:
                    continue
                bearing = float(angle[rr].mean()); theta = np.deg2rad(angle[rr])
                xy = np.column_stack((r[cc]*np.sin(theta), r[cc]*np.cos(theta)))
                values, vectors = np.linalg.eigh(np.cov(xy, rowvar=False))
                radial = np.array([np.sin(np.deg2rad(bearing)), np.cos(np.deg2rad(bearing))])
                alignment = abs(vectors[:, -1] @ radial)
                if alignment > .5 or values[-1] < 4*max(values[0], 1.):
                    continue
                fragments.append(dict(ident=ident, rows=rr, cols=cc, bearing=bearing,
                    left=int(rr.min()), right=int(rr.max())))
            if not fragments:
                continue
            bearings = np.array([f['bearing'] for f in fragments]); seen = set()
            window_cache={}
            # Every neighborhood is anchored in original RAW, never in a new association.
            for anchor in fragments:
                charge(len(fragments))
                members = np.flatnonzero(abs(bearings-anchor['bearing']) <= beam)
                key = tuple(fragments[i]['ident'] for i in members)
                if len(members) < 4 or key in seen:
                    continue
                seen.add(key); group = [fragments[i] for i in members]
                rr = np.concatenate([f['rows'] for f in group]); cc = np.concatenate([f['cols'] for f in group])
                cols = np.unique(cc); span = float(r[cols[-1]]-r[cols[0]]+dr)
                windows = [len(np.unique(r[cols]//scale)) for scale in (5000, 10000, 20000)]
                if span < 60000 or len(cols)*dr < 5000 or min(windows) < 4:
                    continue
                if len(records) >= maximum_objects:
                    raise ResourceLimit('constellation object budget exceeded; no partial output')
                holds = []; accept = []; fractions = []; side_observations = []; member_history = []
                lower_ids=np.unique(lower_labels[rr,cc])
                lower_history=[lower_parents[int(identity)] for identity in lower_ids]
                lower_hold=any(p['angular_width_deg']>4*beam for p in lower_history)
                lower_slope=lower_correlation=None
                lower_distance=np.array([p['mean_range_m'] for p in lower_history])
                lower_width=np.array([p['angular_width_deg'] for p in lower_history])
                if len(lower_history)>=4 and lower_distance.min()>0 and lower_distance.max()/lower_distance.min()>=1.7 and np.ptp(lower_width)>beam:
                    lower_slope=float(np.polyfit(np.log(lower_distance),np.log(lower_width),1)[0])
                    lower_correlation=float(np.corrcoef(np.log(lower_distance),np.log(lower_width))[0,1])
                    lower_hold |= lower_slope<=-.4 and lower_correlation<=-.7
                if lower_hold:holds.append('full_lower_contour_parent_geometry_hold')
                if any(p['weather_gates'] for p in lower_history):holds.append('measured_lower_parent_weather')
                if any(p['protected_gates'] for p in lower_history):holds.append('protected_lower_parent')
                if weather[segment[rr], cc].any(): holds.append('measured_weather_member')
                if barred[segment[rr], cc].any(): holds.append('protected_original_member')
                for fragment in group:
                    fr, fc = fragment['rows'], fragment['cols']
                    parent_ids=np.unique(lower_labels[fr,fc])
                    parent_weather=sum(lower_parents[int(i)]['weather_gates'] for i in parent_ids)
                    parent_protected=sum(lower_parents[int(i)]['protected_gates'] for i in parent_ids)
                    accepted = ~barred[segment[fr], fc] & ~weather[segment[fr], fc]
                    sides = []
                    for side in (fragment['left']-1, fragment['right']+1):
                        if side < 0 or side >= len(segment):
                            accepted[:] = False; break
                        row = segment[side]
                        quiet = ~observed[row, fc] & sa[row, fc] & (snr[row, fc] <= 3)
                        contrast = observed[row, fc] & (z[row, fc] <= z[segment[fr], fc]-6)
                        sides.append(dict(observed_dbzh_fraction=float(observed[row, fc].mean()),
                            measured_quiet_snr_fraction=float(quiet.mean()),
                            measured_nonquiet_snr_fraction=float((~observed[row,fc]&sa[row,fc]&~quiet).mean()),
                            unknown_fraction=float((~observed[row, fc] & ~sa[row,fc]).mean())))
                        if partition_evidence:
                            sides[-1]['point_failure_diagnostics']=shoulder_failure_samples(
                                row,fr,fc,segment,r,z,observed,snr,sa,barred)
                        if shoulder_windows:
                            cache_key=(fragment['ident'],side)
                            if cache_key in window_cache:
                                window_accept,window_detail=window_cache[cache_key]
                                sides[-1].update(window_detail)
                                accepted &= window_accept
                                continue
                            reference=float(np.median(z[segment[fr],fc]))
                            exterior=np.array([row])
                            if shoulder_band:
                                direction=-1 if side<fragment['left'] else 1
                                count=int(np.floor(2*beam/spacing+.5))
                                positions=side+direction*np.arange(count)
                                if (positions<0).any() or (positions>=len(segment)).any():
                                    sides[-1]['angular_band_incomplete']=True
                                    accepted[:]=False;continue
                                edge=angle[fragment['left']]-spacing/2 if direction<0 else angle[fragment['right']]+spacing/2
                                if np.any(abs(angle[positions]-edge)>2*beam+1e-6):
                                    sides[-1]['angular_band_incomplete']=True
                                    accepted[:]=False;continue
                                exterior=segment[positions]
                                sides[-1]['angular_band_rows']=list(map(int,exterior))
                                sides[-1]['angular_band_max_offset_deg']=float(abs(angle[positions]-edge).max())
                                charge(len(exterior)*len(r))
                            band_observed=observed[exterior];band_sa=sa[exterior]
                            band_quiet=(~band_observed&band_sa&(snr[exterior]<=3))|(
                                band_observed&(z[exterior]<=reference-6))
                            row_quiet=band_quiet.mean(axis=0)
                            row_known=(band_observed|band_sa).mean(axis=0)
                            window_accept,window_report=measured_shoulder_windows(r,dr,fc,
                                row_known,row_quiet,(barred[exterior]|weather[exterior]).any(axis=0),charge)
                            sides[-1]['distance_windows']=window_report
                            window_cache[cache_key]=(window_accept,{k:v for k,v in sides[-1].items()
                                if k.startswith('angular_band_') or k=='distance_windows'})
                            accepted &= window_accept
                        else:
                            accepted &= ~barred[row, fc] & (quiet | contrast)
                    fractions.append(float(accepted.mean())); accept.append(accepted); side_observations.append(sides)
                    member_history.append(dict(component_id=fragment['ident'],
                        range_min_m=float(r[fc].min()), range_max_m=float(r[fc].max()+dr),
                        bearing_deg=fragment['bearing'] % 360,
                        angular_width_deg=float(np.ptp(angle[fr])+spacing),
                        original_left_deg=float(angle[fr].min()-spacing/2),
                        original_right_deg=float(angle[fr].max()+spacing/2),
                        observed_weather_gates=int(weather[segment[fr], fc].sum()),
                        protected_gates=int(barred[segment[fr], fc].sum()),
                        lower_parent_ids=list(map(int,parent_ids)),lower_parent_weather_gates=parent_weather,
                        lower_parent_protected_gates=parent_protected,
                        bilateral_fraction=float(accepted.mean())))
                # Keep every original member in qualification history; no restarting
                # the object after discarding a failed/unknown/weather fragment.
                if min(fractions) < .8: holds.append('incomplete_measured_bilateral_boundaries')
                object_id = len(records)+1
                out[PREFIX+'MASK'][segment[rr], cc] = 1
                out[PREFIX+'ID'][segment[rr], cc] = object_id
                # Full-parent width history is retained before optional segmentation.
                # A fixed-km ribbon narrows in angle as range increases; splitting
                # away its near/weather part must not evade this counterexample.
                distance = np.array([(m['range_min_m']+m['range_max_m'])/2 for m in member_history])
                widths = np.array([m['angular_width_deg'] for m in member_history])
                narrowing = False; slope = correlation = None
                if distance.min() > 0 and distance.max()/distance.min() >= 1.7 and np.ptp(widths) > beam:
                    slope = float(np.polyfit(np.log(distance), np.log(widths), 1)[0])
                    correlation = float(np.corrcoef(np.log(distance), np.log(widths))[0, 1])
                    narrowing = slope <= -.4 and correlation <= -.7
                if narrowing:
                    holds.append('full_original_parent_narrowing_weather_counterexample')
                if not holds:
                    for f, accepted in zip(group, accept, strict=True):
                        out[PREFIX+'STRONG_MASK'][segment[f['rows'][accepted]], f['cols'][accepted]] = 1
                segments = []
                ordered = np.argsort([m['range_min_m'] for m in member_history])
                qualifying = np.array([m['bilateral_fraction'] >= .8 and
                    not m['observed_weather_gates'] and not m['lower_parent_weather_gates'] and
                    not m['protected_gates'] and not m['lower_parent_protected_gates'] for m in member_history])
                # Any original failed member blocks its observed radial interval,
                # including another member that overlaps the same interval.
                for i in np.flatnonzero(~qualifying):
                    lo, hi = member_history[i]['range_min_m'], member_history[i]['range_max_m']
                    for j, m in enumerate(member_history):
                        if m['range_min_m'] < hi and m['range_max_m'] > lo:
                            qualifying[j] = False
                edges = np.diff(np.r_[False, qualifying[ordered], False].astype('int8'))
                for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
                    indices = ordered[start:end]
                    if len(indices) < 2:
                        continue
                    sc = np.unique(np.concatenate([group[i]['cols'] for i in indices]))
                    ss = float(r[sc[-1]]-r[sc[0]]+dr)
                    sw = [len(np.unique(r[sc]//scale)) for scale in (5000,10000,20000)]
                    eligible = len(indices)>=4 and not narrowing and not lower_hold and ss >= 60000 and len(sc)*dr >= 5000 and min(sw) >= 4
                    segments.append(dict(original_components=[group[i]['ident'] for i in indices],
                        radial_span_m=ss, actual_range_support_m=float(len(sc)*dr),
                        range_window_counts=sw, independently_qualified=bool(eligible),
                        short_assessment=short_segment_assessment([member_history[i] for i in indices],
                            r[sc],dr,beam,narrowing or lower_hold)))
                    if segment_evidence and eligible:
                        for i in indices:
                            f = group[i]; selected = accept[i]
                            out[PREFIX+'STRONG_MASK'][segment[f['rows'][selected]],f['cols'][selected]] = 1
                records.append(dict(id=object_id, contour_dbz=level, original_components=list(key),
                    anchor_bearing_deg=anchor['bearing'] % 360, neighborhood_half_width_deg=beam,
                    radial_span_m=span, actual_range_support_m=float(len(cols)*dr),
                    range_window_counts=windows, bilateral_fractions=fractions,
                    side_observations=side_observations,
                    member_history=member_history, measured_segments=segments,
                    full_parent_width_slope=slope, full_parent_width_correlation=correlation,
                    full_parent_narrowing_weather_hold=bool(narrowing),
                    full_lower_contour_history=lower_history,lower_parent_width_slope=lower_slope,
                    lower_parent_width_correlation=lower_correlation,lower_parent_geometry_hold=bool(lower_hold),
                    strong=not holds, hold_reasons=holds))
                if partition_evidence:
                    records[-1]['original_distance_partitions'] = original_distance_partitions(
                        group,member_history,lower_history,r,dr,beam,
                        measured_accept=accept if short_subset_evidence else None)
                    if short_subset_evidence:
                        for partition in records[-1]['original_distance_partitions']:
                            if not partition['measured_subset']['qualified']:
                                continue
                            ids=set(partition['original_components'])
                            for fragment,confirmed in zip(group,accept,strict=True):
                                if fragment['ident'] in ids:
                                    out[PREFIX+'SHORT_RESEARCH_MASK'][segment[fragment['rows'][confirmed]],
                                        fragment['cols'][confirmed]]=1
    return out, dict(objects=records, source_claim=False, recursive_growth=False,
        action_gates=0, research_only=True, shoulder_windows=bool(shoulder_windows),
        shoulder_band=bool(shoulder_band),work=work)


def validate(arrays, native, blocked, **options):
    expected, _ = detect(native, blocked, **options)
    if set(arrays) != set(expected):
        raise ValueError('constellation proof field set mismatch')
    for key, value in expected.items():
        actual=np.asarray(arrays[key])
        if actual.dtype != value.dtype or actual.shape != value.shape or not np.array_equal(actual, value):
            raise ValueError('constellation proof mismatch: '+key)


def evidence(native, blocked, *, beam_width=None):
    from .morphology_objects import evidence as native_evidence
    arrays=native_evidence(native,blocked,prefix=PREFIX,beam_width=beam_width)
    arrays[PREFIX+'SEGMENT_MODE']=np.ones(native.shape,'uint8')
    return arrays


def validate_serialized(group, observed, blocked):
    from .morphology_objects import validate_serialized as replay_native_evidence
    key=PREFIX+'SEGMENT_MODE'
    if key not in group:
        raise ValueError('constellation segment contract missing')
    mode=np.asarray(group[key][:])
    if mode.dtype != np.dtype('uint8') or mode.shape != observed.shape or not (mode==1).all():
        raise ValueError('constellation segment contract differs')
    def replay(arrays,native,barred,**options):
        validate(arrays,native,barred,segment_evidence=True,**options)
    replay_native_evidence(group,observed,blocked,prefix=PREFIX,replay=replay)
