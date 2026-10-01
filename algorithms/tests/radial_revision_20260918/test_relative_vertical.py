import importlib
import copy
import numpy as np
import pytest
from .conftest import Native


def fixture():
    low=Native(np.full((5,160),20.),start=2000.,fields={'RHOHV':.99,'SNR':25.})
    low.attrs.update(scan_id='volume-1',radar_id='station-1')
    low.ray_time=np.arange(5)+1700000000.
    high=low.clone();high.elevation[:]=1.5;high.ray_time+=100.
    return [low,high],[np.ones(low.shape,bool),np.ones(high.shape,bool)]


def run(s,m,**kwargs):
    module=importlib.import_module('radial_revision_test.engine.relative_vertical')
    return module.support(s,m,beam_width_deg=1.,**kwargs)


def test_same_site_relative_support_independent_of_common_absolute_altitude():
    s,m=fixture();out=run(s,m)
    assert np.isfinite(out[0][0]).any() and not np.isfinite(out[0][1]).any()
    changed=copy.deepcopy(s)
    for x in changed:x.attrs['antenna_altitude_m']=8000.;x.attrs['altitude_datum']='unknown'
    again=run(changed,m)
    assert np.array_equal(out[0][0],again[0][0],equal_nan=True)
    assert out[3]['negative_evidence'] is False


def test_missing_untrusted_or_weather_incompatible_upper_remains_unknown():
    for key in ('DBZH','RHOHV','SNR'):
        s,m=fixture();s[1].field_available[key][:]=False
        assert not np.isfinite(run(s,m)[0][0]).any()
    s,m=fixture();m[1][:]=False
    assert not np.isfinite(run(s,m)[0][0]).any()
    s,m=fixture();s[1].fields['RHOHV'][:]=.6
    assert not np.isfinite(run(s,m)[0][0]).any()


def test_time_height_geometry_and_identity_are_real_limits():
    s,m=fixture();s[1].ray_time+=1000.
    assert not np.isfinite(run(s,m)[0][0]).any()
    s,m=fixture();s[1].geometry_good[:]=False
    assert not np.isfinite(run(s,m)[0][0]).any()
    s,m=fixture();s[1].elevation[:]=40.
    out=run(s,m)
    assert not np.isfinite(out[0][0][:,10:]).any()
    assert np.nanmax(out[2][0])<=3000.
    s,m=fixture();s[1].attrs['radar_id']='other'
    with pytest.raises(ValueError):run(s,m)


def test_native_ground_footprint_is_not_equal_slant_range_and_no_extrapolation():
    s,m=fixture();s[1].ranges+=100000.
    out=run(s,m)
    assert not np.isfinite(out[0][0][:,:90]).any()
    s,m=fixture();s[1].field_available['DBZH'][:]=False
    s[1].field_available['DBZH'][2,50]=True
    assert not np.isfinite(run(s,m)[0][0]).any()
