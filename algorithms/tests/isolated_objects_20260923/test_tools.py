import copy,hashlib,json,sys
from pathlib import Path
import numpy as np
import pytest
import yaml
from iso_helpers import config,scene,base,ev
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
from make_isolated_object_profiles import generate
from audit_isolated_object_winners import check,run
from volume_review.clutter_fusion.disposition import apply


def parent(tmp_path,mode='quarantine'):
    v={'operational_eligible':False,'engine':'open_source','profile_version':'actual-parent',
       'nonprecip_review':{'near_enabled':True},'untouched_marker':['radial','near','background'],
       'volume_review':{'mode':'experiment_quarantine','phase':3,
                       'clutter_fusion':{'mode':mode,'near_revision':{'mode':'cr_withhold','strong_near':{
                           'mode':'quarantine','object_propagation':True,'temporal_low_rho':{'retrospective_boundary_enabled':True}}}}}}
    p=tmp_path/'parent.yaml';p.write_text(yaml.safe_dump(v));return p,v


def test_generator_preserves_all_parent_fields(tmp_path):
    p,old=parent(tmp_path);before=p.read_bytes();out=tmp_path/'profiles';records=generate(p,out,schema_only=True)
    assert len(records)==3 and p.read_bytes()==before
    for r in records:
        data=(out/r['file']).read_bytes();assert hashlib.sha256(data).hexdigest()==r['sha256']
        child=yaml.safe_load(data);child['profile_version']=old['profile_version']
        del child['volume_review']['clutter_fusion']['isolated_objects'];assert child==old
    g=json.loads((out/'generation.json').read_text());assert 'only' in g['validation_scope']


def test_generator_refuses_overwrite(tmp_path):
    p,_=parent(tmp_path);out=tmp_path/'exists';out.mkdir()
    with pytest.raises(FileExistsError):generate(p,out,schema_only=True)


@pytest.mark.parametrize('mode,expected',[('audit',1),('cr_withhold',2),('quarantine',3)])
def test_generator_never_escalates_parent(mode,expected,tmp_path):
    p,old=parent(tmp_path,mode)
    # Strong existing mode needs an active near owner but does not affect isolation.
    assert len(generate(p,tmp_path/'out',schema_only=True))==expected


def test_generator_requires_unbound_child(tmp_path):
    p,_=parent(tmp_path);out=tmp_path/'out';generate(p,out,schema_only=True)
    with pytest.raises(ValueError):generate(out/'isolated-objects-audit.yaml',tmp_path/'out2',schema_only=True)


def winner_fixture():
    s=scene();c=config('audit');out,_=apply(base(s),ev(s,c).arrays,c,low_quality_flag=1024)
    rr,gg=np.nonzero(s.observed)
    d={'CR_TRUSTED':out['DBZH_QC'][rr[:4],gg[:4]].reshape(2,2).copy(),
       'WINNER_SOURCE':np.zeros((2,2),'int32'),'WINNER_RAY':rr[:4].reshape(2,2).astype('int32'),
       'WINNER_GATE':gg[:4].reshape(2,2).astype('int32')}
    return d,{0:out},rr,gg


def test_winner_audit_rebuilds_actual_value():
    d,src,_,_=winner_fixture();out=check(d,src)
    assert out['matched_winner_cells']==4 and out['eligibility_leaks']==0


@pytest.mark.parametrize('fault',['isolation','eligible','value','index','parent','flags','missing_source'])
def test_winner_audit_rejects_leaks(fault):
    d,src,rr,gg=winner_fixture();a=src[0];ix=(rr[0],gg[0]);reject=0
    if fault=='isolation':a['CF_ISO_CR_WITHHELD_MASK'][ix]=1
    if fault=='eligible':a['REFLECTIVITY_ELIGIBLE_FOR_CR'][ix]=0
    if fault=='value':d['CR_TRUSTED'][0,0]+=1
    if fault=='index':d['WINNER_RAY'][0,0]=-1
    if fault=='parent':a['CF_CR_WITHHELD_MASK'][ix]=1
    if fault=='flags':a['QC_FLAGS'][ix]=1024;reject=1024
    if fault=='missing_source':src={}
    with pytest.raises(ValueError):check(d,src,reject_mask=reject)


def test_bound_manifest_checks_bytes(tmp_path):
    d,src,_,_=winner_fixture();np.savez_compressed(tmp_path/'cr.npz',**d);np.savez_compressed(tmp_path/'s.npz',**src[0])
    def entry(p):return {'path':p,'sha256':hashlib.sha256((tmp_path/p).read_bytes()).hexdigest()}
    m={'schema':'isolated-winner-bundle-v1','reject_mask':1024,'composite':entry('cr.npz'),
       'sources':[{**entry('s.npz'),'source_index':0,'qc_asset_sha256':'a'*64,'qc_parameters_sha256':'b'*64}]}
    p=tmp_path/'manifest.json';p.write_text(json.dumps(m));assert run(p)['matched_winner_cells']==4
    (tmp_path/'cr.npz').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash mismatch'):run(p)
