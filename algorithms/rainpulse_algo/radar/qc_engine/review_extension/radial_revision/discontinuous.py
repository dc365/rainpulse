"""Bounded sparse native-ray objects; gaps are associations, never observations.

The shortest locally supported angular corridor supplies variable boundaries.
Unknown shoulders need two acquired rays each; this is isolation geometry,
not a measured clear-air or physical receiver-source claim.
"""
import numpy as np
from scipy.ndimage import uniform_filter1d
from ..arrays import mask, moment, native_geometry, runs


def detect(native, blocked, *, beam_width=None):
    return _detect(native, blocked, beam_width=beam_width)


def probe(native, blocked, *, beam_width=None, microfragments=False, measured_noise=False,
          maximum_half_beams=2, maximum_width_deg=3.):
    """Read-only factorial probe. Its masks are diagnostics, never QC actions."""
    if not 1<=maximum_half_beams<=6 or not 0<maximum_width_deg<=12:
        raise ValueError('probe width must stay in a bounded native angular domain')
    arrays, report = _detect(native, blocked, beam_width=beam_width,
        minimum_fragment_m=0. if microfragments else 1000., measured_noise=measured_noise, diagnostic=True,
        maximum_half_beams=maximum_half_beams, maximum_width_deg=maximum_width_deg)
    evidence = arrays.pop('RV2_DISCONTINUOUS_MASK')
    out = {key.replace('RV2_DISCONTINUOUS_', 'RV2_SPARSE_PROBE_'):value for key,value in arrays.items()}
    out['RV2_SPARSE_PROBE_EVIDENCE_MASK'] = evidence
    return out, {**report, 'version':'sparse-nomination-probe-v1', 'actions':0,
        'microfragments':bool(microfragments), 'measured_noise':bool(measured_noise),
        'maximum_half_beams':maximum_half_beams,'maximum_width_deg':maximum_width_deg}


