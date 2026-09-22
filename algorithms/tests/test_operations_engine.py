# ruff: noqa: E501, E701, E702, I001
"""Tests execute the production engine with transport and algorithm interfaces."""
import copy
import hashlib
import io
import json

import numpy as np
import pytest

from rainpulse_algo.operations.engine import AttemptJournal, Engine, Tee, process_metrics
from rainpulse_algo.operations.native import NativeAdapter, capture_identity
from rainpulse_algo.operations.protocol import (
    ConfigurationChanged, ControlClient, ControlError, FrozenInputChanged,
    MarkerResponse, PinnedClient, redact, retry_io, transient,
)

CLAIM = {"task_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "attempt_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "token":"a"*64, "identity":{"fingerprint":"b"*64}, "request":{}, "inputs":[]}

class Control:
    def __init__(self, *, stop_stage=None, fail_finish=0, fence=False):
        self.calls=[];self.stop_stage=stop_stage;self.fail_finish=fail_finish;self.fence=fence
    def post(self, route, payload):
        self.calls.append((route,copy.deepcopy(payload)))
        if self.fence: raise ControlError(409,"stale_attempt")
        if route=="finish" and self.fail_finish:
            self.fail_finish-=1;raise ControlError(503,"unavailable")
        return {"stop_requested":payload.get("stage")==self.stop_stage}
    @property
    def outcomes(self):return [p["outcome"] for r,p in self.calls if r=="finish"]

class Adapter:
    def __init__(self, *, error=None, published=False, upload_error=None, identity_error=None):
        self.error=error;self.published=published;self.upload_error=upload_error;self.identity_error=identity_error
        self.executions=0;self.uploads=0;self.checks=0
    def check_identity(self, expected):
        self.checks+=1
        if self.identity_error:raise self.identity_error
    def existing(self, claim):return self.published
    def execute(self, claim):
        self.executions+=1
        print('native algorithm output: token=do-not-log')
        if self.error:raise self.error
        return {"rss_bytes":1234.0,"qc_core_ms":3.0,"bad":float("nan")}
    def metrics(self,result):return result
    def publish(self,claim,result,started):
        self.uploads+=1
        if self.upload_error:raise self.upload_error
        self.published=True

def run(c,a):return Engine(c,a,interval=3600,sleep=lambda _:None).run(copy.deepcopy(CLAIM))

def test_real_engine_completes_once_and_records_stages():
    c,a=Control(),Adapter();assert run(c,a)
    assert a.executions==a.uploads==1;assert c.outcomes==["SUCCEEDED"]
    stages={p['stage'] for r,p in c.calls if r=='heartbeat'}
    assert {'VERIFY_INPUT','COMPUTE','UPLOAD','COMMIT'}<=stages
    metrics=[p['metrics'] for r,p in c.calls if r=='finish'][0]
    assert 'qc_core_ms' in metrics and 'bad' not in metrics and 'rss_bytes' not in metrics
    assert 'do-not-log' not in json.dumps(c.calls)

def test_successful_marker_replays_registration_without_computation():
    c,a=Control(),Adapter(published=True);assert run(c,a)
    assert a.executions==a.uploads==0;assert c.outcomes==['SUCCEEDED']

@pytest.mark.parametrize('error',[ValueError('numeric bug'),OSError('reader failed'),RuntimeError('native failure')])
def test_numerical_executor_is_never_automatically_retried(error):
    c,a=Control(),Adapter(error=error);assert run(c,a)
    assert a.executions==1 and a.uploads==0 and c.outcomes==['FAILED']
    assert any(line['event']=='attempt.traceback' for r,p in c.calls for line in p.get('lines',[]))

@pytest.mark.parametrize('error',[FrozenInputChanged('changed marker'),ConfigurationChanged('changed config')])
def test_frozen_identity_errors_are_blocked_not_optional_transport(error):
    c,a=Control(),Adapter(error=error);assert run(c,a)
    assert c.outcomes==['BLOCKED']
    assert not isinstance(error,(OSError,RuntimeError))

def test_configuration_drift_prevents_compute():
    c,a=Control(),Adapter(identity_error=ConfigurationChanged('changed'))
    assert run(c,a);assert a.executions==0 and c.outcomes==['BLOCKED']

@pytest.mark.parametrize('stage',['VERIFY_INPUT','COMPUTE','UPLOAD'])
def test_cancellation_at_checkpoints_never_publishes(stage):
    c,a=Control(stop_stage=stage),Adapter();assert run(c,a)
    assert a.uploads==0 and c.outcomes==['CANCELLED']
    assert a.executions==(1 if stage=='UPLOAD' else 0)

def test_lost_finish_receipt_retries_receipt_not_numerical_work():
    c,a=Control(fail_finish=2),Adapter();assert run(c,a)
    assert c.outcomes==['SUCCEEDED']*3 and a.executions==1 and a.uploads==1

def test_unavailable_result_registration_remains_recoverable():
    c,a=Control(fail_finish=100),Adapter();assert not run(c,a)
    assert a.published and c.outcomes==['SUCCEEDED']*3 and a.executions==1

@pytest.mark.parametrize('error',[ConnectionError('lost PUT response'),RuntimeError('publish integrity failure')])
def test_publication_error_does_not_record_a_false_numeric_failure(error):
    c,a=Control(),Adapter(upload_error=error);assert not run(c,a)
    assert a.executions==1 and c.outcomes==[]

