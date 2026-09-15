from types import SimpleNamespace
import numpy as np
from rainpulse_algo.radar.qc_engine.measurement_v8.native import angular_mapping


def test_extra_rays_do_not_duplicate_or_fill():
    az=np.sort(np.r_[np.arange(360)+.08, [.1, 20.1, 40.1, 60.1, 80.1, 100.1]])
    n=SimpleNamespace(azimuth=az, shape=(len(az),10), full_ppi=True,
                      geometry_good=np.ones(len(az),bool), audit={'azimuth_spacing_deg':1.})
    target,source,good,error=angular_mapping(n,.25)
    assert len(target)==360 and good.all()
    assert len(set(source))==360 and error.max()<=.25
    assert len(set(range(366))-set(source))==6


def test_outside_error_bound_is_unknown():
    az=np.arange(360,dtype=float);az[90]+=.4
    n=SimpleNamespace(azimuth=az,shape=(360,10),full_ppi=True,
                      geometry_good=np.ones(360,bool),audit={'azimuth_spacing_deg':1.})
    _,_,good,_=angular_mapping(n,.25)
    assert not good[90]
    assert good.sum()==359


def test_scores_restore_without_filling_unselected_rays(monkeypatch):
    from rainpulse_algo.radar.qc_engine import adapters
    from rainpulse_algo.radar.qc_engine.measurement_v8 import native as module
    from rainpulse_algo.radar.qc_engine.measurement_v8.schema import NativeConfig
    az=np.sort(np.r_[np.arange(360)+.08, [.1,20.1,40.1,60.1,80.1,100.1]])
    n=adapters.NativeSweep('sweep_000',az,np.zeros(366),np.arange(10.),np.arange(366),
        {'DBZH':np.ones((366,10))},{'DBZH':np.ones((366,10),bool)},np.arange(366),True,
        np.ones(366,bool),np.zeros(366,bool),{}, {'azimuth_spacing_deg':1.})
    def fake(mapped,cfg,*args):
        assert mapped.shape==(360,10)
        assert not cfg.angular_mapping
        return module.NativeResult({'1':np.ones(mapped.shape),'2':np.ones(mapped.shape)}, {})
    monkeypatch.setattr(module,'run_native',fake)
    out=module.mapped_native(n,NativeConfig(angular_mapping=True),'unused','hash')
    assert np.isfinite(out.scores['1']).sum()==3600
    assert np.isnan(out.scores['1']).sum()==60
    assert out.summary['angular_mapping']['interpolated'] is False