def _detect(native, blocked, *, beam_width=None, minimum_fragment_m=1000., measured_noise=False, diagnostic=False,
            maximum_half_beams=2, maximum_width_deg=3.):
    r, az, dr, good, gaps = native_geometry(native)
    z, obs = moment(native, 'DBZH')
    snr, snr_ok = moment(native, 'SNR') if measured_noise else (None, None)
    blocked = mask(blocked, native.shape, 'discontinuous barriers') | ~good[:, None]
    hit = np.zeros(native.shape, bool)
    candidate = hit.copy()
    measured_hit = hit.copy()
    windows = np.zeros(native.shape, 'uint8')
    identity = np.zeros(native.shape, 'uint32')
    evidence = {k: np.full(native.shape, np.nan, 'float32') for k in
                ('LEFT_DEG', 'RIGHT_DEG', 'SUPPORT_M', 'SPAN_M')}
    object_id = 0
    rejection = np.zeros(native.shape, "uint8") if diagnostic else None
    edge_db = np.full(native.shape, np.nan, 'float32')
    window_fractions = {k: np.full(native.shape, np.nan, 'float32') for k in (20,60)}
    for rows in np.split(np.arange(native.shape[0]), np.flatnonzero(gaps[:-1])+1):
        if len(rows) < 5:
            continue
        angles = np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing = float(np.median(np.diff(angles)))
        if spacing <= 0:
            continue
        beam = max(spacing, beam_width or spacing)
        for i in range(2, len(rows)-2):
            row = rows[i]
            target = obs[row] & (z[row] >= 0.) & (r >= 20000.) & ~blocked[row]
            if diagnostic: rejection[row,target]=1
            if not target.any():
                continue
            left = np.full(len(r), np.nan)
            right = left.copy()
            bilateral = np.zeros(len(r), bool)
            edge = np.full(len(r), np.nan)
            # Never cross a protected range corridor, including absent targets.
            corridor_safe = ~blocked[rows[i-2:i+3]].any(axis=0)
            for factor in range(1,maximum_half_beams+1):
                half=factor*beam
                a = np.searchsorted(angles, angles[i]-half+1e-8, side='right')-1
                b = np.searchsorted(angles, angles[i]+half-1e-8, side='left')
                if a < 1 or b >= len(rows)-1 or a >= i or b <= i:
                    continue
                width = max(beam, angles[b]-angles[a])
                if width > maximum_width_deg+1e-6:
                    continue
                safe = ~blocked[rows[a-1:b+2]].any(axis=0)
                corridor_safe &= safe
                # Internal rays must support the same narrow object at that gate.
                interior = (obs[rows[a+1:b]] & (z[rows[a+1:b]] >= z[row]-6.)).mean(axis=0) >= .6
                def measured_shoulder(j, outer):
                    measured = obs[rows[j]] & (z[row]-z[rows[j]] >= 6.)
                    if measured_noise:
                        acquired_noise = (snr_ok[row] & (snr[row]>=7.) &
                            snr_ok[rows[j]] & snr_ok[rows[outer]] &
                            (snr[rows[j]]<=3.) & (snr[rows[outer]]<=3.))
                        measured |= acquired_noise
                    return measured
                def shoulder(j, outer):
                    isolated = ~obs[rows[j]] & ~obs[rows[outer]]
                    return measured_shoulder(j,outer) | isolated
                accept = target & safe & interior & shoulder(a, a-1) & shoulder(b, b+1)
                fresh = accept & ~np.isfinite(left)
                left[fresh], right[fresh] = angles[a], angles[b]
                bilateral[fresh] = (measured_shoulder(a,a-1)[fresh] & measured_shoulder(b,b+1)[fresh])
                measured = fresh & bilateral
                edge[measured] = np.minimum(z[row,measured]-z[rows[a],measured], z[row,measured]-z[rows[b],measured])
            support = target & np.isfinite(left) & corridor_safe
            if diagnostic: rejection[row,support]=2
            for lo, hi in runs(corridor_safe):
                # Clip both scales to the safe corridor. Missing observations
                # stay outside the denominator; they cannot corroborate contrast.
                bits = np.zeros(hi-lo, 'uint8')
                fractions = {}
                actual = support[lo:hi]
                for bit, scale in ((1, 20000.), (2, 60000.)):
                    size = max(3, int(round(scale/dr))) | 1
                    count = uniform_filter1d(actual.astype(float), size, mode='constant')*size
                    measured_count = uniform_filter1d((actual & bilateral[lo:hi]).astype(float), size, mode='constant')*size
                    fractions[int(scale/1000.)] = np.clip(np.divide(measured_count, count, out=np.zeros_like(count), where=count>0), 0., 1.)
                    enough = (count*dr >= 1000.-1e-6) & (measured_count >= .75*count)
                    bits[enough] |= bit
                fragments = [(a+lo, b+lo) for a, b in runs(support[lo:hi]) if (b-a)*dr >= max(dr,minimum_fragment_m)]
                chains = []
                for a, b in fragments:
                    if (not chains or (a-chains[-1][-1][1])*dr > 60000. or
                            abs(left[a]-left[chains[-1][-1][1]-1]) > beam+1e-6 or
                            abs(right[a]-right[chains[-1][-1][1]-1]) > beam+1e-6):
                        chains.append([])
                    chains[-1].append((a, b))
                for chain in chains:
                    gates = np.concatenate([np.arange(a, b) for a, b in chain])
                    if diagnostic: rejection[row,gates]=3
                    if len(chain) < 4:
                        continue
                    total = len(gates)*dr
                    span = r[gates[-1]]-r[gates[0]]+dr
                    width_m = np.maximum(dr, r[gates]*np.deg2rad(np.maximum(beam, right[gates]-left[gates])))
                    checks=((4,total>=8000.),(5,span>=80000.),
                        (6,len(np.unique((r[gates]//20000.).astype(int)))>=4),
                        (7,span/width_m.max()>=12.))
                    failed=False
                    for code,passed in checks:
                        if diagnostic: rejection[row,gates]=code
                        if not passed: failed=True;break
                    if failed: continue
                    if diagnostic: rejection[row,gates]=8
                    object_id += 1
                    candidate[row, gates] = True
                    measured_hit[row, gates] = bilateral[gates]
                    edge_db[row,gates] = edge[gates]
                    for scale in (20,60):
                        window_fractions[scale][row,gates] = fractions[scale][gates-lo]
                    windows[row, gates] = bits[gates-lo]
                    stable = (np.ptp(left[gates]) <= beam+1e-6 and np.ptp(right[gates]) <= beam+1e-6)
                    hit[row, gates] = bilateral[gates] & (bits[gates-lo] == 3) & stable
                    identity[row, gates] = object_id
                    evidence['LEFT_DEG'][row, gates] = left[gates]
                    evidence['RIGHT_DEG'][row, gates] = right[gates]
                    evidence['SUPPORT_M'][row, gates] = total
                    evidence['SPAN_M'][row, gates] = span
    extra={'RV2_DISCONTINUOUS_REJECTION_CODE':rejection} if diagnostic else {}
    return {**extra,'RV2_DISCONTINUOUS_MASK': hit.astype('uint8'),
            'RV2_DISCONTINUOUS_CANDIDATE_MASK': candidate.astype('uint8'),
            'RV2_DISCONTINUOUS_MEASURED_MASK': measured_hit.astype('uint8'),
            'RV2_DISCONTINUOUS_WINDOW_BITS': windows,
            'RV2_DISCONTINUOUS_EDGE_DB': edge_db,
            **{'RV2_DISCONTINUOUS_WINDOW'+str(k)+'_FRACTION': v for k,v in window_fractions.items()},
            'RV2_DISCONTINUOUS_OBJECT_ID': identity,
            **{'RV2_DISCONTINUOUS_'+k: v for k, v in evidence.items()}}, {
                'version': 'discontinuous-native-v2-measured', 'objects': object_id,
                'candidate_gates': int(candidate.sum()),
                'gates': int(hit.sum()), 'filled_gates': 0,
                'source_claim': False, 'recursive_growth': False}
