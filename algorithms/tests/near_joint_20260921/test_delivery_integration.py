from pathlib import Path
from types import SimpleNamespace as NS
from datetime import datetime,timezone
import copy,hashlib,importlib.util,json
import numpy as np
import pytest,yaml
from fusion_helpers import scene,baseline,cfg,fixture,group,Native
from test_near_joint import config,partial,dem
from volume_review.config import VolumeReviewConfig
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.disposition import apply,CR
from volume_review.clutter_fusion.integration import attributes
from volume_review.clutter_fusion.classifier import decide
from volume_review.clutter_fusion import terrain_admission as ta
from volume_review.integration import root_attributes
from volume_review.composite import build_composite,trace_pixel
from volume_review.receipts import npz_bytes
from volume_review.data import array_digest
from volume_review.clutter_fusion.near_runtime import prepare,RuntimeContext
from test_cf_integration import Root

ROOT=Path(__file__).resolve().parents[3]
def load_script(name):
    s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def parent():
    from volume_review.receiver_domain.config import ReceiverDomainConfig
    return {'engine':'open_source','operational_eligible':False,'profile_version':'actual-parent',
       'pipeline_version':'qc-opensource-7.3.9','echo':{'no_rain_below_dbz':-10.},
       'nonprecip_review':{'mode':'experiment_quarantine','quarantine_classes':['fixed_ground']},
       'volume_review':VolumeReviewConfig(mode='experiment_quarantine',clutter_fusion=cfg(mode='quarantine'),
             receiver_domain=ReceiverDomainConfig()).model_dump(mode='json')}

def test_generated_candidates_enable_existing_near_and_preserve_radials(tmp_path):
    v=parent();p=tmp_path/'parent.yaml';raw=yaml.safe_dump(v).encode();p.write_bytes(raw)
    m=load_script('make_near_joint_profiles');recs=m.generate(p,tmp_path/'children',validate_full=False)
    assert len(recs)==7 and p.read_bytes()==raw
    assert {r['strong_near_mode'] for r in recs}=={None,'audit','quarantine'}
    assert (tmp_path/'children').stat().st_mode&0o777==0o755
    for rec in recs:
        b=(tmp_path/'children'/rec['file']).read_bytes();c=yaml.safe_load(b)
        assert hashlib.sha256(b).hexdigest()==rec['sha256']
        assert c['pipeline_version']==v['pipeline_version']
        assert c['volume_review']['receiver_domain']==v['volume_review']['receiver_domain']
        assert c['volume_review']['clutter_fusion']['background']==v['volume_review']['clutter_fusion']['background']
        if rec['file']!='near-joint-audit.yaml':
            assert c['nonprecip_review']['near_enabled'] is True
            assert 'near_nonmet' in c['nonprecip_review']['quarantine_classes']
            assert c['nonprecip_review']['near_reliability']['maximum_abs_zdr_db']==7.5
        else:assert c['nonprecip_review']==v['nonprecip_review']
        VolumeReviewConfig.model_validate(c['volume_review'])
    with pytest.raises(ValueError):m.generate(p,tmp_path/'children',validate_full=False)

@pytest.mark.parametrize('fault',['operational','no_cf','no_np','old_phase','has_nr','audit_cf'])
def test_profile_incompatible_parent_no_partial_write(tmp_path,fault):
    v=parent()
    if fault=='operational':v['operational_eligible']=True
    if fault=='no_cf':v['volume_review'].pop('clutter_fusion')
    if fault=='no_np':v.pop('nonprecip_review')
    if fault=='old_phase':v['volume_review']['phase']=1
    if fault=='has_nr':v['volume_review']['clutter_fusion']['near_revision']={}
    if fault=='audit_cf':v['volume_review']['clutter_fusion']['mode']='audit'
    p=tmp_path/'p.yaml';p.write_text(yaml.safe_dump(v))
    with pytest.raises(ValueError):load_script('make_near_joint_profiles').generate(p,tmp_path/'out',validate_full=False)
    assert not (tmp_path/'out').exists()

def test_background_diagnostics_are_real_model_arrays():
    from test_cf_background import build,sample,sweep
    from volume_review.clutter_fusion.background import compare
    from volume_review.episode_background.core import evaluate
    c=config();model=build();s=sample(30);expected=evaluate(s,model,c.background)
    a,_=compare(s,model,c)
    for k in ('STATE','REASON','DBZH_DEPARTURE_DB','MAP_AZ_ERROR_DEG','MAP_RANGE_ERROR_M'):
        np.testing.assert_equal(a['CF_BG_'+k],expected.arrays['EBG_'+k])
    e=evaluate_volume([sweep(s)],c,backgrounds=[(a,{})])[0]
    assert e.arrays['CF_BG_MATCH_MASK'].any()

def test_missing_background_state_is_unavailable_not_clean():
    e=evaluate_volume([partial()],config())[0].arrays
    assert not e['CF_BG_AVAILABLE_MASK'].any();assert np.isnan(e['CF_BG_DBZH_DEPARTURE_DB']).all()

