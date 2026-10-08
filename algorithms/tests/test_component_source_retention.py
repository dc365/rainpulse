"""Regression checks against the frozen deployed parent plus the scoped patch."""
from types import SimpleNamespace
import numpy as np
from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig, infer_broad_source
from rainpulse_algo.radar.qc_engine import paired_range_components


def test_components_preserve_same_raw_scalar_actions_and_weather_veto(monkeypatch):
    r = np.arange(125., 460000., 250.)
    snr = np.full((20, len(r)), 55.5)
    z = snr - 44. + 20*np.log10(r/1000) + .011*r/1000
    rng = np.random.default_rng(1)
    fields = {'DBZH': z, 'SNR': snr, 'PHIDP': rng.normal(0, .1, z.shape),
              'ZDR': rng.normal(0, .1, z.shape), 'RHOHV': np.full(z.shape, .98)}
    native = SimpleNamespace(shape=z.shape, fields=fields,
        field_available={k:np.ones(z.shape,bool) for k in fields},
        geometry_good=np.ones(20,bool), ranges=r, gate_spacing_m=250.,
        azimuth=np.arange(20.), gap_after=np.r_[np.zeros(19,bool),True], full_ppi=False)
    weather = np.zeros(z.shape,bool)
    weather[:,300:350] = True
    cfg = BroadSourceConfig(shared_range_term=True)
    baseline, _ = infer_broad_source(native,cfg,weather=weather)
    assert baseline['BWS_CANDIDATE_MASK'].sum() > 0
    # Deliberately unsupported candidate relation exercises the retention boundary.
    monkeypatch.setattr(paired_range_components,'calibrate_components',lambda *args:
        (np.full(20,.5),np.ones(20,bool),{'status':'measured_components'}))
    candidate, report = infer_broad_source(native,cfg.model_copy(update={'component_range_calibration':True}),weather=weather)
    mask = baseline['BWS_CANDIDATE_MASK']==1
    assert np.all(candidate['BWS_CANDIDATE_MASK'][mask]==1)
    assert not candidate['BWS_CANDIDATE_MASK'][weather].any()
    assert report['baseline_retained_gates'] > 0
    assert np.array_equal(candidate['BWS_FOLD_ID'][mask],baseline['BWS_FOLD_ID'][mask])
    assert np.array_equal(candidate['BWS_RANGE_RESIDUAL_DB'][mask],baseline['BWS_RANGE_RESIDUAL_DB'][mask])