def test_fenced_attempt_does_not_execute_or_publish():
    c,a=Control(fence=True),Adapter();assert run(c,a)
    assert a.executions==a.uploads==0

def test_stable_log_sequences_across_unacknowledged_retries():
    j=AttemptJournal(3)
    for n in range(6):j.add('info','line',str(n))
    first=j.batch();assert len(first)==3;assert j.batch()==first
    j.acknowledged(first);overflow=j.batch();assert len(overflow)==1
    assert overflow[0]['event']=='logs.client_overflow' and overflow[0]['sequence']==4
    assert '3条' in overflow[0]['message']

def test_tee_redacts_and_bounds_without_modifying_numeric_values():
    j=AttemptJournal();stream=io.StringIO();t=Tee(stream,j)
    t.write('password=hidden\n');t.write('x'*9000);t.finish()
    assert 'hidden' not in stream.getvalue()
    assert all(len(l['message'])<=4096 for l in j.batch())

def test_io_retries_are_bounded():
    attempts=[];delays=[]
    def fail():attempts.append(1);raise ConnectionError('offline')
    with pytest.raises(ConnectionError):retry_io(fail,sleep=delays.append)
    assert len(attempts)==3 and delays==[1,2]

@pytest.mark.parametrize('status,expected',[(400,False),(401,False),(403,False),(409,False),(429,True),(500,True),(503,True)])
def test_transport_classification(status,expected):assert transient(ControlError(status,'error')) is expected

@pytest.mark.parametrize('base',['file:///etc/passwd','http://u:p@host','http://host/?token=bad','not-a-url','http://host/internal/ops/v1'])
def test_control_url_rejects_credentials_redirect_targets_and_non_http(base):
    with pytest.raises(ValueError):ControlClient(base,'secret')

def test_redaction_keeps_identifiers_and_hides_known_secrets(monkeypatch):
    monkeypatch.setenv('RAINPULSE_OPS_WORKER_TOKEN','bare-value-secret')
    out=redact('job_id=uuid token=secret password=p Bearer ABCD http://user:pass@host bare-value-secret')
    assert 'job_id=uuid' in out
    assert all(v not in out for v in ['token=secret','password=p','ABCD','user:pass','bare-value-secret'])

class Objects:
    def __init__(self,data):self.data=data;self.calls=[]
    def get_object(self,bucket,key,*args,**kwargs):self.calls.append((bucket,key));return MarkerResponse(self.data)

def test_marker_pin_does_not_download_arrays_twice():
    data=b'{"metadata":"frozen"}';client=Objects(data)
    pinned=PinnedClient(client,[{'uri':'s3://bucket/artifact','marker_sha256':hashlib.sha256(data).hexdigest()}])
    assert pinned.get_object('bucket','artifact/_SUCCESS.json').read()==data
    assert len(client.calls)==1
    client.data=b'changed'
    with pytest.raises(FrozenInputChanged):pinned.get_object('bucket','artifact/_SUCCESS.json')

def test_unrelated_object_reads_remain_the_existing_reader_responsibility():
    c=Objects(b'chunk');assert PinnedClient(c,[]).get_object('bucket','x/0.0').read()==b'chunk'

def test_capture_identity_and_drift(tmp_path,monkeypatch):
    f=tmp_path/'flags.yaml';f.write_text('definition_version: qc-flags-v2\n')
    monkeypatch.setenv('RAINPULSE_QC_FLAG_DEFINITIONS',str(f))
    first=capture_identity('render');assert first==capture_identity('render')
    assert len(first['fingerprint'])==64
    adapter=object.__new__(NativeAdapter);adapter.kind='render';adapter.startup=first
    adapter.check_identity(first)
    f.write_text('definition_version: qc-flags-v2\n# changed\n')
    with pytest.raises(ConfigurationChanged):adapter.check_identity(first)

def test_process_metrics_label_lifetime_peak_honestly():
    value=process_metrics();assert 'rss_bytes' not in value
    assert all(v>=0 for v in value.values())


def test_wrapped_frozen_context_is_still_blocked():
    try:
        raise FrozenInputChanged("secondary marker changed")
    except FrozenInputChanged as cause:
        error = RuntimeError("native stage failed")
        error.__cause__ = cause
    c,a=Control(),Adapter(error=error)
    assert run(c,a) and c.outcomes==["BLOCKED"]

@pytest.mark.parametrize("version",["qc-flags-v1","qc-flags-v2"])
def test_preview_support_preserves_missing_and_weather_values(version):
    from rainpulse_algo.operations.preview_mask import business_support
    values=np.array([[0.,15.,np.nan,30.,40.]])
    flags=np.array([[0,0,0,1,0]],dtype=np.uint32)
    original=values.copy();saved_flags=flags.copy()
    support=business_support(values,flags,{"GROUND_CLUTTER":1},flag_version=version,eligible=np.array([[1,1,0,1,0]]))
    assert support[0,:4].tolist()==[True,True,False,False]
    assert support[0,4]==(version=="qc-flags-v1")
    np.testing.assert_array_equal(values,original)
    np.testing.assert_array_equal(flags,saved_flags)

def test_v2_preview_refuses_missing_eligibility():
    from rainpulse_algo.operations.preview_mask import business_support
    with pytest.raises(ValueError):business_support([1.],[0],{},flag_version="qc-flags-v2")
