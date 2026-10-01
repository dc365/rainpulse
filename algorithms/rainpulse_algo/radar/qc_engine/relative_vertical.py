"""Positive same-volume weather support on relative beam geometry.

Only measured, independently usable upper echoes provide support. Absence and
incompatibility remain unknown. Absolute height/terrain/cross-site evidence is
never inferred by this path: the common antenna-height term cancels.
"""
import numpy as np

EARTH_RADIUS_M = 6371000. * 4. / 3.


def beam_coordinates(ranges, elevation):
    r = np.asarray(ranges, dtype=float)
    el = np.deg2rad(elevation)
    h = np.sqrt(r*r+EARTH_RADIUS_M**2+2*r*EARTH_RADIUS_M*np.sin(el))-EARTH_RADIUS_M
    ground = EARTH_RADIUS_M*np.arcsin(np.clip(r*np.cos(el)/(EARTH_RADIUS_M+h), -1., 1.))
    return ground, h


def _times(native):
    t = np.asarray(native.ray_time)
    if np.issubdtype(t.dtype, np.datetime64):
        return np.where(np.isnat(t), np.nan, t.astype('datetime64[ns]').astype(float)/1e9)
    return t.astype(float)


def support(sweeps, donor_masks, *, beam_width_deg, maximum_time_seconds=300.,
            maximum_height_difference_m=3000., minimum_dbzh=10.):
    if len(sweeps) != len(donor_masks) or not 0 < beam_width_deg <= 5.:
        raise ValueError('relative support requires declared beam width and donor masks')
    identities={(s.attrs.get('radar_id'),s.attrs.get('scan_id')) for s in sweeps}
    if len(identities) != 1 or any(x is None for x in next(iter(identities), (None,None))):
        raise ValueError('relative support requires one original physical radar volume')
    if maximum_time_seconds <= 0 or maximum_height_difference_m <= 0:
        raise ValueError('relative support requires positive physical limits')
    scores=[np.full(s.shape,np.nan,'float32') for s in sweeps]
    available=[np.zeros(s.shape,'uint8') for s in sweeps]
    heights=[np.full(s.shape,np.nan,'float32') for s in sweeps]
    donors=[]
    donor_counts=[]
    funnel={'matched_ray_pairs':0,'current_observed_pairs':0,'physical_pairs':0,'clean_upper_pairs':0,'positive_pairs':0}
    for s,mask in zip(sweeps,donor_masks,strict=True):
        m=np.asarray(mask)
        if m.shape!=s.shape or not np.isin(m,[0,1]).all():raise ValueError('invalid independent donor mask')
        good=np.asarray(s.geometry_good,bool)[:,None]
        z=s.fields['DBZH'];rho=s.fields.get('RHOHV',np.full(s.shape,np.nan));snr=s.fields.get('SNR',np.full(s.shape,np.nan))
        usable=m.astype(bool)&good&s.field_available['DBZH']&np.isfinite(z)&(z>=minimum_dbzh)
        usable &= s.field_available.get('RHOHV',np.zeros(s.shape,bool))&np.isfinite(rho)&(rho>=.95)
        usable &= s.field_available.get('SNR',np.zeros(s.shape,bool))&np.isfinite(snr)&(snr>=10.)
        donors.append(usable)
        donor_counts.append({'cut':s.name,'independent_input':int(np.asarray(m,bool).sum()),
            'measured_weather_donors':int(usable.sum())})
    for i,low in enumerate(sweeps):
        if not low.field_available['DBZH'].any():continue
        for j,high in enumerate(sweeps):
            if np.median(high.elevation)<=np.median(low.elevation)+.2 or not donors[j].any():continue
            delta=abs((low.azimuth[:,None]-high.azimuth[None,:]+180.)%360.-180.)
            rows=np.argmin(delta,axis=1);offset=delta[np.arange(len(rows)),rows]
            time_delta=abs(_times(low)-_times(high)[rows])
            for row,rr in enumerate(rows):
                if (not low.geometry_good[row] or not high.geometry_good[rr] or
                    offset[row] > beam_width_deg/2. or not np.isfinite(time_delta[row]) or
                    time_delta[row]>maximum_time_seconds or high.elevation[rr]<=low.elevation[row]+.2):continue
                funnel['matched_ray_pairs']+=1
                lg,lh=beam_coordinates(low.ranges,low.elevation[row])
                hg,hh=beam_coordinates(high.ranges,high.elevation[rr])
                pos=np.searchsorted(hg,lg);a=np.clip(pos-1,0,len(hg)-1);b=np.clip(pos,0,len(hg)-1)
                ix=np.where(abs(lg-hg[a])<=abs(lg-hg[b]),a,b)
                dr=float(np.median(np.diff(hg)))
                spatial=np.hypot(lg-hg[ix],lg*np.deg2rad(offset[row]))
                footprint=np.maximum(dr*.55,lg*np.tan(np.deg2rad(beam_width_deg/2.)))
                dz=abs(lh-hh[ix])
                matched=low.field_available['DBZH'][row]&np.isfinite(low.fields['DBZH'][row])&(low.fields['DBZH'][row]>=minimum_dbzh)
                funnel['current_observed_pairs']+=int(matched.sum())
                matched &= (lg>=hg[0])&(lg<=hg[-1])&(spatial<=footprint)&(dz<=maximum_height_difference_m)
                funnel['physical_pairs']+=int(matched.sum())
                # Require an actual native upper neighbourhood, not one isolated gate.
                stable=np.convolve(donors[j][rr].astype(int),np.ones(3,dtype=int),mode='same')==3
                matched &= donors[j][rr,ix]&stable[ix]
                funnel['clean_upper_pairs']+=int(matched.sum())
                # Positive support only. We do not publish negative/zero scores.
                score=1.-np.clip(np.maximum(low.fields['DBZH'][row]-high.fields['DBZH'][rr,ix],0.)/15.,0.,1.)
                matched &= score>=.7
                funnel['positive_pairs']+=int(matched.sum())
                better=matched&(~np.isfinite(scores[i][row])|(score>scores[i][row]))
                scores[i][row,better]=score[better];available[i][row,better]=1;heights[i][row,better]=dz[better]
    return scores,available,heights,{'version':'relative-vertical-positive-v1',
        'positive_gates':sum(int(x.sum()) for x in available),'negative_evidence':False,
        'absolute_datum_verified':False,'cross_radar_support':False,
        'donor_counts':donor_counts,
        'funnel':funnel,
        'maximum_height_difference_m':maximum_height_difference_m,
        'maximum_time_seconds':maximum_time_seconds}
