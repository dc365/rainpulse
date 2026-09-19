"""Research sampling: equivalent footprint equations to RainPulse c3df57c polar_sampling.py.
Original index is retained; no angular or radial extrapolation.
"""
import numpy as np

def polar_targets(azimuth, ranges, radius, angle):
    az=np.asarray(azimuth,float)%360; r=np.asarray(ranges,float)
    order=np.argsort(az,kind='stable'); a=az[order]
    gap=(np.roll(a,-1)-a)%360; dup=gap<=.01
    positive=gap[~dup]; small=positive[positive<=np.median(positive)] if len(positive) else []
    nominal=float(np.median(small)) if len(small) else 0.
    good=~(dup|np.roll(dup,1)); after=np.where(gap>nominal*1.8,nominal,gap)/2;before=np.roll(after,1)
    radius,angle=np.broadcast_arrays(np.asarray(radius,float),np.asarray(angle,float)%360)
    pos=np.searchsorted(a,angle); li=(pos-1)%len(a);ri=pos%len(a)
    dl=abs((angle-a[li]+180)%360-180); dr=abs((angle-a[ri]+180)%360-180)
    nearest=np.where(dl<=dr,li,ri); signed=(angle-a[nearest]+180)%360-180
    within=np.where(signed>=0,signed<=after[nearest]+1e-9,-signed<=before[nearest]+1e-9)
    gi=np.clip(np.searchsorted(r,radius),0,len(r)-1); prev=np.maximum(gi-1,0)
    gi=np.where(abs(radius-r[prev])<=abs(radius-r[gi]),prev,gi)
    spacing=float(np.median(np.diff(r))); side=np.where(np.diff(r)>spacing*1.8,spacing,np.diff(r))/2
    lo=np.maximum(0,r-np.r_[spacing/2,side]);hi=r+np.r_[side,spacing/2]
    valid=within&good[nearest]&(nominal>0)&(radius>=lo[gi])&(radius<=hi[gi])&(radius<=r[-1])
    return order[nearest],gi,valid

def polar_pixels(azimuth,ranges,size=640):
    xy=np.linspace(-1.,1.,size);xx,yy=np.meshgrid(xy,-xy)
    return polar_targets(azimuth,ranges,np.hypot(xx,yy)*ranges[-1],np.degrees(np.arctan2(xx,yy))%360)
