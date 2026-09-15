import numpy as np
from pathlib import Path
from rainpulse_algo.radar.qc_engine.measurement_v8.synthetic import make_case
from rainpulse_algo.radar.qc_engine.measurement_v8.case import FrozenCase
from rainpulse_algo.radar.qc_engine.algorithms import library_evidence


def test_cached_texture_filter_matches_pinned_helper(tmp_path,monkeypatch):
    import pyart
    c=FrozenCase(make_case(tmp_path/'case',Path(__file__).resolve().parents[2],index=41,missing=True))
    n,_,_=c.sweep('sweep_000');p=c.profile
    radar=n.to_pyart();window=max(3,int(round(p.pyart.texture_window_m/n.gate_spacing_m))|1)
    expected=pyart.filters.moment_and_texture_based_gate_filter(radar,refl_field='reflectivity',zdr_field='differential_reflectivity',rhv_field='cross_correlation_ratio',phi_field='differential_phase',wind_size=window,max_textrefl=p.pyart.max_textrefl,max_textzdr=p.pyart.max_textzdr,max_textrhv=p.pyart.max_textrhv,max_textphi=p.pyart.max_textphi,min_rhv=p.pyart.min_rhv)
    calls=[];original=pyart.util.texture_along_ray
    def count(*args,**kwargs):calls.append(args[1]);return original(*args,**kwargs)
    monkeypatch.setattr(pyart.util,'texture_along_ray',count)
    result=library_evidence(n,p)
    echo=n.field_available['DBZH']&(n.fields['DBZH']>=p.pyart.object_threshold_dbz)
    np.testing.assert_equal(result.arrays['OS_PYART_BASELINE_CANDIDATE_MASK'],expected.gate_excluded&echo)
    assert len(calls)==len(set(calls))==4
