from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import numpy as np
import pytest
from .helpers import config
from rainpulse_algo.radar.qc_engine.volume_review.episode_background.data import Sample
from rainpulse_algo.radar.qc_engine.volume_review.episode_background.builder import build_episode
from rainpulse_algo.radar.qc_engine.volume_review.episode_background.config import BuildConfig
from rainpulse_algo.radar.qc_engine.volume_review.episode_background.io import background_bytes
from rainpulse_algo.multiband.xqc_v2.core import background
from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep
T=datetime(2026,8,27,21,tzinfo=timezone.utc)


def fixture(tmp_path):
    shape=(8,40)
    f={k:np.full(shape,v,'float32') for k,v in {'DBZH':5.,'SNR':12.,'RHOHV':.7,'ZDR':.2,'PHIDP':2.}.items()}
    a={k:np.isfinite(v) for k,v in f.items()}
    samples=[Sample('x01','bg'+str(i),'sweep_000','xconfig',(T+timedelta(minutes=6*i)).isoformat(),
        hashlib.sha256(str(i).encode()).hexdigest(),np.arange(8.),np.full(8,.5),2000+np.arange(40)*250.,f,a,np.ones(8,bool)) for i in range(20)]
    model=build_episode(samples,BuildConfig(),reviewed_no_precipitation=True,review_receipt='a'*64)
    b=background_bytes(model);p=tmp_path/'bg.npz';p.write_bytes(b)
    target=replace(samples[0],scan_id='target',observed_at=(T+timedelta(hours=3)).isoformat(),source_sha256='b'*64)
    gaps=np.zeros(8,bool);gaps[-1]=True
    fields={k:v for k,v in target.fields.items() if k!='ZDR'};avail={k:v for k,v in target.available.items() if k!='ZDR'}
    s=Sweep('sweep_000',target.azimuth,target.elevation,target.ranges,fields,avail,np.ones(8,bool),gaps,
            np.full(8,(T+timedelta(hours=3)).timestamp()))
    c=config(clutter={'depolarization_backend':'numpy_reference','mode':'quarantine',
         'background':{'assets':{'x01':{'path':str(p),'sha256':hashlib.sha256(b).hexdigest()}}}})
    m={'radar_id':'x01','scan_id':'target','radar_config_version':'xconfig','asset_sha256':'b'*64}
    return s,c,m


def test_real_shared_background_roundtrip_in_x_bridge(tmp_path):
    s,c,m=fixture(tmp_path);a,r=background(s,m,c)
    assert a['CF_BG_CURRENT_NONMET_MASK'].any()
    assert a['CF_BG_MATCH_MASK'].any()


def test_background_processing_missing_or_different(tmp_path):
    s,c,m=fixture(tmp_path);m['radar_config_version']='other'
    a,r=background(s,m,c);assert not a['CF_BG_CURRENT_NONMET_MASK'].any()
    del m['radar_config_version'];a,r=background(s,m,c)
    assert r['status']=='PROCESSING_ID_UNAVAILABLE'
    assert not a['CF_BG_CURRENT_NONMET_MASK'].any()
