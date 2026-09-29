from dataclasses import replace
import copy
import numpy as np
import pytest
from rainpulse_algo.multiband.native_geometry import representative_rows, qc_then_select
from rainpulse_algo.multiband.preview_sampling import prepare_polar_sampling
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from .helpers import fixture, config, station


def repeated():
    v,_=fixture('weather',rays=120,gates=40);s=v.sweeps[0]
    s.azimuth_deg[11]=s.azimuth_deg[10]
    s.ray_time_epoch[11]=s.ray_time_epoch[10]-1
    s.fields['DBZH'][11]=65
    return v


def test_latest_timestamp_then_first_native_index_not_last_row():
    np.testing.assert_array_equal(representative_rows([10,10,10,20],[3,4,4,2]),[1,3])
    with pytest.raises(ValueError):representative_rows([10,10],[1,float('nan')])


def test_projection_does_not_remove_qc_geometry_barriers():
    v=repeated();s=v.sweeps[0];c=config()
    native=adapt(s,c).sweep
    assert not native.good[10:12].any()
    c=config(mode='quarantine',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    standalone=x_qc(v,station(c),'a'*64)
    calls=[]
    def checked_qc(raw,st,sha):
        calls.append(len(raw.sweeps[0].azimuth_deg))
        return x_qc(raw,st,sha)
    projected,rows=qc_then_select(v,station(c),'a'*64,qc=checked_qc)
    assert calls==[120] and 10 in rows and 11 not in rows
    for key,a in standalone.sweeps[0].fields.items():
        np.testing.assert_array_equal(projected.sweeps[0].fields[key],a[rows])
    assert len(v.sweeps[0].azimuth_deg)==120 and s.fields['DBZH'][11,0]==65


def test_preview_selection_matches_numerical_projection_when_time_supplied():
    v=repeated();s=v.sweeps[0]
    sampling=(np.full((2,2),s.range_m[5]),np.full((2,2),s.azimuth_deg[10]))
    p=prepare_polar_sampling(s.azimuth_deg,s.range_m,size=2,map_sampling=sampling,
        elevation_deg=s.elevation_deg,ray_time_epoch=s.ray_time_epoch)
    assert (p.rays==10).all()
    old=prepare_polar_sampling(s.azimuth_deg,s.range_m,size=2,map_sampling=sampling,elevation_deg=s.elevation_deg)
    assert (old.rays==11).all() # backwards-compatible timestamp-free API


@pytest.mark.parametrize('seed',[0,1,7])
def test_row_permutations_keep_real_acquisition_identity(seed):
    v=repeated();s=v.sweeps[0];rng=np.random.default_rng(seed);order=rng.permutation(120)
    a=representative_rows(s.azimuth_deg,s.ray_time_epoch)
    b=representative_rows(s.azimuth_deg[order],s.ray_time_epoch[order])
    assert set(order[b])==set(a)
