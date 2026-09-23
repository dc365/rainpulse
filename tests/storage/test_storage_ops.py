"""S3 protocol/fault tests use a version-aware fake, not a live MinIO claim."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("storage_ops", ROOT / "scripts/storage_ops.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def uid(): return str(uuid4())

def plan_fixture():
    run, task, attempt = uid(), uid(), uid()
    prefix = f"s3://rainpulse/operations/{run}/{task}/attempts/{attempt}/"
    return {"id": uid(), "digest": "a"*64, "scope": "managed_candidates_only",
        "object_store_endpoint": "http://minio:9000", "targets": [
            {"run_id": run, "attempts": [{"id": attempt, "task_id": task, "kind": "qc", "prefix": prefix}]}]}


class Control:
    def __init__(self, plan, state="PREVIEW"):
        self.plan, self.state = copy.deepcopy(plan), state
        self.calls = []
    def call(self, path, value=None):
        self.calls.append((path,value))
        if value is None: return {"plan": self.plan, "state": self.state}
        if path.endswith("/begin"):
            self.state = "DELETING"; return self.plan
        self.state = "COMPLETE" if all(x['empty'] for x in value['prefixes']) else "ERROR"
        return {"ok": True}


class S3:
    def __init__(self, plan):
        self.plan=plan; self.removed=[]; self.fail=False;self.late=False
        a=plan["targets"][0]["attempts"][0]; key=a["prefix"].removeprefix("s3://rainpulse/")+"qc.zarr/_objects/"+"b"*64+"/chunk"
        self.items=[SimpleNamespace(object_name=key,version_id='old',size=2,is_delete_marker=False),
                    SimpleNamespace(object_name=key,version_id='new',size=3,is_delete_marker=False),
                    SimpleNamespace(object_name=key,version_id='delete',size=0,is_delete_marker=True)]
    def get_bucket_versioning(self,bucket): return SimpleNamespace(status="Enabled")
    def list_objects(self,bucket,*,prefix,recursive,include_version):
        assert bucket=="rainpulse" and recursive and include_version
        if self.late and self.removed: return iter([SimpleNamespace(object_name=prefix+'late',version_id='late')])
        return iter(list(self.items))
    def remove_object(self,bucket,key,*,version_id):
        assert bucket=="rainpulse" and version_id
        if self.fail: raise OSError("failure without secret")
        self.removed.append((key,version_id));self.items=[x for x in self.items if (x.object_name,x.version_id)!=(key,version_id)]


def test_exact_version_purge_and_postcheck(tmp_path):
    p=plan_fixture();control=Control(p);s3=S3(p)
    r=m.purge(p,control,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'receipt.json')
    assert {x[1] for x in s3.removed}=={'old','new','delete'}
    assert r['prefixes'][0]['empty'] and control.state=='COMPLETE'
    assert r['prefixes'][0]['deleted_versions']==3
    assert json.loads((tmp_path/'receipt.json').read_text())['receipt']==r


@pytest.mark.parametrize('prefix', ['s3://rainpulse/radar/', 's3://rainpulse/operations/', 's3://other/operations/', 'file:///data/minio', 's3://rainpulse/operations/../'])
def test_refuse_broad_or_raw_prefixes(prefix):
    p=plan_fixture();p['targets'][0]['attempts'][0]['prefix']=prefix
    with pytest.raises(ValueError): m.validate_plan(p)


@pytest.mark.parametrize('confirm,stopped', [('wrong',True), ('a'*64,False)])
def test_requires_digest_and_stopped_confirmation(tmp_path,confirm,stopped):
    p=plan_fixture();s3=S3(p);c=Control(p)
    with pytest.raises(ValueError):m.purge(p,c,s3,confirm=confirm,workers_stopped=stopped,receipt_path=tmp_path/'r')
    assert not s3.removed and not c.calls


def test_plan_tamper_rejected_before_retirement(tmp_path):
    p=plan_fixture();c=Control(p);local=copy.deepcopy(p);local['targets'][0]['attempts'][0]['marker_sha256']='c'*64
    with pytest.raises(ValueError):m.purge(local,c,S3(p),confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert not any(path.endswith('/begin') for path,_ in c.calls)


@pytest.mark.parametrize('mutation', ['prefix','version','layout','budget'])
def test_bad_inventory_prevents_any_deletion(tmp_path,mutation):
    p=plan_fixture();s3=S3(p);c=Control(p)
    if mutation=='prefix':s3.items[0].object_name='radar/raw'
    if mutation=='version':s3.items[0].version_id=None
    if mutation=='layout':s3.items[0].object_name=s3.items[0].object_name.rsplit('/',1)[0]+'/../foreign'
    kw={'max_objects':1} if mutation=='budget' else {}
    with pytest.raises(ValueError):m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r',**kw)
    assert not s3.removed and not any(path.endswith('/begin') for path,_ in c.calls)


def test_failure_receipt_keeps_tombstone_and_allows_resume(tmp_path):
    p=plan_fixture();s3=S3(p);s3.fail=True;c=Control(p)
    first=m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert not first['prefixes'][0]['empty'] and c.state=='ERROR'
    s3.fail=False
    done=m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert done['prefixes'][0]['empty'] and c.state=='COMPLETE'


def test_late_write_never_reported_empty(tmp_path):
    p=plan_fixture();s3=S3(p);s3.late=True;c=Control(p)
    r=m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert not r['prefixes'][0]['empty'] and c.state=='ERROR'


def test_inspection_uses_real_statvfs(tmp_path):
    r=m.inspect_filesystem(tmp_path,'test-data','data')
    assert r['total_bytes']>0 and r['available_bytes']<=r['total_bytes']
    if r['inodes_total']:assert 0<=r['inode_used_percent']<=100


def test_directory_sampling_bounded_no_symlink_follow(tmp_path):
    (tmp_path/'d').mkdir();(tmp_path/'d/f').write_bytes(b'a');(tmp_path/'loop').symlink_to(tmp_path,target_is_directory=True)
    assert m.sample_entries(tmp_path)['sampled_entries']==3
    assert m.sample_entries(tmp_path,max_entries=1)['complete'] is False


def test_atomic_report_and_no_unintended_overwrite(tmp_path):
    p=tmp_path/'report';m.write_json(p,{'x':1})
    with pytest.raises(ValueError):m.write_json(p,{'x':2})
    assert m.load_json(p)=={'x':1}


@pytest.mark.parametrize('url', ['http://user:secret@host','s3://rainpulse','http://host/path','http://host/?secret=x'])
def test_endpoint_has_no_credentials_or_extra_paths(url):
    with pytest.raises(ValueError):m.endpoint_identity(url)


def test_known_marker_missing_stops_fresh_cleanup(tmp_path):
    p=plan_fixture();p['targets'][0]['attempts'][0]['marker_sha256']='a'*64;c=Control(p);s3=S3(p)
    with pytest.raises(ValueError):m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert not s3.removed


def test_resuming_refuses_unrelated_receipt(tmp_path):
    p=plan_fixture();c=Control(p,'ERROR');m.write_json(tmp_path/'r',{'plan_id':uid(),'receipt':{'digest':'bad'}})
    with pytest.raises(ValueError):m.purge(p,c,S3(p),confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')


def test_completed_plan_not_reexecuted(tmp_path):
    p=plan_fixture();c=Control(p,'COMPLETE');s3=S3(p)
    with pytest.raises(ValueError):m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert not s3.removed


def attach_marker(p, s3):
    import hashlib
    import io
    a = p['targets'][0]['attempts'][0]
    raw = json.dumps({'completion_event': {'run_id': p['targets'][0]['run_id'], 'job_id': a['task_id'],
        'payload': {'diagnostics': {'operations': {'attempt_id': a['id'], 'candidate_only': True}}}}}).encode()
    a['marker_sha256'] = hashlib.sha256(raw).hexdigest()
    key = a['prefix'].removeprefix('s3://rainpulse/') + 'qc.zarr/_SUCCESS.json'
    s3.items.append(SimpleNamespace(object_name=key,version_id='marker',size=len(raw),is_delete_marker=False,is_latest=True))
    def get_object(bucket, name):
        assert name == key
        response = io.BytesIO(raw); response.release_conn = lambda: None
        return response
    s3.get_object = get_object
    return key


def test_verified_success_marker_is_deleted_last(tmp_path):
    p=plan_fixture();s3=S3(p);key=attach_marker(p,s3);c=Control(p)
    result=m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert s3.removed[-1] == (key,'marker')
    assert result['prefixes'][0]['empty']


def test_marker_content_drift_rejected_before_begin(tmp_path):
    p=plan_fixture();s3=S3(p);attach_marker(p,s3);p['targets'][0]['attempts'][0]['marker_sha256']='f'*64;c=Control(p)
    with pytest.raises(ValueError,match='marker changed'):
        m.purge(p,c,s3,confirm=p['digest'],workers_stopped=True,receipt_path=tmp_path/'r')
    assert not s3.removed and not any(path.endswith('/begin') for path,_ in c.calls)


def test_management_qc_default_packing_and_explicit_optout():
    path=ROOT/'tests/operations/test_deployment.py'
    loader=importlib.util.spec_from_file_location('storage_deployment_fixture',path)
    fixture=importlib.util.module_from_spec(loader);loader.loader.exec_module(fixture)
    output=fixture.build()['services']
    assert output['ops-qc-worker']['environment']['RAINPULSE_QC_PACKED_STORAGE']=='1'
    assert 'RAINPULSE_QC_PACKED_STORAGE' not in output['ops-render-worker']['environment']
    source=fixture.model();source['services']['radar-qc-worker']['environment']['RAINPULSE_QC_PACKED_STORAGE']='0'
    assert fixture.build(source)['services']['ops-qc-worker']['environment']['RAINPULSE_QC_PACKED_STORAGE']=='0'
