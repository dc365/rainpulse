#!/usr/bin/env python3
"""Read-only same-volume radio-coordinate source audit; not weather absence."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_s_source_footprint import select_roi


def read(path):
    with np.load(path,allow_pickle=False) as data:
        arrays={key:data[key] for key in data.files}
    return arrays,json.loads(str(arrays['METADATA']))


def seconds(values):
    if np.issubdtype(values.dtype,np.datetime64):
        if np.isnat(values).any():raise ValueError('missing native ray time')
        return values.astype('datetime64[ns]').astype('float64')/1e9
    values=values.astype('float64')
    if not np.isfinite(values).all() or np.any((values<946684800.)|(values>4102444800.)):
        raise ValueError('numeric native ray time must be supported epoch seconds')
    return values


def audit(target_path,donor_paths, *, azimuth=None, range_min=0., range_max=None):
    target,meta=read(target_path)
    tr=target['RANGE'];ta=target['AZIMUTH'];tt=seconds(target['RAY_TIME'])
    obs=target['AVAILABLE_DBZH']==1
    if ta.shape!=tt.shape or obs.shape!=(len(ta),len(tr)):raise ValueError('native target geometry mismatch')
    roi=select_roi(ta,tr,range_min=range_min,range_max=range_max,
        azimuth_start=azimuth[0] if azimuth else None,azimuth_end=azimuth[1] if azimuth else None)
    remaining=target['BEFORE']&~target['ADDED']&roi
    rows=[];used=set();cumulative=np.zeros(obs.shape,'uint8')
    for path in donor_paths:
        donor,dm=read(path)
        if (dm['scan_id']!=meta['scan_id'] or dm['radar_id']!=meta['radar_id'] or
            dm['raw_artifact_sha256']!=meta['raw_artifact_sha256'] or dm['normalized_uri']!=meta['normalized_uri']):
            raise ValueError('donor must share exact normalized volume identity')
        if dm['sweep']==meta['sweep'] or dm['sweep'] in used:raise ValueError('donor cuts must be independent and unique')
        used.add(dm['sweep'])
        da=donor['AZIMUTH'];dr=donor['RANGE'];dt=seconds(donor['RAY_TIME'])
        delta=abs((ta[:,None]-da[None,:]+180.)%360.-180.)
        ray=np.argmin(delta,axis=1);gate=np.argmin(abs(tr[:,None]-dr[None,:]),axis=1)
        beam=min(float(np.median(np.diff(ta))),float(np.median(np.diff(da))))
        ray_ok=(delta[np.arange(len(ta)),ray]<=beam/2.+1e-6)&(abs(tt-dt[ray])<=300.)
        ray_ok&=target['GEOMETRY_GOOD']&donor['GEOMETRY_GOOD'][ray]
        distance_ok=abs(tr-dr[gate])<=min(np.median(np.diff(tr)),np.median(np.diff(dr)))/2.+1e-6
        distance_ok&=(tr>=dr[0])&(tr<=dr[-1])
        native_ok=ray_ok[:,None]&distance_ok[None,:]&obs
        pair=np.ix_(ray,gate)
        native_ok&=donor['AVAILABLE_DBZH'][pair]==1
        original=(donor['RV2_SOURCE_LEDGER_SEED_ID']>0)
        kinds=donor['RV2_SOURCE_LEDGER_KIND']
        flags=donor.get('STORED_QC_FLAGS')
        attrs=dm.get('stored_qc_flags_attributes',{})
        definitions=dm.get('stored_qc_flag_definitions',attrs.get('flag_definitions',{}))
        # Count only explicitly named typed causes. Do not guess flag bits.
        radial_bit=definitions.get('RADIAL_INTERFERENCE')
        if radial_bit is not None and not isinstance(radial_bit,int):
            raise ValueError('unsupported radial flag definition shape')
        stored_radial=np.zeros(donor['RAW'].shape,bool) if flags is None or radial_bit is None else (flags&radial_bit)!=0
        masks={'original_revision_source':original,
            'original_receiver_or_coherent_line':original&((kinds&3)!=0),
            'stored_typed_radial_interference':stored_radial}
        for name in ('VOR_SOURCE_MATCH_MASK','VOR_SOURCE_CORROBORATED_MASK','RDR_SOURCE_MASK','RFI_QUARANTINE_MASK'):
            value=donor.get('STORED_'+name)
            if value is not None:
                if not np.isin(value,[0,1]).all():raise ValueError('invalid stored source mask')
                masks[name]=value==1
        counts={name:int((remaining&native_ok&value[pair]).sum()) for name,value in masks.items()}
        if radial_bit is None or flags is None:counts['stored_typed_radial_interference']=None
        cumulative+=(native_ok&stored_radial[pair]).astype('uint8')
        rows.append(dict(path=str(path.resolve()),snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            sweep=dm['sweep'],elevation_deg=dm['elevation_deg'],profile_sha256=dm['profile_sha256'],
            native_coverage_remaining_gates=int((remaining&ray_ok[:,None]&distance_ok[None,:]).sum()),
            native_range_start_m=float(dr[0]),native_range_end_m=float(dr[-1]),
            native_measured_remaining_gates=int((remaining&native_ok).sum()),
            matched_ray_max_time_difference_s=float(abs(tt-dt[ray])[ray_ok].max()) if ray_ok.any() else None,
            mapped_source_counts=counts,original_source_total=int(original.sum()),
            typed_radial_flag_total=int(stored_radial.sum()) if radial_bit is not None and flags is not None else None,radial_flag_definition_available=radial_bit is not None))
    return dict(scope='same_volume_radio_coordinate_source_audit_not_weather_absence',
        product_writes=False,actions=0,independent_weather_truth=False,
        selection={'azimuth':azimuth,'range_min':range_min,'range_max':range_max},
        target_snapshot_sha256=hashlib.sha256(target_path.read_bytes()).hexdigest(),
        normalized_volume_sha256=meta['raw_artifact_sha256'],scan_id=meta['scan_id'],
        remaining_roi_gates=int(remaining.sum()),donors=rows,
        remaining_supported_by_two_typed_radial_cuts=int((remaining&(cumulative>=2)).sum()) if sum(row['radial_flag_definition_available'] for row in rows)>=2 else None)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('target',type=Path);p.add_argument('donors',nargs='+',type=Path)
    p.add_argument('--azimuth',type=float,nargs=2)
    p.add_argument('--range-min',type=float,default=0.);p.add_argument('--range-max',type=float)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    if a.output.exists():raise ValueError('output must be new')
    result=audit(a.target,a.donors,azimuth=a.azimuth,range_min=a.range_min,range_max=a.range_max)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
