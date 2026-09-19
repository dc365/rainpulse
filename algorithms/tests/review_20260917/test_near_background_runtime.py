import hashlib
import numpy as np
import pytest
from .conftest import Native,load,keep,serial


def setup(tmp_path):
    path=tmp_path/'background.npz';s=(360,75)
    np.savez(path,azimuth=np.arange(360),range_m=np.arange(500,75000,1000),
        **{'001_p10':np.full(s,-3.),'001_p90':np.full(s,3.),'001_observed_fraction':np.ones(s),
           '001_observed_count':np.full(s,40),'001_elevation':np.array(.5)})
    n=Native(shape=(360,40),full=True,dr=250,start=1000,RHOHV=.75,SNR=15,ZDR=1)
    c=load('config').NonPrecipConfig(mode='experiment_quarantine',quarantine_classes=('near_nonmet',),near_background={
        'version':'near-background-20260919-v1','target_date':'2026-09-17','assets':{'synthetic':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}})
    return n,c


def apply(n,c):
    b=keep(n);result=load('nonprecip').classify(n,{},c)
    load('near_background_runtime').augment(n,b,result,c,-10)
    out,_=load('runtime').project(n,b,result,c,low_quality_flag=16384)
    return out


def test_real_projection_closes_all_trust_and_passes_validator(tmp_path):
    n,c=setup(tmp_path);out=apply(n,c);q=out.arrays['NP_QUARANTINE_MASK']==1
    assert q.any()
    assert not out.arrays['QPE_ELIGIBLE_MASK'][q].any()
    assert not out.arrays['REFLECTIVITY_TRUST_MASK'][q].any()
    assert np.isnan(out.arrays['DBZH_USABLE'][q]).all()
    load('runtime').validate_nonprecip_fields(serial(out,n),np.ones(n.shape,bool),np.zeros(n.shape,bool),np.zeros(n.shape,bool))


def test_date_and_moment_availability_barriers(tmp_path):
    n,c=setup(tmp_path);n.attrs['volume_end_time_utc']='2026-09-18T00:00:00Z'
    assert not apply(n,c).arrays['NP_QUARANTINE_MASK'].any()
    n.attrs['volume_end_time_utc']='2026-09-17T00:00:00Z';n.field_available['RHOHV'][:]=False
    assert not apply(n,c).arrays['NP_QUARANTINE_MASK'].any()


def test_hash_fail_closed(tmp_path):
    n,c=setup(tmp_path);asset=c.near_background.assets['synthetic']
    with pytest.raises(ValueError,match='hash mismatch'):
        load('near_background_runtime').load_asset(asset.path,'0'*64)
