#!/usr/bin/env python3
"""Diagnose saved --diagnostics snapshots; no algorithm or product mutations."""
import argparse,json
from pathlib import Path
import numpy as np
from scipy.ndimage import label


def runs(row):
    d=np.diff(np.r_[False,row,False].astype('int8'))
    return list(zip(np.flatnonzero(d==1),np.flatnonzero(d==-1)))


def audit(path):
    with np.load(path,allow_pickle=False) as z:a={k:z[k] for k in z.files}
    m=json.loads(str(a['METADATA']));r=a['RANGE'];az=a['AZIMUTH'];dr=float(np.median(np.diff(r)))
    roi=(r[None,:]>=250000)&(az[:,None]>=285)&(az[:,None]<=340) if m['radar_id']=='z9591' else (r[None,:]>=100000)&(az[:,None]>=160)&(az[:,None]<=280)
    left=a['BEFORE']&~a['ADDED']&roi
    counts={k:int((left&(a[k]>0)).sum()) for k in a if k.startswith('RV2_') and (k.endswith('_MASK') or k in ('RV2_REASON',)) and a[k].shape==left.shape}
    perray=[]
    for i in np.flatnonzero(left.any(axis=1)):
        raw=a['AVAILABLE_DBZH'][i]&(a['RAW'][i]>=0)&roi[i]
        pieces=runs(raw);lengths=[(b-c)*dr for c,b in pieces]
        perray.append(dict(ray=int(i),azimuth_deg=round(float(az[i]),3),remaining=int(left[i].sum()),raw_support_m=int(raw.sum()*dr),fragments=len(pieces),fragments_ge_1km=int(sum(x>=1000 for x in lengths)),max_fragment_m=int(max(lengths,default=0)),span_m=float(np.ptp(r[raw])+dr) if raw.any() else 0))
    labels,n=label(left,np.ones((3,3)));objects=[]
    for k in range(1,n+1):
        rr,gg=np.where(labels==k)
        if len(rr)<2:continue
        objects.append({'gates':len(rr),'range_span_m':float(np.ptp(r[gg])+dr),'angular_width_deg':float(np.ptp(az[rr])),'range_min_m':float(r[gg].min()),'range_max_m':float(r[gg].max())})
    moments={}
    for name in ('SNR','RHOHV','ZDR','PHIDP','VR','SW'):
        x=a.get('MOMENT_'+name);valid=a.get('AVAILABLE_'+name)
        if x is None: moments[name]={'available':0};continue
        use=left&valid&np.isfinite(x);v=x[use]
        moments[name]={'available':int(use.sum()),'quantiles_10_50_90':np.quantile(v,[.1,.5,.9]).round(4).tolist() if len(v) else []}
    held=left&a['HELD']
    raw_envelope=a['RV2_ENVELOPE_RAW_OBJECT_ID']>0
    frozen_seed=a['RV2_ENVELOPE_SEED_ID']>0
    row={'case':path.stem,'remaining_sector':int(left.sum()),'old_visible_sector':int((a['BEFORE']&roi).sum()),'added_sector':int((a['ADDED']&roi).sum()),
        'within_accepted_raw_envelope':int((left&raw_envelope).sum()),'total_raw_envelope_objects':len(np.unique(a['RV2_ENVELOPE_RAW_OBJECT_ID'][raw_envelope])),
        'total_accepted_seed_gates':int(frozen_seed.sum()),'legacy_seed_remaining':int((left&(a['SEED']==1)).sum()),'protected_remaining':int((left&((a['WEATHER']==1)|(a['CONFLICTS']==1))).sum()),
        'overlap_counts':counts,'per_ray_raw_support':perray,'remaining_components':sorted(objects,key=lambda x:x['gates'],reverse=True),'moments':moments,
        'held_gates':[{'az':float(az[i]),'range_m':float(r[j]),'edge_db':float(a['RV2_DISCONTINUOUS_EDGE_DB'][i,j]) if np.isfinite(a['RV2_DISCONTINUOUS_EDGE_DB'][i,j]) else None,'window20':float(a['RV2_DISCONTINUOUS_WINDOW20_FRACTION'][i,j]),'window60':float(a['RV2_DISCONTINUOUS_WINDOW60_FRACTION'][i,j])} for i,j in zip(*np.where(held))]}
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args()
    rows=[audit(x) for x in sorted(a.directory.glob('*.npz'))]
    (a.directory/'residual-audit.json').write_text(json.dumps(rows,indent=2,allow_nan=False)+'\n')
    for row in rows:print(json.dumps({k:row[k] for k in ('case','remaining_sector','old_visible_sector','added_sector','within_accepted_raw_envelope','total_raw_envelope_objects','protected_remaining','moments')}))
if __name__=='__main__':main()
