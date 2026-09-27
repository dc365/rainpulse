"""Actual modified stream-managed entry, with explicit parser/renderer doubles.

The verified ArtifactSession, cut cache, source receipt, budgets and generator
cleanup are real. These tests do not claim a MinIO/Zarr/production-QC replay.
"""
from types import SimpleNamespace as NS
from pathlib import Path
import copy,importlib,json,time
import numpy as np
import pytest
from rainpulse_algo.multiband.model import Volume
from rainpulse_algo.multiband.managed import VolumeCache
from rainpulse_algo.multiband.execution import ExecutionOptions
from test_fusion_rdr import scene
from test_input_reuse import fake_store


def harness(monkeypatch,tmp_path,schema='3.0',purpose='x_qc',fail_last=False):
    import rainpulse_algo.multiband.product as product
    # Test parser/renderer boundaries explicitly, NOT a substituted QC truth.
    monkeypatch.setattr(product,'MAX_X_QC_PREVIEW_BYTES',512*1024**2,raising=False)
    def render(volumes):
        seen=[]
        for v in volumes:
            s=v.sweeps[0];seen.append(s.number)
        return {'manifest.json':json.dumps({'comparison':{'sweeps':seen}}).encode()}
    monkeypatch.setattr(product,'x_qc_objects',render,raising=False)
    monkeypatch.setattr(product,'sx_comparison_objects',lambda *x:{'dummy':b'out'},raising=False)
    module=importlib.import_module('rainpulse_algo.multiband.stream_managed')
    monkeypatch.setattr(module,'x_qc_objects',render)
    reader,data,calls,marker,refresh=fake_store(schema)
    vols,network=scene(cuts=2);vols=[v for v in vols if v.metadata['band']=='X']
    by_number={v.sweeps[0].number:v for v in vols}
    st=network.stations['x1'];src={k:vols[0].metadata[k] for k in ('radar_id','scan_id','available_at','volume_start','volume_end')}
    src['input_uri']='s3://bucket/sample'
    options=ExecutionOptions(streaming=True,scratch_parent=str(tmp_path),layer_memory_bytes=1000000)
    executor=NS(network=network,execution=options,execution_policy_sha256='e'*64,
                reject_mask=7,flag_version='flag',cut_cache=VolumeCache(10**7,600))
    request={'payload':{'mode':purpose,'sources':[src], 'product_id':'demo',
             'analysis_time':'2026-08-28T00:06:00Z','input_cutoff':'2026-08-28T00:06:00Z'}}
    parse_count=[];qc_count=[]
    class Cuts:
        def __init__(self,*args,**kwargs):
            parse_count.append(1);self.numbers=[0,1];self.gates={k:v.sweeps[0].fields['DBZH'].size for k,v in by_number.items()}
        def read(self,n):
            if fail_last and n==1:raise ValueError('injected last decode')
            return copy.deepcopy(by_number[n])
        def close(self):pass
    monkeypatch.setattr(module,'NPZCuts',Cuts)
    monkeypatch.setattr('rainpulse_algo.multiband.managed.selected_source_keys',lambda *args,**kw:['a','b'])
    # native_bundle expects arrays.npz+volume.json. Adapt aliases only in this
    # test store wrapper; production staging itself is unchanged.
    original_open=reader.open
    def open_alias(uri):
        session=original_open(uri)
        old_staged=session.staged
        from contextlib import contextmanager
        @contextmanager
        def staged(**kwargs):
            with old_staged(**kwargs) as mapping:
                class Alias:
                    stats=mapping.stats
                    def __getitem__(self,k):return mapping['a' if k=='volume.json' else k]
                    def copy_logical_to(self,k,path):mapping.copy_logical_to('b',path)
                yield Alias()
        session.staged=staged
        return session
    monkeypatch.setattr(reader,'open',open_alias)
    def qc(v,*args):qc_count.append(1);return v
    monkeypatch.setattr(module,'x_qc',qc)
    return module,executor,request,reader,data,calls,parse_count,qc_count

@pytest.mark.parametrize('schema',['2.0','3.0'])
@pytest.mark.parametrize('purpose',['x_qc','sx_composite'])
def test_actual_entry_full_hit_skips_staging_after_fresh_marker(monkeypatch,tmp_path,schema,purpose):
    m,e,r,reader,data,calls,parses,qcs=harness(monkeypatch,tmp_path,schema,purpose)
    first=m.execute(e,r,reader,started=time.perf_counter())
    assert first[2]['qc_executions']==2 and len(parses)==1
    calls.clear()
    second=m.execute(e,r,reader,started=time.perf_counter())
    assert second[0]==first[0]
    assert second[2]['verified_source_cache_hits']==1 and second[2]['qc_executions']==0
    assert second[2]['packed_staged_bytes']==0
    assert calls==['sample/_SUCCESS.json'] and len(parses)==1
    assert not list(tmp_path.iterdir())
    del data['sample/_SUCCESS.json']
    with pytest.raises(FileNotFoundError):m.execute(e,r,reader,started=time.perf_counter())

@pytest.mark.parametrize('purpose',['x_qc','sx_composite'])
def test_last_read_failure_no_receipt_and_no_scratch(monkeypatch,tmp_path,purpose):
    m,e,r,reader,_,_,_,_=harness(monkeypatch,tmp_path,purpose=purpose,fail_last=True)
    with pytest.raises(ValueError,match='last decode'):m.execute(e,r,reader,started=time.perf_counter())
    assert not e._verified_source_inventory.entries
    assert not list(tmp_path.iterdir())


def test_output_failure_closes_generator_without_receipt(monkeypatch,tmp_path):
    m,e,r,reader,_,_,_,_=harness(monkeypatch,tmp_path)
    def broken(volumes):
        next(volumes)
        raise RuntimeError('encoder failed')
    monkeypatch.setattr(m,'x_qc_objects',broken)
    with pytest.raises(RuntimeError,match='encoder'):m.execute(e,r,reader,started=time.perf_counter())
    assert not e._verified_source_inventory.entries and not list(tmp_path.iterdir())
