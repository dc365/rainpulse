from dataclasses import replace
import numpy as np
from volume_review.data import Sweep
from volume_review.clutter_fusion.config import ClutterFusionConfig
from volume_review.clutter_fusion.isolation_config import IsolationConfig
from volume_review.clutter_fusion.engine import evaluate_volume


def config(mode='audit',**kw):
    c=kw.pop('isolated_objects',None) or IsolationConfig(mode=mode,**kw)
    return ClutterFusionConfig(mode='quarantine' if mode=='quarantine' else 'cr_withhold',
        depolarization_backend='numpy_reference',isolated_objects=c)


def scene(kind='isolated',*,name='sweep_000',zvalue=15.,rho=.65,known=True,shift=0.,dr=250.,nr=360,range0=125.):
    ng=int(45000/dr);ranges=range0+np.arange(ng)*dr;az=(np.arange(nr)*360/nr+shift)%360
    shape=(nr,ng);z=np.full(shape,np.nan,'float32')
    # Physical placement described by true azimuth and range, not fixed pixel count.
    da=(az-90+180)%360-180
    obj=(abs(da[:,None])<=1.1)&(ranges[None,:]>=19750)&(ranges[None,:]<20750)
    if kind=='uniform':obj=(ranges[None,:]<35000)*np.ones((nr,1),bool)
    if kind=='long':obj=(abs(da[:,None])<=1.1)&(ranges[None,:]>5000)&(ranges[None,:]<35000)
    z[obj]=zvalue
    fields={'DBZH':z}
    for k,v in [('SNR',20.),('RHOHV',rho),('ZDR',0.),('PHIDP',5.)]:
        fields[k]=np.where(obj,v,np.nan).astype('float32')
    no=(~obj) if known else np.zeros(shape,bool)
    return Sweep(name,az,np.full(nr,.5),ranges,fields,{k:np.isfinite(v) for k,v in fields.items()},
                 np.ones(nr,bool),np.zeros(nr,bool),np.zeros(nr),no_echo=no)


def base(s):
    obs=s.observed;z=s.fields['DBZH']
    a={'DBZH_RAW':z.copy(),'DBZH_QC':z.copy(),'DBZH_USABLE':z.copy(),
       'QC_FLAGS':np.zeros(s.shape,'uint32'),'QUALITY_INDEX':np.where(obs,.9,np.nan).astype('float32'),
       'QC_ACTION':np.where(obs,0,3).astype('uint8'),'LOW_QUALITY_MASK':np.zeros(s.shape,'uint8'),
       'CR_UNCERTAIN_MASK':np.zeros(s.shape,'uint8'),'CR_QUALIFICATION_REASON':obs.astype('uint16')}
    for k in ('VALID_MASK','REFLECTIVITY_TRUST_MASK','QPE_ELIGIBLE_MASK','REFLECTIVITY_ELIGIBLE_FOR_CR'):
        a[k]=obs.astype('uint8')
    for k,v in s.fields.items():
        if k!='DBZH':a[k+'_RAW']=v.copy();a[k+'_TRUST_MASK']=(s.available[k]&obs).astype('uint8')
    return a


def ev(s,c,**kw):return evaluate_volume([s],c,**kw)[0]

def change(s,k,mask,value):
    fields={n:v.copy() for n,v in s.fields.items()};fields[k][mask]=value
    available={n:np.isfinite(v) for n,v in fields.items()}
    return replace(s,fields=fields,available=available,no_echo=s.no_echo&~available['DBZH'])
