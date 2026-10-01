#!/usr/bin/env python3
"""Audit causal native-ray recurrence; recurrence alone never authorizes QC."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from audit_s_source_footprint import select_roi
from audit_s_volume_sources import seconds


def read(path):
    with np.load(path, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}
    return arrays, json.loads(str(arrays['METADATA']))


def mapping(target, past, *, max_age_seconds=1800.):
    """Half-native-bin matching, no extrapolation; missing moments stay unknown."""
    if max_age_seconds <= 0:
        raise ValueError('positive maximum age required')
    ta, pa = target['AZIMUTH'], past['AZIMUTH']
    tr, pr = target['RANGE'], past['RANGE']
    if min(len(ta), len(pa), len(tr), len(pr)) < 2:
        raise ValueError('insufficient native geometry')
    if any(not np.isfinite(v).all() for v in (ta, pa, tr, pr)):
        raise ValueError('nonfinite native coordinates')
    angular = [np.diff(np.rad2deg(np.unwrap(np.deg2rad(a)))) for a in (ta, pa)]
    radial = [np.diff(r) for r in (tr, pr)]
    if any((d <= 0).any() for d in angular + radial):
        raise ValueError('native coordinates must be ordered without duplicates')
    delta = abs((ta[:, None]-pa[None, :]+180.) % 360.-180.)
    ray = delta.argmin(axis=1)
    at = np.searchsorted(pr, tr)
    lo, hi = np.clip(at-1, 0, len(pr)-1), np.clip(at, 0, len(pr)-1)
    gate = np.where(abs(tr-pr[lo]) <= abs(tr-pr[hi]), lo, hi)
    age = seconds(target['RAY_TIME'])-seconds(past['RAY_TIME'])[ray]
    ray_ok = (delta[np.arange(len(ta)), ray] <= min(np.median(d) for d in angular)/2.+1e-6)
    ray_ok &= (age > 0.) & (age <= max_age_seconds)
    ray_ok &= target['GEOMETRY_GOOD'].astype(bool) & past['GEOMETRY_GOOD'][ray].astype(bool)
    ray_ok &= abs(target['ELEVATION']-past['ELEVATION'][ray]) <= .1
    # Reject both sides of native acquisition gaps rather than bridging them.
    for arrays, mapped, keep in ((target, np.arange(len(ta)), ray_ok), (past, ray, ray_ok)):
        gaps = arrays['GAP_AFTER'].astype(bool)
        if gaps.shape != arrays['AZIMUTH'].shape:
            raise ValueError('invalid native gap identity')
        unsafe = gaps | np.r_[False, gaps[:-1]]
        keep &= ~unsafe[mapped]
    gate_ok = (tr >= pr[0]) & (tr <= pr[-1])
    gate_ok &= abs(tr-pr[gate]) <= min(np.median(d) for d in radial)/2.+1e-6
    return np.ix_(ray, gate), ray_ok[:, None] & gate_ok[None, :], age


def audit(target_path, references, *, azimuth=None, range_min=0., max_age_seconds=1800.):
    target, meta = read(target_path)
    shape = target['RAW'].shape
    roi = select_roi(target['AZIMUTH'], target['RANGE'], range_min=range_min,
                     azimuth_start=azimuth[0] if azimuth else None,
                     azimuth_end=azimuth[1] if azimuth else None)
    remaining = target['BEFORE'].astype(bool) & ~target['ADDED'].astype(bool) & roi
    target_barred = (target['WEATHER']==1) | (target['CONFLICTS']==1) | (target['RV2_BARRED_MASK']==1)
    votes = {name: np.zeros(shape, 'uint16') for name in
             ('native_coverage', 'observed_echo', 'observed_snr', 'stable_snr', 'typed_radial')}
    records, seen = [], {meta['raw_artifact_sha256']}
    for path in references:
        past, pm = read(path)
        if pm['radar_id'] != meta['radar_id'] or pm['sweep'] != meta['sweep']:
            raise ValueError('reference must share radar and explicit native cut')
        if pm['scan_id'] == meta['scan_id'] or pm['raw_artifact_sha256'] in seen:
            raise ValueError('reference must be an independent raw volume')
        seen.add(pm['raw_artifact_sha256'])
        pair, covered, age = mapping(target, past, max_age_seconds=max_age_seconds)
        # A future volume cannot masquerade as a partly missing past reference.
        if not (age > 0.).all():
            raise ValueError('reference is not strictly past')
        past_barred = (past['WEATHER']==1) | (past['CONFLICTS']==1) | (past['RV2_BARRED_MASK']==1)
        covered &= ~target_barred & ~past_barred[pair]
        echo = covered & (target['AVAILABLE_DBZH']==1) & (past['AVAILABLE_DBZH'][pair]==1)
        echo &= np.isfinite(target['RAW']) & np.isfinite(past['RAW'][pair])
        snr = covered & (target.get('AVAILABLE_SNR', np.zeros(shape))==1)
        snr &= past.get('AVAILABLE_SNR', np.zeros(past['RAW'].shape))[pair]==1
        tv = target.get('MOMENT_SNR', np.full(shape, np.nan))
        pv = past.get('MOMENT_SNR', np.full(past['RAW'].shape, np.nan))[pair]
        snr &= np.isfinite(tv) & np.isfinite(pv)
        stable = echo & snr & (abs(tv-pv) <= 2.5)
        definitions = pm.get('stored_qc_flag_definitions', {})
        bit = definitions.get('RADIAL_INTERFERENCE')
        flags = past.get('STORED_QC_FLAGS')
        known = isinstance(bit, int) and bit > 0 and flags is not None
        typed = echo & ((flags[pair] & bit) != 0) if known else np.zeros(shape, bool)
        counts = {}
        for name, measured in zip(votes, (covered, echo, snr, stable, typed)):
            votes[name] += measured.astype('uint16')
            counts[name] = int((remaining & measured).sum())
        records.append(dict(scan_id=pm['scan_id'], raw_sha256=pm['raw_artifact_sha256'],
            snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            profile_sha256=pm.get('profile_sha256'),
            flag_definitions_sha256=pm.get('stored_qc_flag_definitions_sha256'),
            minimum_age_seconds=float(age.min()), maximum_age_seconds=float(age.max()),
            typed_radial_definition_available=known, remaining_counts=counts))
    rho = target.get('MOMENT_RHOHV', np.full(shape, np.nan))
    snr = target.get('MOMENT_SNR', np.full(shape, np.nan))
    weather_like = ((target.get('AVAILABLE_RHOHV', np.zeros(shape))==1) & np.isfinite(rho) &
                    (target.get('AVAILABLE_SNR', np.zeros(shape))==1) & np.isfinite(snr) &
                    (rho >= .95) & (snr >= 10.))
    parents = target.get('RV2_RAW_FAN_ID', np.zeros(shape, 'uint32'))
    groups = []
    for identity in np.unique(parents[remaining]):
        use = remaining & (parents == identity)
        groups.append(dict(parent_id=int(identity), remaining_gates=int(use.sum()),
            weather_like_gates=int((use & weather_like).sum()),
            recurrence={name:{str(n):int((use & (v == n)).sum()) for n in np.unique(v[use])}
                        for name,v in votes.items()}))
    return dict(scope='causal_native_recurrence_not_contamination_truth', actions=0,
        product_writes=False, source_claim=False, independent_weather_truth=False,
        target_scan_id=meta['scan_id'], target_raw_sha256=meta['raw_artifact_sha256'],
        target_snapshot_sha256=hashlib.sha256(target_path.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        target_web_identity_present=bool(meta.get('web_frame_identity')),
        remaining_gates=int(remaining.sum()), references=records, parents=groups,
        selection=dict(azimuth=azimuth, range_min=range_min, max_age_seconds=max_age_seconds,
                       stable_snr_tolerance_db=2.5, eligibility_enabled=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('target', type=Path)
    p.add_argument('references', nargs='+', type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--azimuth', type=float, nargs=2)
    p.add_argument('--range-min', type=float, default=0.)
    p.add_argument('--max-age-seconds', type=float, default=1800.)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('new audit output required')
    result = audit(args.target, args.references, azimuth=args.azimuth,
                   range_min=args.range_min, max_age_seconds=args.max_age_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ('target_scan_id','remaining_gates','references')},indent=2))


if __name__ == '__main__':
    main()
