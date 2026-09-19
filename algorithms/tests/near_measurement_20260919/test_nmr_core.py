import hashlib,json
import numpy as np
import pytest
from near_helpers import cfg,sample,same
from volume_review.near_measurement.core import evaluate
from volume_review.near_measurement.backends import reference_depolarization,depolarization
from volume_review.near_measurement.config import NearMeasurementConfig
from volume_review.config import VolumeReviewConfig


def ev(a,az,r,**kwargs):return evaluate(a,az,r,cfg(),**kwargs).arrays


def test_candidate_does_not_change_raw():
 a,az,r=sample();before={k:v.copy() for k,v in a.items()};e=ev(a,az,r)
 assert e['NMR_NONMET_CANDIDATE_MASK'].sum()>500
 assert not e['NMR_LOW_SNR_UNCERTAIN_MASK'].any();same(a,before)


@pytest.mark.parametrize('z',[-20.,-10.01,30.,35.,60.])
def test_no_rain_and_strong_echoes_are_not_removed(z):
 a,az,r=sample(z=z,snr=2);e=ev(a,az,r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'].any();assert not e['NMR_LOW_SNR_UNCERTAIN_MASK'].any()


def test_no_rain_boundary_is_not_confused_with_missing():
 a,az,r=sample(z=-10.,snr=2);e=ev(a,az,r)
 assert e['NMR_LOW_SNR_UNCERTAIN_MASK'].any()
 assert not e['NMR_VALID_NO_RAIN_MASK'].any()


@pytest.mark.parametrize('key',['SNR_RAW','RHOHV_RAW','ZDR_RAW'])
def test_missing_pol_does_not_create_nonmet(key):
 a,az,r=sample();del a[key];e=ev(a,az,r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'].any()
 if key=='SNR_RAW':assert not e['NMR_LOW_SNR_UNCERTAIN_MASK'].any()


def test_explicit_availability_overrides_finite_value():
 a,az,r=sample();available={k:np.ones(a['DBZH_RAW'].shape,bool) for k in ('DBZH','SNR','RHOHV','ZDR','PHIDP')}
 available['RHOHV'][:]=False;e=ev(a,az,r,available=available)
 assert not e['NMR_NONMET_CANDIDATE_MASK'].any()
 assert not e['NMR_DR_AVAILABLE_MASK'].any()


def test_low_snr_is_uncertainty_not_nonmet():
 a,az,r=sample(snr=2);e=ev(a,az,r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'].any();assert e['NMR_LOW_SNR_UNCERTAIN_MASK'].any()


@pytest.mark.parametrize('key,val',[('RHOHV_RAW',1.2),('RHOHV_RAW',-.2),('ZDR_RAW',7.5),('ZDR_RAW',-7.5)])
def test_invalid_or_tail_polarization_is_not_evidence(key,val):
 a,az,r=sample();a[key][:]=val;e=ev(a,az,r);assert not e['NMR_NONMET_CANDIDATE_MASK'].any()


def test_perfect_correlation_dr_is_finite_diagnostic_floor():
 c=cfg();v,_=depolarization(np.array([0.,.2,np.nan]),np.array([1.,.7,.7]),c)
 assert v[0]==-100 and np.isfinite(v[1]) and np.isnan(v[2])
 assert np.isneginf(reference_depolarization(0,1))


def test_weather_proxy_and_previous_mixed_protect():
 a,az,r=sample(rho=.99,zdr=.2,snr=20);e=ev(a,az,r)
 assert e['NMR_WEATHER_PROXY_MASK'].any();assert not e['NMR_NONMET_CANDIDATE_MASK'].any()
 a,az,r=sample();a['NP_MIXED_MASK']=np.ones_like(a['VALID_MASK']);e=ev(a,az,r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'].any()


def test_missing_phi_cannot_create_weather_seed():
 a,az,r=sample(rho=.99,snr=20);del a['PHIDP_RAW'];e=ev(a,az,r)
 assert not e['NMR_WEATHER_PROXY_MASK'].any()


def test_sparse_neighbourhood_needs_measured_samples():
 a,az,r=sample();a['RHOHV_RAW'][:]=np.nan;a['RHOHV_RAW'][6,50]=.7;e=ev(a,az,r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'].any()


def test_original_order_invariance():
 a,az,r=sample();e=ev(a,az,r);p=np.random.default_rng(4).permutation(len(az))
 ee=ev({k:v[p] for k,v in a.items()},az[p],r)
 for k in e:assert np.array_equal(ee[k],e[k][p],equal_nan=True),k


def test_angular_rotation_invariance_including_north_seam():
 a,az,r=sample();e=ev(a,az,r);ee=ev(a,(az+355)%360,r)
 for k in e:assert np.allclose(ee[k],e[k],equal_nan=True),k


def test_gap_and_duplicate_abstention():
 a,az,r=sample();az[6:]+=30;e=ev(a,az,r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'][5:7].any()
 a,az,r=sample();az[6]=az[5];e=ev(a,az,r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'][4:8].any()


def test_bad_native_geometry_and_range_edges():
 a,az,r=sample();good=np.ones(len(az),bool);good[6]=False;e=ev(a,az,r,geometry_good=good)
 assert not e['NMR_NONMET_CANDIDATE_MASK'][5:8].any()
 assert not e['NMR_NONMET_CANDIDATE_MASK'][:,:2].any()
 assert not e['NMR_NONMET_CANDIDATE_MASK'][:,-2:].any()


def test_two_rays_cannot_count_neighbour_twice():
 a,az,r=sample();e=ev({k:v[:2] for k,v in a.items()},az[:2],r)
 assert not e['NMR_NONMET_CANDIDATE_MASK'].any()


@pytest.mark.parametrize('value',[0.,2.,7.,float('nan')])
def test_frozen_uncertainty_profile_validation(value):
 with pytest.raises(ValueError):cfg(uncertainty_snr_db=value)


def test_absent_nested_config_keeps_parent_serialization():
 c=VolumeReviewConfig();d=c.model_dump(mode='json')
 assert 'near_measurement' not in d
 assert c.digest==hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 child=VolumeReviewConfig(near_measurement=cfg());assert child.digest!=c.digest


def test_invalid_parent_experiment_and_evidence_export_rejected():
 with pytest.raises(ValueError):VolumeReviewConfig(near_measurement=cfg(mode='experiment'))
 with pytest.raises(ValueError):VolumeReviewConfig(export_evidence=False,near_measurement=cfg())


def test_resource_limit_does_not_return_partial_evidence():
 a,az,r=sample()
 with pytest.raises(ValueError,match='resource'):evaluate(a,az,r,cfg(maximum_sweep_gates=20))
