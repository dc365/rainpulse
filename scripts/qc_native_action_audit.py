#!/usr/bin/env python3
"""Offline native-score/action association. Scores are not calibrated truth."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import zarr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def analyse(q, e1, e2):
    action=q['QC_ACTION'][:]
    if e1.shape != action.shape or e2.shape != action.shape:
        raise ValueError('native/action geometry mismatch')
    score=np.fmax(e1,e2)
    raw=q['DBZH_RAW'][:]
    observed=(q['VALID_MASK'][:]==1) & np.isfinite(raw)
    available=observed & np.isfinite(score)
    rho=q['RHOHV_RAW'][:] if 'RHOHV_RAW' in q else np.full(raw.shape,np.nan)
    snr=q['SNR_RAW'][:] if 'SNR_RAW' in q else np.full(raw.shape,np.nan)
    quarantine=q['RFI_QUARANTINE_MASK'][:]==1
    trust=q['RHOHV_TRUST_MASK'][:]==1 if 'RHOHV_TRUST_MASK' in q else np.zeros(raw.shape,bool)
    # Exploratory review proposal only. No automatic QC action or eligibility change.
    review=available & (score>=.5) & (action==0) & ~quarantine & trust & (rho<.9) & (snr>=8)
    groups={'keep':observed & (action==0), 'quarantine':observed & quarantine,
            'reject':observed & (action==2), 'other_downweight':observed & (action==1) & ~quarantine}
    stats={}
    for name,mask in groups.items():
        values=score[mask & available]
        stats[name]={'gates':int(mask.sum()),'scored':int(values.size),
                     'score_quantiles':np.quantile(values,[0,.5,.9,.99,1]).tolist() if values.size else None,
                     'threshold_counts':{str(t):int((values>=t).sum())for t in [.25,.5,.75]}}
    return {'groups':stats,'observed':int(observed.sum()),'scored':int(available.sum()),
            'review_only_gates':int(review.sum()),'operational_eligible':False}, score, review


def main():
    p=argparse.ArgumentParser();p.add_argument('frozen');p.add_argument('native');p.add_argument('output');args=p.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True);reports=[]
    for folder in sorted(Path(args.native).iterdir()):
        if not (folder/'native.npz').exists():continue
        source=Path(args.frozen)/folder.name
        try:
            root=zarr.open_group(str(source/'qc.zarr'),mode='r')
            if str(root.attrs['scan_id'])!=folder.name:raise ValueError('scan mismatch')
            receipt=json.loads((folder/'summary.json').read_text())
            if receipt['scan_id']!=folder.name:raise ValueError('native receipt mismatch')
            with np.load(folder/'native.npz',allow_pickle=False) as n:
                report,score,review=analyse(root['sweep_000'],n['emitter1'],n['emitter2'])
            report.update(scan_id=folder.name,radar_id=root.attrs['radar_id'],
                native_sha256=hashlib.sha256((folder/'native.npz').read_bytes()).hexdigest(),
                qc_case_sha256=hashlib.sha256((source/'case.json').read_bytes()).hexdigest(),
                native_status=receipt['status'])
            q=root['sweep_000'];fig,axes=plt.subplots(2,2,figsize=(14,8),constrained_layout=True)
            panels=[(q['DBZH_RAW'][:],'Raw DBZH',-10,70),
                    (q['QC_ACTION'][:],'V7 action: 0 keep / 1 downweight / 2 reject / 3 missing',0,3),
                    (score,'Mapped native score; white = unavailable',0,1),
                    (np.where(np.isfinite(score),review.astype(float),np.nan),'Review suggestion only; NOT published QC',0,1)]
            for ax,(data,title,lo,hi) in zip(axes.flat,panels):
                im=ax.imshow(data,aspect='auto',origin='lower',vmin=lo,vmax=hi,cmap='viridis');ax.set_title(title);ax.set_xlabel('Original gate');ax.set_ylabel('Original ray');fig.colorbar(im,ax=ax)
            fig.suptitle(str(root.attrs['radar_id'])+' / '+folder.name)
            fig.savefig(out/(folder.name+'.png'),dpi=110);plt.close(fig)
            np.savez_compressed(out/(folder.name+'-review.npz'),review_only=review)
            reports.append(report);print(folder.name,report['review_only_gates'],flush=True)
        except Exception as e:reports.append({'scan_id':folder.name,'error':str(e)})
        temp=out/'summary.tmp';temp.write_text(json.dumps(reports,indent=2));temp.replace(out/'summary.json')

if __name__=='__main__':main()
