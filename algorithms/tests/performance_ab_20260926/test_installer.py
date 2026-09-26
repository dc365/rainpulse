import json
import subprocess
import pytest
from . import installer_reference as ins


def repo(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    (root/'base.py').write_text('"""base"""\nfrom __future__ import annotations\n\ndef f(x):\n    return x + 1\n')
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@invalid','commit','-qm','base'],check=True)
    return root


def test_transform_future_async_and_method():
    raw=b'"""doc"""\nfrom __future__ import annotations\nclass X:\n    @staticmethod\n    async def f(x):\n        return x\n'
    out=ins.transform(raw,{'decorators':{'X.f':{'label':'call','root':True}}}).decode()
    assert out.index('from __future__')<out.index('from rainpulse_algo.performance')
    assert '@staticmethod\n    @_perf_timed' in out
    compile(out,'x','exec')


@pytest.mark.parametrize('bad',['missing','repeat','syntax'])
def test_transform_rejects(bad):
    raw=b'def f():\n    return 1\n'
    r=({'replacements':[['absent','x']]} if bad=='missing' else
       {'replacements':[['return 1','return (']]} if bad=='syntax' else
       {'replacements':[['return 1','return 2',2]]})
    with pytest.raises((ValueError,SyntaxError)):ins.transform(raw,r)


def test_full_apply_rollback(tmp_path):
    root=repo(tmp_path);old=(root/'base.py').read_bytes();new=ins.transform(old,{'decorators':{'f':{'label':'function'}}})
    changes={'base.py':(old,new),'new/module.py':(None,b'value=1\n')}
    receipt=ins.apply(root,changes,'test')
    assert (root/'base.py').read_bytes()==new and (root/'new/module.py').exists()
    ins.rollback(root,receipt)
    assert (root/'base.py').read_bytes()==old and not (root/'new/module.py').exists()


def test_rollback_does_not_overwrite_subsequent_edit(tmp_path):
    root=repo(tmp_path);old=(root/'base.py').read_bytes()
    receipt=ins.apply(root,{'base.py':(old,b'a=1\n')},'test')
    (root/'base.py').write_text('user=2\n')
    with pytest.raises(ValueError):ins.rollback(root,receipt)
    assert (root/'base.py').read_text()=='user=2\n'


def test_failure_restores_original(tmp_path,monkeypatch):
    root=repo(tmp_path);old=(root/'base.py').read_bytes();real=ins.write_atomic
    def failing(path,data,mode=0o644):
        if path==root/'other.py':raise OSError('disk full')
        real(path,data,mode)
    monkeypatch.setattr(ins,'write_atomic',failing)
    with pytest.raises(OSError):ins.apply(root,{'base.py':(old,b'x=1\n'),'other.py':(None,b'y=2\n')},'test')
    assert (root/'base.py').read_bytes()==old and not (root/'other.py').exists()
    assert not (root/'.rainpulse-performance-ab.lock').exists()


@pytest.mark.parametrize('path',['../escape','/absolute','a/../b','a\\b'])
def test_path_refusal(tmp_path,path):
    with pytest.raises(ValueError):ins.safe(tmp_path,path)


def test_symlink_refusal(tmp_path):
    (tmp_path/'a').symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError):ins.safe(tmp_path,'a/file.py')


def test_package_plan_hashes_and_complete_diff(tmp_path):
    root=repo(tmp_path);package=tmp_path/'pkg';(package/'recipes').mkdir(parents=True);(package/'payload').mkdir()
    head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    old=(root/'base.py').read_bytes()
    recipe=json.dumps({'base.py':{'git_blob':ins.blob(old),'decorators':{'f':{'label':'work'}}}}).encode()
    (package/'recipes/changes.json').write_bytes(recipe);(package/'payload/new.py').write_bytes(b'x=1\n')
    manifest={'schema':'rainpulse.performance-ab-delivery-v1','base_commit':head,'package_id':'test',
              'recipes_sha256':ins.sha(recipe),'payloads':{'new.py':ins.sha(b'x=1\n')}}
    (package/'manifest.json').write_text(json.dumps(manifest))
    changes=ins.Package(package).plan(root)
    assert '@_perf_timed' in ins.patch(changes) and '+++ b/new.py' in ins.patch(changes)
    patch=tmp_path/'changes.patch';patch.write_text(ins.patch(changes))
    subprocess.run(['git','-C',str(root),'apply','--check',str(patch)],check=True)
    (root/'base.py').write_bytes(old+b'\n')
    with pytest.raises(ValueError):ins.Package(package).plan(root)


def test_new_path_and_lock_protection(tmp_path):
    root=repo(tmp_path);old=(root/'base.py').read_bytes()
    (root/'.rainpulse-performance-ab.lock').mkdir()
    with pytest.raises(FileExistsError):ins.apply(root,{'base.py':(old,b'x=1\n')},'test')
    assert (root/'base.py').read_bytes()==old
