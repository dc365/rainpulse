from dataclasses import replace
import numpy as np
import pytest
from volume_review.config import VolumeReviewConfig as C
from volume_review.data import Sweep, array_digest
from volume_review.objects import capability, extract_objects
from volume_review.geometry import label_native, xyz
from volume_review.source import fit_sources
from volume_review.engine import evaluate
from volume_review.disposition import dispose, validate_fields
from helpers import sweep, change, baseline


@pytest.mark.parametrize("bad",[{"phase":1,"mode":"experiment_quarantine"},{"levels_dbz":(10,0)},
                                 {"scales_m":(5000,5000)},{"levels_dbz":(float('nan'),)},
                                 {"maximum_source_residual_db":10},{"operational_eligible":True},
                                 {"weak_policy":"delete"},{"source_corroboration_required":False}])
def test_bad_config(bad):
    with pytest.raises(ValueError): C(**bad)


def test_immutable_copied_inputs():
    s=sweep(); z=s.fields['DBZH']
    with pytest.raises(ValueError): z[0,0]=100
    with pytest.raises(TypeError): s.fields['NEW']=z
    before=s.digest; evaluate([s],C())
    assert s.digest==before


def test_no_echo_cannot_overlap():
    s=sweep()
    with pytest.raises(ValueError): replace(s,no_echo=s.observed)


def test_missing_does_not_become_no_echo():
    s=sweep(with_polar=False)
    assert not s.no_echo.any()
    ev=evaluate([s],C())
    assert not ev.arrays[0]['VOR_PROPOSAL_MASK'].any()
    assert not ev.arrays[0]['VOR_CANDIDATE_MASK'][~s.observed].any()


def test_invalid_nonfinite_available():
    s=sweep()
    with pytest.raises(ValueError): replace(s,available={**s.available,'DBZH':np.ones(s.shape,bool)})


@pytest.mark.parametrize("scale",[5000,10000,20000,40000])
def test_high_layer_capability(scale):
    s=sweep(el=19.5)
    assert scale in capability(s,C())['available_scales_m']


def test_all_missing_status():
    s=change(sweep(),'DBZH',lambda x:x.fill(np.nan))
    a,o,c=extract_objects(s,C())
    assert c['status']=='NOT_APPLICABLE_NO_DBZH' and not o
    assert not a['VOR_CANDIDATE_MASK'].any()


def test_p0_has_no_candidates():
    e=evaluate([sweep()],C(phase=0))
    assert not e.arrays[0]['VOR_CANDIDATE_MASK'].any()


def test_all_foreground_labels_no_background_bug():
    s=sweep()
    labels=label_native(np.ones(s.shape,bool),s)
    assert labels.min()==1 and labels.max()==1


def test_gap_breaks_components():
    s=sweep(); gaps=s.gap_after.copy(); gaps[8]=True
    s=replace(s,gap_after=gaps)
    x=np.zeros(s.shape,bool);x[8:10,20:25]=True
    ids=label_native(x,s)
    assert ids[8,20]!=ids[9,20]


def test_north_seam_connected_only_when_declared():
    s=sweep();nr=360;ng=s.shape[1];z=np.full((nr,ng),np.nan,'float32');z[[0,-1],20:30]=30
    s=Sweep('sweep_000',np.arange(nr),np.ones(nr),s.ranges,{'DBZH':z},{'DBZH':np.isfinite(z)},np.ones(nr,bool),np.zeros(nr,bool))
    labels=label_native(s.observed,s); assert labels[0,20]==labels[-1,20]>0
    gaps=s.gap_after.copy();gaps[-1]=True
    labels=label_native(s.observed,replace(s,gap_after=gaps));assert labels[0,20]!=labels[-1,20]


def test_multilevel_not_multiple_independent_votes():
    a,o,c=extract_objects(sweep(),C())
    assert len(o)>1
    assert {x['evidence_group'] for x in o}=={'same_raw_DBZH'}
    assert any(x['parent_id']>0 for x in o)


