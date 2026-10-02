"""Reject residual selections that do not match immutable published native data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'algorithms'))
spec=importlib.util.spec_from_file_location('residual_shapes',ROOT/'scripts/audit_s_published_residual_shapes.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def inputs(tmp_path):
    snapshot=tmp_path/'raw.npz';receipt=tmp_path/'published.json'
    np.savez(snapshot,METADATA=np.array(json.dumps({'scan_id':'original'})),RAW=np.array([[25.]]),
        AZIMUTH=np.array([30.]),RANGE=np.array([100000.]))
    report=dict(scope='exact_Web_consumed_stored_QC_not_replay',
        snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        web_frame_identity={'web_scan_id':'original'},target_gates=1,renderer_eligible_visible_gates=1,
        target_records=[dict(row=0,column=0,raw_dbzh=25.,azimuth_deg=30.,range_m=100000.,renderer_visible=True)])
    return snapshot,receipt,report


@pytest.mark.parametrize('change,match',[
    ('scope','actual published'),('digest','snapshot mismatch'),('scan','scan mismatch'),
    ('duplicate','unique integer'),('coordinate','coordinate/measurement'),('count','count mismatch')])
def test_audit_rejects_unbound_or_forged_native_targets(tmp_path,change,match):
    snapshot,receipt,data=inputs(tmp_path)
    if change=='scope':data['scope']='prototype'
    if change=='digest':data['snapshot_sha256']='different'
    if change=='scan':data['web_frame_identity']['web_scan_id']='different'
    if change=='duplicate':data['target_records']*=2
    if change=='coordinate':data['target_records'][0]['range_m']=100001.
    if change=='count':data['renderer_eligible_visible_gates']=0
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError,match=match):m.audit(snapshot,receipt)


def test_bounded_exterior_band_requires_measured_windows(tmp_path):
    with pytest.raises(ValueError,match='requires measured windows'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',shoulder_band=True)


def test_short_subset_cannot_run_without_measured_window_evidence(tmp_path):
    with pytest.raises(ValueError,match='short subset requires measured windows'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',short_subset=True)


def test_parent_footprint_cannot_run_without_short_original_evidence(tmp_path):
    with pytest.raises(ValueError,match='parent footprint requires short subset evidence'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',parent_footprint=True)


def test_boundary_hypotheses_require_complete_variable_history(tmp_path):
    with pytest.raises(ValueError,match='require complete variable objects'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',boundary_hypotheses=True)


def test_enclosures_cannot_bypass_complete_boundary_mode(tmp_path):
    with pytest.raises(ValueError,match='require full original boundary hypotheses'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',enclosed_branches=True)


def test_boundary_target_selection_does_not_crop_original_history():
    from types import SimpleNamespace
    raw=np.full((5,8),25.);raw[2,5]=5.
    n=SimpleNamespace(fields={'DBZH':raw},ranges=100000.+1000*np.arange(8))
    targets=np.zeros(raw.shape,bool)
    targets[2,1]=True;targets[2,5]=True;targets[4,1]=True
    original=dict(scale_m=10000.,level_dbz=10.,original_start_m=20000.,original_end_m=300000.,
        holds=['original_branch_weather'],history=[{'block':2,'state':'unmatched'}],
        matched_original_runs=[{'block':10,'native_row_start':1,'native_row_end':3}])
    result=m.boundary_target_diagnosis(n,targets,[original])
    assert len(result)==1 and result[0]['target_overlap']==1
    assert result[0]['original_start_m']==20000 and result[0]['original_end_m']==300000
    assert result[0]['holds']==original['holds'] and result[0]['history']==original['history']
    assert 'target_overlap' not in original


def source_inputs():
    from types import SimpleNamespace
    from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import source_ledger,raw_fans
    r=np.arange(1000.,361000.,1000.);shape=(20,len(r))
    source=np.zeros(shape,bool)
    for start in (60000.,100000.,140000.,180000.):
        source[8:15,(r>=start)&(r<start+12000.)]=True
    target=np.zeros(shape,bool);target[8:15,(r>=240000.)&(r<242000.)]=True
    valid=source|target;z=np.where(valid,20.+20.*np.log10(r[None,:]/50000.),np.nan)
    n=SimpleNamespace(shape=shape,ranges=r,azimuth=np.arange(20,dtype=float),
        geometry_good=np.ones(20,bool),gap_after=np.zeros(20,bool),gate_spacing_m=1000.,
        fields={'DBZH':z,'SNR':np.where(valid,13.,np.nan),'RHOHV':np.where(valid,.8,np.nan)},
        field_available={k:valid.copy() for k in ['DBZH','SNR','RHOHV']})
    n.gap_after[-1]=True;blocked=np.zeros(shape,bool)
    ledger,_=source_ledger.freeze(n,blocked,source,np.zeros(shape,bool))
    fans,_=raw_fans.detect(n,blocked,ledger['RV2_SOURCE_LEDGER_SEED_ID'])
    return n,blocked,{**ledger,**fans},target


def test_source_segment_models_use_full_original_sources_and_exclude_target_guard():
    n,blocked,group,target=source_inputs()
    baseline=m.source_segment_diagnosis(n,blocked,group,target)
    assert baseline['target_same_source_agreement']==int(target.sum())
    assert not baseline['action_authority'] and not baseline['source_agreement_is_pollution_truth']
    assert all(not r['target_guard_used_for_training'] for r in baseline['records'])
    n.fields['DBZH'][target]+=15.
    changed=m.source_segment_diagnosis(n,blocked,group,target)
    assert changed['target_same_source_agreement']==0
    assert [r['model']['INTERCEPT_DB'] for r in baseline['records']]==[
        r['model']['INTERCEPT_DB'] for r in changed['records']]


def test_source_agreement_preserves_current_weather_and_target_selection_cannot_train():
    n,blocked,group,target=source_inputs()
    n.fields['RHOHV'][target]=.99
    report=m.source_segment_diagnosis(n,blocked,group,target)
    assert report['target_joint_matches']==int(target.sum())
    assert report['target_same_source_agreement']==0
    assert report['target_current_weather_retained']==int(target.sum())
    one=np.zeros(n.shape,bool);one[8,240]=True
    single=m.source_segment_diagnosis(n,blocked,group,one)['records'][0]
    original=next(r for r in report['records'] if r['row']==8 and r['column']==240)
    assert single==original


@pytest.mark.parametrize('cause',['coordinate','barrier'])
def test_source_segment_diagnostic_rejects_unbound_or_protected_original_sources(cause):
    n,blocked,group,target=source_inputs()
    source=group['RV2_SOURCE_LEDGER_SEED_ID']>0
    if cause=='coordinate':group['RV2_SOURCE_LEDGER_RANGE_M'][source]+=100.
    else:blocked[source]=True
    with pytest.raises(ValueError,match='original source'):
        m.source_segment_diagnosis(n,blocked,group,target)


def test_source_report_preserves_complete_history_but_counts_only_safe_training_section():
    n,blocked,group,target=source_inputs()
    blocked[:,89]=True  # Original source IDs are frozen before this later barrier.
    report=m.source_segment_diagnosis(n,blocked,group,target)
    assert report['target_same_source_agreement']==int(target.sum())
    assert all(r['original_source_gates']==48 and r['guard_excluded_reference_gates']==36
               and r['original_source_start_m']==60000. for r in report['records'])


def test_variable_audit_detects_full_raw_before_published_target_selection(tmp_path,monkeypatch):
    snapshot=tmp_path/'raw.npz';receipt=tmp_path/'published.json'
    raw=np.full((5,12),np.nan);raw[2,2:10]=25.
    az=np.arange(5,dtype=float);ranges=100000.+250*np.arange(12)
    np.savez(snapshot,METADATA=np.array(json.dumps({'scan_id':'original'})),RAW=raw,
        AZIMUTH=az,RANGE=ranges,GEOMETRY_GOOD=np.ones(5,bool),GAP_AFTER=np.zeros(5,bool),
        AVAILABLE_DBZH=np.isfinite(raw),WEATHER=np.zeros(raw.shape,'uint8'),
        CONFLICTS=np.zeros(raw.shape,'uint8'),RV2_BARRED_MASK=np.zeros(raw.shape,'uint8'))
    receipt.write_text(json.dumps(dict(scope='exact_Web_consumed_stored_QC_not_replay',
        snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        web_frame_identity={'web_scan_id':'original'},target_gates=1,renderer_eligible_visible_gates=1,
        target_records=[dict(row=2,column=5,raw_dbzh=25.,azimuth_deg=2.,
            range_m=float(ranges[5]),renderer_visible=True)])))
    calls=[]
    def detector(module):
        def detect(native,blocked,**options):
            # A single published residual must not truncate its eight-gate RAW parent.
            assert np.array_equal(native.fields['DBZH'],raw,equal_nan=True)
            calls.append(module.PREFIX)
            domain=np.isfinite(raw)
            return {module.PREFIX+'MASK':domain.astype('uint8'),
                module.PREFIX+'ID':domain.astype('uint32'),
                module.PREFIX+'STRONG_MASK':np.zeros(raw.shape,'uint8'),
                module.PREFIX+'WEATHER_VETO_MASK':np.zeros(raw.shape,'uint8')},\
                {'objects':[{'id':1,'member_gates':8,'holds':['unknown_shoulders']}]}
        return detect
    for module in (m.fragment_constellation,m.morphology_objects,m.variable_morphology):
        monkeypatch.setattr(module,'detect',detector(module))
    baseline=m.audit(snapshot,receipt)
    assert 'variable_object' not in baseline['detectors']
    assert m.variable_morphology.PREFIX not in calls
    result=m.audit(snapshot,receipt,variable_objects=True)
    obj=result['detectors']['variable_object']['objects'][0]
    assert obj['member_gates']==8 and obj['target_overlap']==1
    assert obj['holds']==['unknown_shoulders']
    assert result['action_authority'] is False and result['product_writes'] is False


@pytest.mark.parametrize('extra',[[],['--fragment-constellation','--engine-quarantine']])
def test_geometry_preview_cannot_route_to_engine_actions(tmp_path,extra):
    import subprocess
    result=subprocess.run([sys.executable,str(ROOT/'scripts/audit_s_morphology_objects.py'),
        str(tmp_path/'unused.npz'),'--output',str(tmp_path/'unused-output'),
        '--geometry-evidence',*extra],capture_output=True,text=True)
    assert result.returncode!=0
    assert 'geometry evidence requires research-only constellation; no engine actions' in result.stderr
    assert not (tmp_path/'unused-output').exists()


def test_original_parent_diagnosis_keeps_full_raw_and_native_gap_identity():
    from types import SimpleNamespace
    shape=(6,12);raw=np.full(shape,np.nan)
    raw[1,3:7]=20.;raw[4,3:7]=20.
    native=SimpleNamespace(shape=shape,fields={'DBZH':raw},field_available={'DBZH':np.isfinite(raw)},
        ranges=100000.+np.arange(12)*250,azimuth=(359.+np.arange(6))%360,
        geometry_good=np.ones(6,bool),gap_after=np.array([0,0,1,0,0,0],bool))
    targets=np.zeros(shape,bool);targets[1,4]=True;targets[4,4]=True
    proposal=np.zeros(shape,bool);proposal[1,4]=True
    evidence={'objects':[{'native_segment_start':0,'original_distance_partitions':[
        {'original_lower_parent_ids':[1],'measured_subset':{'qualified':True}}]}]}
    results=m.original_parent_target_diagnosis(native,targets,proposal,evidence)
    assert [(p['native_segment_start'],p['original_lower_parent_id']) for p in results]==[(0,1),(3,1)]
    assert [p['original_gate_count'] for p in results]==[4,4]
    assert [p['originally_authorized_parent'] for p in results]==[True,False]
    assert [p['targets'][0]['research_nominated'] for p in results]==[True,False]
    assert all(p['original_radial_span_m']==1000 for p in results)
    # Selecting one point never shrinks the complete original four-gate object.
    targets[4,4]=False
    narrowed=m.original_parent_target_diagnosis(native,targets,proposal,evidence)
    assert len(narrowed)==1 and narrowed[0]['original_gate_count']==4
