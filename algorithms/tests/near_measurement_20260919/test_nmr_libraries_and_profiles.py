from pathlib import Path
import importlib.util
import json
import numpy as np
import pytest
from near_helpers import cfg
from volume_review.near_measurement.backends import depolarization,reference_depolarization
from volume_review.near_measurement.config import NearMeasurementConfig


def test_real_wradlib_parity_when_installed():
 pytest.importorskip('wradlib',reason='wradlib is not installed; real backend not verified locally')
 c=NearMeasurementConfig();z=np.linspace(-7.,7.,31)[:,None];rho=np.linspace(0.,1.,21)[None,:]
 result,_=depolarization(z,rho,c)
 assert np.allclose(result,np.maximum(reference_depolarization(z,rho),-100.),atol=2e-5)


def test_real_pyart_gatefilter_when_installed():
 pyart=pytest.importorskip('pyart',reason='Py-ART is not installed; real GateFilter not verified locally')
 from volume_review.near_measurement.backends import require
 pyart=require('arm_pyart','pyart','2.2.5')
 radar=pyart.testing.make_empty_ppi_radar(20,10,1);radar.add_field('reflectivity',{'data':np.ones((10,20))})
 filt=pyart.filters.GateFilter(radar);m=np.zeros((10,20),bool);m[:,5]=True
 filt.exclude_gates(m,op='or');assert np.array_equal(filt.gate_excluded,m)


def test_default_backend_is_not_silent_reference_fallback(monkeypatch):
 from importlib.metadata import PackageNotFoundError
 from volume_review.near_measurement import backends
 def absent(*args):raise PackageNotFoundError('wradlib')
 monkeypatch.setattr(backends.metadata,'version',absent)
 with pytest.raises(RuntimeError,match='requires wradlib'):depolarization([.2],[.7],NearMeasurementConfig())
 # The reference mode is a deliberate different identity, not fake installed wradlib.
 result,rec=depolarization([.2],[.7],cfg());assert rec['wradlib_called'] is False
 assert cfg().digest!=NearMeasurementConfig().digest


def load_generator():
 p=Path(__file__).resolve().parents[3]/'scripts/make_near_measurement_profiles.py'
 s=importlib.util.spec_from_file_location('near_generate',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def test_profile_generation_is_versioned_atomic_and_non_overwriting(tmp_path):
 import yaml
 p=tmp_path/'parent.yaml';p.write_text(yaml.safe_dump({'profile_version':'test-parent','pipeline_version':'qc-opensource-7.3.9',
 'operational_eligible':False,'echo':{'no_rain_below_dbz':-10},'volume_review':{'phase':3,'mode':'experiment_quarantine'}}))
 original=p.read_bytes();gen=load_generator();result=gen.generate(p,tmp_path/'children',backend='numpy_reference')
 assert len(result)==8;assert len({x['near_config_sha256'] for x in result})==8
 assert p.read_bytes()==original
 with pytest.raises(ValueError):gen.generate(p,tmp_path/'children')
 for path in (tmp_path/'children').glob('*.yaml'):
  child=yaml.safe_load(path.read_text());assert child['operational_eligible'] is False
