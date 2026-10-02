"""One finalizer, all native choices, shared work caps and atomic fallback."""
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from .helpers import station
from .test_native_alternatives import repeated
from .test_near_floor_references import candidate_config, sample


def test_normal_native_consensus_withholds_with_original_raw_and_no_confirmation():
    from rainpulse_algo.multiband.quality import x_qc
    volume, row, _ = sample()
    cut = volume.sweeps[0]
    repeated_row = row + 1
    cut = replace(cut, fields={k: np.concatenate([a, a[repeated_row:repeated_row+1]])
                              for k, a in cut.fields.items()},
                  azimuth_deg=np.r_[cut.azimuth_deg, cut.azimuth_deg[repeated_row]+.005],
                  elevation_deg=np.r_[cut.elevation_deg, cut.elevation_deg[repeated_row]],
                  ray_time_epoch=np.r_[cut.ray_time_epoch, cut.ray_time_epoch[repeated_row]+31])
    volume = replace(volume, sweeps=[cut])
    cfg = candidate_config(native_alternative_source_candidates_enabled=True)
    out = x_qc(volume, station(cfg), 'a'*64).sweeps[0]
    selected = out.fields['XQC_NATIVE_ALTERNATIVE_SOURCE_MASK'].astype(bool)
    assert selected[row, 400:].sum() > 10
    assert not selected[[repeated_row, len(cut.azimuth_deg)-1]].any()
    assert np.all(out.fields['QC_ACTION'][selected] == 3)
    assert np.isnan(out.fields['DBZH_QC'][selected]).all()
    assert not out.fields['REFLECTIVITY_ELIGIBLE_FOR_CR'][selected].any()
    np.testing.assert_array_equal(out.fields['DBZH'], cut.fields['DBZH'])
    assert out.xqc_diagnostics['module_records']['native_alternative_source']['status']=='EVALUATED'


def test_native_contract_default_off_and_dependency_strict():
    import jsonschema
    from pydantic import ValidationError
    cfg=candidate_config()
    assert not cfg.native_alternative_source_candidates_enabled
    schema=json.loads((Path(__file__).resolve().parents[3]/
                      'contracts/internal/multiband/x-qc-v2.schema.json').read_text())
    data=cfg.model_dump(mode='json')|{'native_alternative_source_candidates_enabled':True}
    jsonschema.validate(data,schema)
    for bad in [{'near_floor_source_candidates_enabled':False},
                {'native_alternative_source_candidates_enabled':'true'}]:
        with pytest.raises(ValidationError):
            type(cfg).model_validate(data|bad)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data|bad,schema)


def test_shared_allowance_exhaustion_abstains_all_views(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import native_alternatives as module
    from rainpulse_algo.multiband.xqc_v2.source_summary import SourceStatistics
    cut, _, _=repeated()
    cfg=candidate_config(native_alternative_source_candidates_enabled=True)
    called=[]
    original=SourceStatistics.build
    def build(s,c):
        called.append(s)
        return original(s,c)
    monkeypatch.setattr(SourceStatistics,'build',build)
    zero=np.zeros(cut.fields['DBZH'].shape,bool)
    previous={'radial_source':{'work':{'model_trials':cfg.source_maximum_trials}},
              'near_floor_source':{'status':'EVALUATED','work':{}}}
    selected, record=module.evaluate(cut,cfg,zero,previous)
    assert not selected.any()
    assert record['status']=='RESOURCE_OR_GEOMETRY_ABSTAINED'
    assert len(called)==1
    assert record['cumulative_source_trials']==cfg.source_maximum_trials


def test_partial_choice_failure_keeps_parent_and_never_publishes_partial(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import native_alternatives as module
    from rainpulse_algo.multiband.xqc_v2 import source_fans
    from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
    cut, row, _=repeated()
    cfg=candidate_config(native_alternative_source_candidates_enabled=True)
    calls=[]
    def detect(s,c,**kw):
        calls.append(s)
        if len(calls)==2:
            raise ResourceLimit('second actual view incomplete')
        kw['prepared'].trials += 7
        return np.ones(s.shape,bool), {'status':'EVALUATED'}
    monkeypatch.setattr(source_fans,'detect',detect)
    previous={'radial_source':{'work':{'model_trials':9}},
              'near_floor_source':{'status':'EVALUATED','work':{'model_trials':3}}}
    before=json.dumps(previous,sort_keys=True)
    selected, record=module.evaluate(cut,cfg,np.zeros(cut.fields['DBZH'].shape,bool),previous)
    assert len(calls)==2 and not selected.any()
    assert record['cumulative_source_trials']==19
    assert record['status']=='RESOURCE_OR_GEOMETRY_ABSTAINED'
    assert json.dumps(previous,sort_keys=True)==before


def test_native_evidence_overflow_withdraws_only_new_module(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import core, native_alternatives

    from .helpers import fixture
    volume,_=fixture()
    cut, row, _=repeated()
    cfg=candidate_config(native_alternative_source_candidates_enabled=True)
    old=core.evaluate_cut(cut,volume.metadata,
                          cfg.model_copy(update={'native_alternative_source_candidates_enabled':False}))
    def evaluate(c,*args):
        a=np.zeros(c.fields['DBZH'].shape,bool)
        a[row]=True
        return a, {'status':'EVALUATED','source_gates':int(a.sum()),'work':{'model_trials':7}}
    monkeypatch.setattr(native_alternatives,'evaluate',evaluate)
    dumps=core.json.dumps
    def overflow(obj,*a,**kw):
        module=(obj.get('module_records',{}).get('native_alternative_source',{})
                if isinstance(obj,dict) else {})
        if module.get('status')=='EVALUATED':
            return 'x'*(cfg.maximum_evidence_bytes+1)
        return dumps(obj,*a,**kw)
    monkeypatch.setattr(core.json,'dumps',overflow)
    new=core.evaluate_cut(cut,volume.metadata,cfg)
    assert not new.arrays['XQC_NATIVE_ALTERNATIVE_SOURCE_MASK'].any()
    for name,value in old.arrays.items():
        np.testing.assert_array_equal(value,new.arrays[name])
    r=new.record['module_records']['native_alternative_source']
    assert r['status']=='EVIDENCE_BUDGET_ABSTAINED' and r['work']['model_trials']==7
