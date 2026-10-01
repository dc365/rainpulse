#!/usr/bin/env python3
"""Plot an offline source qualification increment on immutable snapshots.

This does not publish QC/images to the Web. Red marks are proposed increments,
not validated contamination truth or full-Worker results.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots',type=Path);p.add_argument('replays',type=Path)
    p.add_argument('--field',default='RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK')
    p.add_argument('--case',action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    cmap=LinearSegmentedColormap.from_list('radar',['#18a8dc','#04d000','#078800','#ffc000','#ee746d','#d000b4','#b597ee'],N=256)
    receipts=[]
    for case in a.case:
        raw_path=a.snapshots/(case+'.npz');replay_path=a.replays/(case+'.npz')
        with np.load(raw_path,allow_pickle=False) as d:raw={k:d[k] for k in d.files}
        with np.load(replay_path,allow_pickle=False) as d:replay={k:d[k] for k in d.files}
        meta=json.loads(str(raw['METADATA']));check=json.loads(str(replay['METADATA']))
        if check['input_snapshot_sha256']!=hashlib.sha256(raw_path.read_bytes()).hexdigest() or meta['scan_id']!=check['scan_id']:raise ValueError('replay input identity differs')
        qualified=replay[a.field]
        if qualified.shape!=raw['RAW'].shape or not np.isin(qualified,[0,1]).all():raise ValueError('invalid projection mask')
        prior=raw['BEFORE']&~raw['ADDED'];increment=prior&(qualified==1)
        if np.any(increment&((raw['WEATHER']==1)|(raw['CONFLICTS']==1)|(raw['RV2_BARRED_MASK']==1)|~raw['AVAILABLE_DBZH'])):raise ValueError('proposal crossed a known barrier')
        r=raw['RANGE'][None,:]/1000.;az=np.deg2rad(raw['AZIMUTH'])[:,None]
        x,y=r*np.sin(az),r*np.cos(az);full=float(r.max())+10
        zoom=(-460,20,-460,-50) if meta['radar_id']=='z9598' else (-430,-260,120,210)
        fig,axes=plt.subplots(2,3,figsize=(15,9),layout='constrained')
        for axis,bounds in zip(axes,[(-full,full,-full,full),zoom]):
            region=(x>=bounds[0])&(x<=bounds[1])&(y>=bounds[2])&(y<=bounds[3])
            for ax,visible,values,title in zip(axis,[np.isfinite(raw['RAW'])&(raw['RAW']>=5),prior,prior&~increment],
                    [raw['RAW'],raw['QC'],raw['QC']],['Raw','Prior source-stage projection','With source footprint (proposal)']):
                use=visible&region
                im=ax.scatter(x[use],y[use],c=values[use],s=1.,cmap=cmap,vmin=5,vmax=70,rasterized=True)
                ax.set(xlim=bounds[:2],ylim=bounds[2:],aspect='equal',title=title,xlabel='East (km)',ylabel='North (km)');ax.grid(alpha=.2)
            use=increment&region;axis[1].scatter(x[use],y[use],s=12,facecolors='none',edgecolors='#ff2222',linewidths=.7)
        fig.colorbar(im,ax=axes,label='Reflectivity (dBZ)',shrink=.6)
        fig.suptitle(f"{meta['radar_id'].upper()} {meta['local_time']} | sweep {meta['sweep']} | proposed visible increment {int(increment.sum())}\nOffline source-stage comparison; not full QC / published Web. Red rings: proposed increment.")
        out=a.output/(case+'.png');fig.savefig(out,dpi=150);plt.close(fig)
        receipts.append({'case':case,'scan_id':meta['scan_id'],'field':a.field,'scope':'offline_source_projection_not_published_QC',
            'proposed_visible_increment':int(increment.sum()),'snapshot_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            'replay_sha256':hashlib.sha256(replay_path.read_bytes()).hexdigest(),'image_sha256':hashlib.sha256(out.read_bytes()).hexdigest()})
    (a.output/'report.json').write_text(json.dumps(receipts,indent=2)+'\n')
    print(json.dumps(receipts))


if __name__=='__main__':main()
