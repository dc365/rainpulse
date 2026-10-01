#!/usr/bin/env python3
"""Explain frozen source-footprint residuals; no reclassification or writes to inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

REASONS = {
    0:'qualified',1:'no_original_source',2:'insufficient_original_support_or_rows',
    3:'native_angular_gap',4:'outside_original_row_or_range_extent',
    5:'original_stencil_barrier',6:'guard_excluded_reference_support',
    7:'insufficient_adjacent_source_boundary_blocks',8:'unstable_original_boundary',
    9:'outside_reference_boundary_or_distance',10:'current_measured_polar_retained',
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots',type=Path)
    parser.add_argument('replays',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('audit output must be new')
    rows=[]
    for path in sorted(args.replays.glob('*.npz')):
        snapshot=args.snapshots/path.name
        with np.load(snapshot,allow_pickle=False) as a,np.load(path,allow_pickle=False) as b:
            meta=json.loads(str(a['METADATA']));replay=json.loads(str(b['METADATA']))
            sha=hashlib.sha256(snapshot.read_bytes()).hexdigest()
            if sha!=replay['input_snapshot_sha256'] or meta['scan_id']!=replay['scan_id']:
                raise ValueError('audit input identity mismatch')
            az=a['AZIMUTH'][:,None];r=a['RANGE'][None,:]
            roi=(r>=250000)&(az>=285)&(az<=340) if meta['radar_id']=='z9591' else (r>=100000)&(az>=160)&(az<=280)
            remaining=a['BEFORE']&~a['ADDED']&roi
            family=b['RV2_RAW_FAN_ID'];source=b['RV2_SOURCE_LEDGER_SEED_ID']
            candidate=b['RV2_SOURCE_FOOTPRINT_CANDIDATE_MASK']==1
            codes=b['RV2_SOURCE_FOOTPRINT_REJECTION_CODE']
            if not np.isin(codes,list(REASONS)).all():raise ValueError('unknown rejection stage')
            counts={name:int((remaining&candidate&(codes==code)).sum()) for code,name in REASONS.items()}
            outside=int((remaining&~candidate).sum())
            if sum(counts.values())+outside!=int(remaining.sum()):raise ValueError('decision partition incomplete')
            parents=[]
            for parent in np.unique(family[remaining&candidate]):
                original=(family==parent)&(source>0)
                rr,cc=np.where(original);target=remaining&candidate&(family==parent)
                kinds=b['RV2_SOURCE_LEDGER_KIND'][original]
                keys,values=np.unique(kinds,return_counts=True)
                raw_rows,raw_cols=np.where(family==parent)
                cols=np.unique(raw_cols)
                outer=[row for row in (int(raw_rows.min())-1,int(raw_rows.max())+1)
                    if 0<=row<len(a['AZIMUTH'])]
                snr=a['MOMENT_SNR']
                measured=(a['AVAILABLE_SNR']==1)&np.isfinite(snr)
                flank_index=np.ix_(outer,cols)
                target_values=snr[target&measured]
                parents.append(dict(parent_id=int(parent),remaining_gates=int(target.sum()),
                    original_gates=len(cc),original_rays=len(np.unique(rr)),
                    original_span_m=float(np.ptp(a['RANGE'][cc])) if len(cc) else None,
                    original_kind_counts={str(k):int(v) for k,v in zip(keys,values)},
                    raw_span_m=float(np.ptp(a['RANGE'][raw_cols])),
                    raw_angular_span_deg=float(a['AZIMUTH'][raw_rows.max()]-a['AZIMUTH'][raw_rows.min()]),
                    target_snr_measured_gates=len(target_values),
                    target_snr_median_db=float(np.median(target_values)) if len(target_values) else None,
                    outer_rows=len(outer),
                    outer_snr_measured_fraction=float(measured[flank_index].mean()) if outer else None,
                    outer_snr_at_most_3db_fraction=float((measured[flank_index]&(snr[flank_index]<=3.)).mean()) if outer else None,
                    decisions={name:int((target&(codes==code)).sum()) for code,name in REASONS.items()}))
            rows.append(dict(case=path.stem,scan_id=meta['scan_id'],snapshot_sha256=sha,
                replay_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                remaining_roi_gates=int(remaining.sum()),outside_candidate=outside,
                decisions=counts,parents=parents))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(scope='offline_residual_decision_audit_not_weather_truth',
        classification_changed=False,product_writes=False,cases=rows),indent=2,allow_nan=False)+'\n')
    for row in rows:
        print(row['case'],json.dumps({k:v for k,v in row['decisions'].items() if v}))


if __name__=='__main__':main()
