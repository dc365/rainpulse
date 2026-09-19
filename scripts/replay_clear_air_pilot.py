"""Offline retrospective projection only; never publish a QC or CR asset."""
import argparse
import json
from pathlib import Path
import numpy as np
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment
from rainpulse_algo.diagnostics.renderer import _open_group


def replay(inputs, backgrounds, output):
    output.mkdir(parents=True,exist_ok=True)
    reader=ArtifactObjectReader(minio_client_from_environment())
    results=[]
    for label,uri in json.loads(inputs.read_text()):
        station=label.split()[0].upper()
        if station not in ('Z9591','Z9598'):
            continue
        bg=np.load(backgrounds/(station+'.npz'),allow_pickle=False)
        root=_open_group(reader.load(uri))
        before=np.full((360,75),-np.inf);after=before.copy()
        counts=dict(case=label,added=0,eligible_before=0,weather_protected_changed=0,strong_echo_changed=0,sweeps=[])
        for sn in root['sweep_number'][:]:
            g=root[f'sweep_{sn:03d}'];key=f'{int(sn)+1:03d}'
            required=['DBZH_RAW','RHOHV_RAW','ZDR_RAW','SNR_RAW','DBZH_QC','REFLECTIVITY_TRUST_MASK','NP_WEATHER_PROTECTED_MASK']
            if any(k not in g for k in required) or key+'_mean' not in bg:
                continue
            if abs(float(np.median(g['elevation'][:]))-float(bg[key+'_elevation']))>.2:
                raise ValueError('background/target elevation mismatch')
            z=g['DBZH_RAW'][:];qc=g['DBZH_QC'][:]
            az=g['azimuth'][:];r=g['range'][:]
            ai=np.rint(az).astype(int)%360
            ri=np.clip(np.floor(r/1000).astype(int),0,74)
            mean=bg[key+'_mean'][ai[:,None],ri[None,:]]
            freq=bg[key+'_recurrence_lower'][ai[:,None],ri[None,:]]
            n=bg[key+'_observed_count'][ai[:,None],ri[None,:]]
            rho=g['RHOHV_RAW'][:];zdr=g['ZDR_RAW'][:];snr=g['SNR_RAW'][:]
            protected=g['NP_WEATHER_PROTECTED_MASK'][:]==1
            usable=(g['REFLECTIVITY_TRUST_MASK'][:]==1)&np.isfinite(qc)
            near=np.broadcast_to((r>=0)&(r<75000),z.shape)
            measured=np.isfinite(z)&np.isfinite(rho)&np.isfinite(zdr)&np.isfinite(snr)
            pol=measured&(rho>=0)&(rho<=.85)&(snr>=8)&((zdr < -1)|(zdr>4))&(abs(zdr)<=10)
            persistent=(freq>=.8)&(n>=20)&np.isfinite(mean)
            # No subtraction of background power, interpolation or automatic no-rain.
            remove=usable&near&persistent&pol&(z<=30)&(z<=mean+6)&~protected
            counts['added']+=int(remove.sum());counts['eligible_before']+=int((usable&near).sum())
            counts['weather_protected_changed']+=int((remove&protected).sum())
            counts['strong_echo_changed']+=int((remove&(z>=35)).sum())
            counts['sweeps'].append(dict(sweep=int(sn),added=int(remove.sum()),persistent=int((usable&near&persistent).sum())))
            ia=np.broadcast_to(ai[:,None],z.shape);ir=np.broadcast_to(ri[None,:],z.shape)
            chosen=usable&near
            np.maximum.at(before,(ia[chosen],ir[chosen]),qc[chosen])
            chosen &= ~remove
            np.maximum.at(after,(ia[chosen],ir[chosen]),qc[chosen])
        counts['projection_cells_lost']=int((np.isfinite(before)&~np.isfinite(after)).sum())
        counts['projection_cells_changed']=int((before!=after).sum())
        results.append(counts);print(json.dumps(counts),flush=True)
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        theta=np.deg2rad(np.arange(360));ranges=np.arange(.5,75,1)
        x=np.sin(theta[:,None])*ranges;y=np.cos(theta[:,None])*ranges
        fig,axes=plt.subplots(1,2,figsize=(10,4.5),layout='constrained')
        for ax,a,title in zip(axes,[before,after],['Current QC','Offline background candidate']):
            valid=np.isfinite(a);im=ax.scatter(x[valid],y[valid],c=a[valid],s=2,cmap='turbo',vmin=0,vmax=50)
            ax.set(title=title,xlabel='East (km)',ylabel='North (km)',xlim=(-75,75),ylim=(-75,75));ax.set_aspect('equal')
        fig.colorbar(im,ax=axes,label='dBZ');fig.suptitle(label+' | near-site max over available sweeps')
        fig.savefig(output/(label.replace(' ','_').replace(':','-')+'.png'),dpi=140);plt.close(fig)
    (output/'results.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('backgrounds',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();replay(a.inputs,a.backgrounds,a.output)
