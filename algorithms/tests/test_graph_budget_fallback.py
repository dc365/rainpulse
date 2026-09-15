from types import SimpleNamespace
import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine import hypotheses as h


@pytest.mark.parametrize('resource',['node','edge'])
def test_only_capacity_failure_preserves_baseline(monkeypatch,resource):
    d=SimpleNamespace(arrays={'QC_ACTION':np.array([[0,1,2,3]])})
    before=d.arrays['QC_ACTION'].copy()
    def fail(*a,**kw): raise h.GraphBudgetExceeded(resource+' budget')
    monkeypatch.setattr(h,'build_hypotheses',fail)
    result,record=h.graph_with_fallback(None,d,None)
    assert result is d and record['status']=='degraded_budget'
    np.testing.assert_equal(before,result.arrays['QC_ACTION'])
    assert record['partial_graph_used'] is False


def test_geometry_errors_remain_fatal(monkeypatch):
    def fail(*a,**kw):raise ValueError('geometry differs')
    monkeypatch.setattr(h,'build_hypotheses',fail)
    with pytest.raises(ValueError,match='geometry'):
        h.graph_with_fallback(None,None,None)


def test_stage_a_budget_failure_cannot_vote_weather(tmp_path,monkeypatch):
    from rainpulse_algo.radar.qc_engine.measurement_v8.synthetic import make_case
    from rainpulse_algo.radar.qc_engine.measurement_v8.case import FrozenCase
    from rainpulse_algo.radar.qc_engine.standalone_evidence import stage_a
    from pathlib import Path
    case=FrozenCase(make_case(tmp_path/'case',Path(__file__).resolve().parents[2],index=41))
    native,_,_=case.sweep('sweep_000')
    def fail(*a,**kw):raise h.GraphBudgetExceeded('node budget')
    monkeypatch.setattr(h,'build_hypotheses',fail)
    result=stage_a(native,case.profile)
    assert not result.donor_usable.any()
    assert result.summary['graph_degradation']['status']=='degraded_budget'
    assert np.all(~result.temporal_available | result.temporal_candidate)


def test_worker_serializes_budget_fallback(tmp_path,monkeypatch):
    import zarr
    from zarr.storage import MemoryStore
    from rainpulse_algo.radar.qc import load_qc_profile
    from rainpulse_algo.radar.qc_worker import _execute_basic_qc
    from rainpulse_algo.worker.domain_contracts import RadarQCRequested
    from .test_residual_v61_integration import build_case61, frozen, FLAGS, ROOT
    _,_,_,client,request=build_case61(tmp_path)
    path=ROOT/'configs/qc/fujian-qc-evidence-graph-v7.yaml'
    p=load_qc_profile(path,FLAGS);task=request.model_dump(mode='json')
    task['payload'].update(qc_profile=p.profile_version,qc_pipeline_version=p.pipeline_version,qc_profile_sha256=frozen(path)['sha256'])
    monkeypatch.setenv('RAINPULSE_RADAR_QC_CONFIG',str(path));monkeypatch.setenv('RAINPULSE_QC_FLAG_DEFINITIONS',str(FLAGS))
    def fail(*a,**kw):raise h.GraphBudgetExceeded('node budget')
    monkeypatch.setattr(h,'build_hypotheses',fail)
    output=_execute_basic_qc(RadarQCRequested.model_validate(task),client)
    store=MemoryStore();store.update(output.objects);q=zarr.open_group(store,mode='r')['sweep_000']
    assert not q['V7_STAGE_A_DONOR_USABLE_MASK'][:].any()
    eligible=q['QPE_ELIGIBLE_MASK'][:]==1
    assert np.isnan(q['DBZH_USABLE'][:][~eligible]).all()
