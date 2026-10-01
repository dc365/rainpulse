"""RAW bounded short-fragment families. Nomination only, never source/action.

A family's first original fragment fixes its angular search and maximum extent.
Association does not create observed gates or grow from derived remnants.
"""
from enum import IntFlag
import numpy as np
from scipy.ndimage import uniform_filter1d
from ..arrays import mask, moment, native_geometry, runs
from .source_envelope import _corridor

PREFIX = 'RV2_RAW_FAMILY_'
FLOATS = ('LEFT_DEG', 'RIGHT_DEG', 'START_M', 'END_M', 'SUPPORT_M', 'SPAN_M',
          'WIDTH_M', 'FRAGMENT_M', 'ORIGINAL_FRAGMENT_M', 'WINDOW20_FRACTION', 'WINDOW60_FRACTION',
          'REFERENCE_AZ_DEG', 'BEAM_DEG')


class Hold(IntFlag):
    NO_INDEPENDENT_EVIDENCE = 1
    SUPPORT = 2
    SPAN = 4
    BOUNDARY = 8
    BILATERAL_WINDOWS = 16


def empty(shape):
    return {**{PREFIX+k: np.zeros(shape, dt) for k,dt in
               (('MASK','uint8'), ('WINDOW_BITS','uint8'), ('BEAM_PROXY_MASK','uint8'),
                ('ID','uint32'), ('HOLD_REASON','uint16'))},
            **{PREFIX+k: np.full(shape, np.nan, 'float32') for k in FLOATS}}