def test_source_target_guard_exclusion():
    s=sweep();cfg=C(); cand=s.observed.copy()
    a,records,status=fit_sources(s,cfg,cand)
    assert a['VOR_SOURCE_MATCH_MASK'].any() and records
    for rec in records:
        assert all(abs(b-rec['target_block'])>cfg.guard_blocks for b in rec['reference_blocks'])
    target=3; cols=(s.ranges//cfg.source_block_m).astype(int)
    s2=change(s,'DBZH',lambda x:x.__setitem__((slice(None),abs(cols-target)<=1),x[:,abs(cols-target)<=1]+10))
    _,records2,_=fit_sources(s2,cfg,cand)
    first={(x['ray'],x['target_block'],x['family']):x['reference_digest'] for x in records if x['target_block']==target}
    second={(x['ray'],x['target_block'],x['family']):x['reference_digest'] for x in records2 if x['target_block']==target}
    assert first and first==second


def test_shapes_alone_never_delete():
    s=sweep(with_polar=False)
    e=evaluate([s,replace(s,name='sweep_001')],C(mode='experiment_quarantine'))
    assert e.summary['candidate_gates']>0 and e.summary['proposed_gates']==0


def test_single_layer_cannot_self_corroborate():
    e=evaluate([sweep()],C(mode='experiment_quarantine'))
    assert e.arrays[0]['VOR_SOURCE_MATCH_MASK'].any()
    assert not e.arrays[0]['VOR_SOURCE_CORROBORATED_MASK'].any()
    assert e.summary['proposed_gates']==0


def test_two_source_layers_propose():
    e=evaluate([sweep(),sweep('sweep_001',el=1.5)],C(mode='experiment_quarantine'))
    assert e.summary['proposed_gates']>0 and e.links
    for a in e.arrays:
        assert np.all(a['VOR_SOURCE_MATCH_MASK'][a['VOR_PROPOSAL_MASK']==1]==1)


def test_old_graph_result_cannot_retrain_sources():
    s=[sweep(),sweep('sweep_001',el=1.5)]
    x=evaluate(s,C());y=evaluate(s,C(mode='experiment_quarantine'))
    assert x.models==y.models


def test_unavailable_timing_abstains():
    s=sweep();other=replace(sweep('sweep_001'),ray_time_s=None)
    e=evaluate([s,other],C(mode='experiment_quarantine'))
    assert e.summary['proposed_gates']==0


def test_time_mismatch_no_donor():
    s=sweep();other=replace(sweep('sweep_001'),ray_time_s=np.full(s.shape[0],10000.))
    e=evaluate([s,other],C(mode='experiment_quarantine'));assert e.summary['proposed_gates']==0


def test_real_azimuth_matching_not_same_array_row():
    one=sweep();two=sweep('sweep_001',offset=80)
    e=evaluate([one,two],C(mode='experiment_quarantine'));assert e.summary['proposed_gates']==0


def test_explicit_weather_overrides_source_proposal():
    s=[sweep(),sweep('sweep_001',el=1.5)]
    e=evaluate(s,C(mode='experiment_quarantine'),protected=[x.observed for x in s])
    assert e.summary['proposed_gates']==0
    assert any((x['VOR_STATE']==5).any() for x in e.arrays)


def test_high_rho_weather_not_deleted():
    s=[change(sweep(),'RHOHV',lambda a:a.__setitem__(np.isfinite(a),.99)),
       change(sweep('sweep_001',el=2),'RHOHV',lambda a:a.__setitem__(np.isfinite(a),.99))]
    e=evaluate(s,C(mode='experiment_quarantine'));assert e.summary['proposed_gates']==0


def test_missing_high_layer_does_not_veto_shallow_weather():
    s=change(sweep(),'RHOHV',lambda a:a.__setitem__(np.isfinite(a),.99))
    other=change(sweep('sweep_001',el=20),'DBZH',lambda a:a.fill(np.nan))
    e=evaluate([s,other],C(mode='experiment_quarantine'));assert not e.arrays[0]['VOR_PROPOSAL_MASK'].any()


@pytest.mark.parametrize('phase',[0,1,2,3])
def test_audit_does_not_change_existing_arrays(phase):
    s=sweep();cfg=C(phase=phase);e=evaluate([s],cfg)
    a,f,q=baseline(s);before=array_digest(a)
    out,flags,quality,_=dispose(a,f,q,s.observed,e.arrays[0],cfg,low_quality_flag=1024)
    assert before==array_digest(a)
    for k in a:
        assert np.array_equal(out[k],a[k],equal_nan=True)
    assert np.array_equal(quality,q,equal_nan=True)
    assert np.array_equal(flags,f)
    validate_fields(out,s.observed)


def test_experiment_preserves_raw_and_removes_all_trust():
    s=sweep();cfg=C(mode='experiment_quarantine');e=evaluate([s,sweep('sweep_001',el=1.5)],cfg)
    a,f,q=baseline(s);raw=s.digest
    out,flags,quality,diag=dispose(a,f,q,s.observed,e.arrays[0],cfg,low_quality_flag=1024)
    mask=out['VOR_QUARANTINE_MASK']==1
    assert mask.any() and diag['review_required']
    assert not out['QPE_ELIGIBLE_MASK'][mask].any()
    assert np.isnan(out['DBZH_USABLE'][mask]).all()
    assert s.digest==raw and quality[mask].max()<=cfg.quarantine_quality
    for k in a:
        if k.endswith('_TRUST_MASK'):assert not out[k][mask].any()
    validate_fields(out,s.observed)


def test_cr_independent_from_qpe_without_revival():
    s=sweep();cfg=C(phase=0);e=evaluate([s],cfg)
    a,f,q=baseline(s);a['QPE_ELIGIBLE_MASK'][:]=0;a['DBZH_USABLE'][:]=np.nan
    out,_,_,_=dispose(a,f,q,s.observed,e.arrays[0],cfg,low_quality_flag=1024)
    assert out['REFLECTIVITY_ELIGIBLE_FOR_CR'].sum()==s.observed.sum()
    assert not out['QPE_ELIGIBLE_MASK'].any()


def test_resource_abstains_whole_volume():
    e=evaluate([sweep(),sweep('sweep_001')],C(maximum_objects=1,mode='experiment_quarantine'))
    assert e.summary['status']=='RESOURCE_ABSTAINED' and len(e.arrays)==2
    assert all(not x['VOR_PROPOSAL_MASK'].any() for x in e.arrays)


def test_geometry_height_is_relative_and_increasing():
    s=sweep(); p=xyz(s,np.array([8,8]),np.array([1,100]))
    assert p[1,2]>p[0,2]>0


def test_same_elevation_duplicate_does_not_corroborate():
    e=evaluate([sweep(),sweep("sweep_001")],C(mode="experiment_quarantine"))
    assert e.summary["proposed_gates"]==0
