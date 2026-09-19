"""Summarize actual composite changes within 75 km of each target station."""
import argparse,json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args();rows=[]
for path in sorted(args.directory.glob('*.npz')):
    a=np.load(path);before=a['before'];after=a['after'];west,south,east,north=a['bounds'];ny,nx=before.shape
    lon=west+(np.arange(nx)+.5)*(east-west)/nx;lat=north-(np.arange(ny)+.5)*(north-south)/ny
    fig,axes=plt.subplots(2,3,figsize=(12,8),layout='constrained')
    for axrow,(station,x,y) in zip(axes,[('Z9591',119.54055786,25.99138832),('Z9598',117.081,27.009)]):
        roi=((lon[None,:]-x)*111*np.cos(np.deg2rad(y)))**2+((lat[:,None]-y)*111)**2<=75**2
        valid=roi&np.isfinite(before);lost=valid&~np.isfinite(after);lowered=valid&np.isfinite(after)&(after<before);changed=lost|lowered
        rows.append(dict(case=path.stem,station=station,before_valid=int(valid.sum()),changed=int(changed.sum()),lost=int(lost.sum()),lowered=int(lowered.sum()),changed_percent=round(100*changed.sum()/max(1,valid.sum()),2)))
        change=np.full(before.shape,np.nan);change[lowered]=1;change[lost]=2
        for ax,data,title in zip(axrow,[before,after,change],['Before','Near-background candidate','Lowered=1 / unavailable=2']):
            im=ax.imshow(np.where(roi,data,np.nan),extent=[west,east,south,north],origin='upper',cmap='turbo' if title!='Lowered=1 / unavailable=2' else 'cool',vmin=-10 if title!='Lowered=1 / unavailable=2' else 1,vmax=30 if title!='Lowered=1 / unavailable=2' else 2)
            ax.set(xlim=(x-.8,x+.8),ylim=(y-.7,y+.7),title=station+' '+title);ax.plot(x,y,'k+',markersize=8)
            fig.colorbar(im,ax=ax,shrink=.65,label='dBZ' if title!='Lowered=1 / unavailable=2' else 'change category')
    fig.suptitle(path.stem+' UTC | 75 km ROIs; retrospective, not published')
    fig.savefig(args.directory/(path.stem+'-near.png'),dpi=140);plt.close(fig)
(args.directory/'near-summary.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))
