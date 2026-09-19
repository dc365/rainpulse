"""Offline near-site background/nonmet hypothesis; no texture or ZDR prerequisite.

Requires reliable low RHOHV coherent across a measured neighbourhood. Does not
assign ground/biological subtype, or treat low SNR as nonmeteorological evidence.
"""
import numpy as np
from scipy.ndimage import uniform_filter


def candidate(z,rho,snr,background,eligible,protected,azimuth,ranges):
    z=np.asarray(z);rho=np.asarray(rho);snr=np.asarray(snr)
    shape=z.shape;r=np.asarray(ranges);az=np.asarray(azimuth)
    if z.ndim!=2 or shape!=(len(az),len(r)) or min(shape)<3:raise ValueError('invalid polar shape')
    if rho.shape!=shape or snr.shape!=shape:raise ValueError('moment shape mismatch')
    masks=[]
    for value in (background,eligible,protected):
        a=np.asarray(value)
        if a.shape!=shape or not np.isin(a,(0,1)).all():raise ValueError('invalid binary mask')
        masks.append(a.astype(bool))
    bg,ok,weather=masks
    dr=np.diff(r)
    if not np.isfinite(r).all() or not np.isfinite(az).all() or np.any(dr<=0) or not np.allclose(dr,np.median(dr),rtol=.001):raise ValueError('invalid sampling geometry')
    delta=(np.roll(az,-1)-az)%360
    good=(delta>.01)&(delta<=2)&(np.roll(delta,1)>.01)&(np.roll(delta,1)<=2)
    measured=np.isfinite(z)&np.isfinite(rho)&(rho>=0)&(rho<=1)&np.isfinite(snr)&(snr>=8)&good[:,None]
    abnormal=measured&(rho<=.85)&(z<=20)
    width=max(3,int(round(2000/np.median(dr)))|1)
    coverage=uniform_filter(measured.astype(float),size=(3,width),mode=('wrap','constant'))
    support=uniform_filter(abnormal.astype(float),size=(3,width),mode=('wrap','constant'))
    fraction=np.divide(support,coverage,out=np.zeros(shape),where=coverage>0)
    remove=ok&bg&~weather&abnormal&(r[None,:]>=0)&(r[None,:]<75000)&(coverage>=.8)&(fraction>=.7)
    return remove,coverage,fraction