def detect(native, blocked, *, beam_width=None, maximum_fragments=100000):
    r, az, dr, good, gaps = native_geometry(native)
    z, obs = moment(native, 'DBZH')
    barred = mask(blocked, native.shape, 'raw family barriers') | ~good[:, None]
    out = empty(native.shape)
    records = []
    ident = 0
    count = 0
    for rows in np.split(np.arange(native.shape[0]), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 5:
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        if spacing <= 0:
            continue
        beam = max(spacing, beam_width or spacing)
        cache = {}
        original_lengths = {}
        fragments = []
        for i in range(2, len(rows)-2):
            row = rows[i]
            if not (obs[row] & ~barred[row] & (z[row]>=0.)).any():
                continue
            bounded, left, right, safe = _corridor(rows, angles, i, beam, z, obs, barred, r)
            # Only measured flank contrasts enter window evidence; missing is
            # permitted for nomination but supplies no clear-air vote.
            measured = np.zeros(len(r), bool)
            gates = np.flatnonzero(bounded)
            a = np.searchsorted(angles,left[gates])
            b = np.searchsorted(angles,right[gates])
            measured[gates] = (obs[rows[a],gates] & obs[rows[b],gates] &
                               (z[row,gates]-z[rows[a],gates]>=6.) &
                               (z[row,gates]-z[rows[b],gates]>=6.))
            bits = np.zeros(len(r), 'uint8')
            fractions = {k:np.zeros(len(r)) for k in (20,60)}
            for lo,hi in runs(safe):
                actual = bounded[lo:hi].astype(float)
                for bit,scale in ((1,20),(2,60)):
                    size = max(3, int(round(scale*1000./dr))) | 1
                    total = uniform_filter1d(actual,size,mode='constant')*size
                    contrast = uniform_filter1d((bounded[lo:hi]&measured[lo:hi]).astype(float),size,mode='constant')*size
                    frac = np.clip(np.divide(contrast,total,out=np.zeros_like(total),where=total>0),0.,1.)
                    fractions[scale][lo:hi] = frac
                    bits[lo:hi][(total*dr>=1000.-1e-6)&(frac>=.75)] |= bit
            cache[i] = (left,right,safe,bits,fractions)
            original_lengths[i] = np.full(len(r),np.nan)
            tile = max(1,int(np.floor(180000./dr)))
            for lo,hi in runs(bounded):
                original_lengths[i][lo:hi] = (hi-lo)*dr
                for start in range(lo,hi,tile):
                    fragments.append((start,min(hi,start+tile),i))
        count += len(fragments)
        if count > maximum_fragments:
            return empty(native.shape), {'version':'raw-families-v1','status':'resource_abstained',
                                        'objects':0,'action_gates':0,'filled_gates':0,
                                        'limit':maximum_fragments}
        fragments = sorted(fragments)
        by_ray = {}
        for index,(lo,hi,i) in enumerate(fragments):
            by_ray.setdefault(i,[]).append(index)
        starts = {i:np.array([fragments[k][0] for k in indices]) for i,indices in by_ray.items()}
        used = np.zeros(len(fragments),bool)
        for index,first in enumerate(fragments):
            if used[index]:continue
            used[index] = True
            lo,hi,i = first
            ll,rr,safe,_,_ = cache[i]
            ref_left,ref_right = float(np.median(ll[lo:hi])),float(np.median(rr[lo:hi]))
            members = [first]
            end = hi
            # Index by native ray and fixed first-fragment range. No quadratic
            # whole-sweep graph, and no traversal initiated by new members.
            choices = []
            for j,indices in by_ray.items():
                if abs(angles[j]-angles[i])>beam+1e-6:continue
                first_pos = np.searchsorted(starts[j],lo,side='left')
                last_pos = np.searchsorted(starts[j],lo+int(180000./dr),side='right')
                choices.extend(k for k in indices[first_pos:last_pos] if not used[k])
            for k in sorted(choices):
                a,b,j = fragments[k]
                if (a-end)*dr>60000. or (max(end,b)-lo)*dr>180000.+1e-6:continue
                lj,rj,sj,_,_ = cache[j]
                bound = (abs(float(np.median(lj[a:b]))-ref_left)<=beam+1e-6 and
                         abs(float(np.median(rj[a:b]))-ref_right)<=beam+1e-6)
                if not bound:continue
                # All gaps must remain safe across both original corridors.
                if not np.all(safe[lo:max(end,b)] & sj[lo:max(end,b)]):continue
                members.append((a,b,j));end=max(end,b)
                safe = safe & sj
                used[k] = True
            ident += 1
            support_ranges = np.zeros(len(r),bool)
            left_values,right_values = [],[]
            for a,b,j in members:
                support_ranges[a:b] = True
                left_values.extend(cache[j][0][a:b]);right_values.extend(cache[j][1][a:b])
            support = float(support_ranges.sum()*dr)
            span = float((end-lo)*dr)
            stable = np.ptp(left_values)<=beam+1e-6 and np.ptp(right_values)<=beam+1e-6
            hold = int(Hold.NO_INDEPENDENT_EVIDENCE)
            if support<8000.:hold |= int(Hold.SUPPORT)
            if span<80000.:hold |= int(Hold.SPAN)
            if not stable:hold |= int(Hold.BOUNDARY)
            for a,b,j in members:
                row = rows[j];left,right,_,bits,frac = cache[j]
                gates = np.arange(a,b)
                out[PREFIX+'MASK'][row,gates] = 1
                out[PREFIX+'ID'][row,gates] = ident
                out[PREFIX+'WINDOW_BITS'][row,gates] = bits[gates]
                out[PREFIX+'BEAM_PROXY_MASK'][row,gates] = int(beam_width is None)
                out[PREFIX+'HOLD_REASON'][row,gates] = hold | ((bits[gates]!=3).astype('uint16')*int(Hold.BILATERAL_WINDOWS))
                values = {'LEFT_DEG':left[gates], 'RIGHT_DEG':right[gates],
                          'START_M':r[lo], 'END_M':r[end-1]+dr, 'SUPPORT_M':support,
                          'SPAN_M':span, 'WIDTH_M':np.maximum(dr,r[gates]*np.deg2rad(right[gates]-left[gates])),
                          'FRAGMENT_M':(b-a)*dr, 'ORIGINAL_FRAGMENT_M':original_lengths[j][gates], 'WINDOW20_FRACTION':frac[20][gates],
                          'WINDOW60_FRACTION':frac[60][gates], 'REFERENCE_AZ_DEG':angles[i], 'BEAM_DEG':beam}
                for key,value in values.items():out[PREFIX+key][row,gates] = value
            records.append({'id':ident,'fragments':len(members),'support_m':support,
                            'span_m':span,'reference_az_deg':float(angles[i]),'hold_reason':hold,
                            'start_m':float(r[lo]),'end_m':float(r[end-1]+dr)})
    return out, {'version':'raw-families-v1','status':'nomination_only','objects':ident,
                 'fragments':count,'gates':int(out[PREFIX+'MASK'].sum()),'action_gates':0,
                 'filled_gates':0,'recursive_growth':False,'maximum_extent_m':180000.,
                 'beam_metadata_available':beam_width is not None,'records':records}


def validate(group, observed, barred):
    """Reject forged nomination evidence without assigning it action eligibility."""
    get = lambda k:np.asarray(group[PREFIX+k][:])
    shape = observed.shape
    for key,dt in (('MASK','uint8'),('WINDOW_BITS','uint8'),('BEAM_PROXY_MASK','uint8'),('ID','uint32'),('HOLD_REASON','uint16')):
        if get(key).shape!=shape or get(key).dtype!=np.dtype(dt):raise ValueError('invalid raw family dtype/shape')
    candidate = mask(get('MASK'),shape,'raw family nomination')
    proxy = mask(get('BEAM_PROXY_MASK'),shape,'raw family beam proxy')
    if np.any(candidate & (~observed|barred)) or np.any(proxy&~candidate):raise ValueError('raw family crossed observation/barrier')
    if not np.array_equal(get('ID')>0,candidate):raise ValueError('raw family lacks identity')
    for key in FLOATS:
        value = get(key)
        if value.shape!=shape or value.dtype!=np.dtype('float32') or not np.array_equal(np.isfinite(value),candidate):
            raise ValueError('invalid raw family evidence')
    if (np.any(candidate & ((get('RIGHT_DEG')<=get('LEFT_DEG'))|(get('RIGHT_DEG')-get('LEFT_DEG')>8.00001)|
                           (get('SPAN_M')>180000.01)|(get('SPAN_M')<=0)|(get('SUPPORT_M')>get('SPAN_M')+.01)|
                           (get('FRAGMENT_M')<=0)|(get('FRAGMENT_M')>get('SUPPORT_M')+.01)|
                           (get('ORIGINAL_FRAGMENT_M')<get('FRAGMENT_M'))|(get('WIDTH_M')<=0)|(get('BEAM_DEG')<=0)|
                           (~np.isclose(get('END_M')-get('START_M'),get('SPAN_M'),rtol=1e-5,atol=.05))))):
        raise ValueError('raw family lacks bounded extent')
    if (np.any(candidate & ((get('HOLD_REASON')&1)==0)) or np.any(get('WINDOW_BITS')>3) or
            np.any(get('HOLD_REASON')>31) or np.any((~candidate)&((get('WINDOW_BITS')!=0)|(get('HOLD_REASON')!=0)))):raise ValueError('raw family lacks review hold')
    for scale in (20,60):
        value = get('WINDOW'+str(scale)+'_FRACTION')
        if np.any(candidate & ((value<0)|(value>1))):raise ValueError('invalid raw family window')