def near_root(use_dem=False):
    s=partial();c=config(partial_enabled=not use_dem,dem_policy='cr_withhold')
    e=evaluate_volume([s],c)[0].arrays
    if use_dem:e.update(dem(s,c));e.update(decide(e,c))
    a,_=apply(baseline(s),e,c,low_quality_flag=1024)
    vp=VolumeReviewConfig(mode='experiment_quarantine',clutter_fusion=c)
    r=Root({**attributes(c,1024),'qc_volume_review_sha256':vp.digest,'qc_volume_review_phase':3,
       'qc_volume_review_mode':'experiment_quarantine','radar_id':'TEST','scan_id':'scan','asset_id':'asset',
       'site_longitude_deg':0.,'site_latitude_deg':0.,'qc_parameters_sha256':'a'*64})
    r['sweep_number']=np.array([0],dtype='int32');r['sweep_000']={**a,'azimuth':s.azimuth,'range':s.ranges,'elevation':s.elevation}
    return r,c

@pytest.mark.parametrize('dem_mode',[False,True])
def test_cr_consumes_gate_qualification_and_persists_new_risk(dem_mode):
    r,c=near_root(dem_mode);p=build_composite([r],0,maximum_size=64)
    key='CR_TERRAIN_UNRELIABLE' if dem_mode else 'CR_NEAR_JOINT_WITHHELD'
    assert np.isfinite(p.arrays[key]).any()
    for row,col in np.argwhere(np.isfinite(p.arrays['CR_TRUSTED'])):
        assert trace_pixel(p,[r],int(row),int(col))['status']=='RECONSTRUCTED'
    assert not r['sweep_000']['CF_QUARANTINE_MASK'].any()

def test_nr_withheld_cannot_leak_through_other_cr_flags():
    r,c=near_root();a=r['sweep_000'];a[CR][a['CF_NR_CR_WITHHELD_MASK']==1]=1
    with pytest.raises(ValueError,match='leaked'):build_composite([r],0,maximum_size=64)

def test_full_qc_audit_and_exact_winner_reconstruction(tmp_path):
    r,c=near_root();m=load_script('audit_near_joint')
    assert m.audit(r)['status']=='CHECKED_NO_ELIGIBILITY_LEAK'
    p=build_composite([r],0,maximum_size=64);payload=npz_bytes(p.arrays)
    npz=tmp_path/'cr.npz';npz.write_bytes(payload)
    meta=tmp_path/'cr.json';meta.write_text(json.dumps({'payload_sha256':hashlib.sha256(payload).hexdigest(),
                        'numeric_sha256':array_digest(p.arrays),'sources':p.sources}))
    assert m.check_composite([r],npz,meta)['status']=='RECONSTRUCTED'
    r['sweep_000']['DBZH_QC'][1,1]+=1
    with pytest.raises(ValueError,match='source numeric'):m.check_composite([r],npz,meta)


def test_terrain_sampler_uses_ground_coordinates_and_verified_primitives():
    from volume_review.clutter_fusion.context import height
    s=partial();c=config();calls=[]
    class Terrain:
        cache_identity='test-dem-hash'
        def sample(self,lon,lat):calls.append((lon.copy(),lat.copy()));return np.full(len(lon),1000.)
    def centre(r,e,alt,bc):assert bc.earth_radius_m==6371000.;return height(r,e)+alt
    def radius(r,width):return r*np.tan(np.deg2rad(width/2))
    def pbb(t,h,rad):return (t>=h).astype(float)
    beam=NS(longitude_deg=119.,latitude_deg=26.,antenna_altitude_m=100.,beam_width_vertical_deg=1.,
            altitude_datum_status='verified_egm2008',radar_config_version='p1')
    a,d=ta.from_sampler(s,c,beam,Terrain(),'dem-v1',primitives=(centre,radius,pbb))
    assert a['CF_NR_DEM_SEVERE_MASK'].any() and d['height_datum']=='EGM2008'
    assert len(calls)==1 and len(calls[0][0])==np.prod(s.shape)


def test_runtime_absent_stage_no_work():
    assert prepare(NS(volume_review=None),None,None,None,None,None,None,None) is None

def test_runtime_oversized_context_abstains_without_adapting_sources():
    c=config(maximum_previous_gates=1);profile=NS(volume_review=NS(clutter_fusion=c))
    r=Root({'radar_id':'a','scan_id':'s','radar_config_version':'p'});r['sweep_number']=np.array([0]);r['sweep_000']={'DBZH':np.zeros((3,4))}
    ctx=prepare(profile,r,[(r,None,None)],None,None,None,datetime.now(timezone.utc),[])
    assert ctx.abstention_reason and not ctx.past
    e=evaluate_volume([partial()],c,near_context=ctx)[0]
    assert e.summary['near_revision']['status']=='RESOURCE_LIMIT_ABSTAINED'

@pytest.mark.parametrize('station,proc',[('b','p'),('a','q')])
def test_wrong_station_or_processor_dem_is_rejected(station,proc):
    r=Root({'radar_id':'a','scan_id':'s','radar_config_version':'p'})
    with pytest.raises(ValueError,match='identity|radar'):
        prepare(NS(volume_review=NS(clutter_fusion=config())),r,[],
                NS(radar_id=station,radar_config_version=proc),None,None,datetime.now(timezone.utc),[])
