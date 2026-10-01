import numpy as np
import pytest
from .conftest import Native,load,evaluate

P='RV2_SOURCE_WINDOW_'


def case(dr=1000.):
    ranges=np.arange(0.,360000.,dr)
    z=np.full((15,len(ranges)),np.nan,'float32')
    seeds=np.zeros(z.shape,bool);tail=seeds.copy()
    for a,b in ((50000.,60000.),(100000.,110000.),(150000.,160000.)):
        seeds[7,(ranges>=a)&(ranges<b)]=True
    tail[7,(ranges>=200000.)&(ranges<205000.)]=True
    tail[8,(ranges>=245000.)&(ranges<250000.)]=True
    z[seeds]=30.;z[tail]=5.
    return Native(z,dr=dr,start=0.),seeds,tail


def detect(n,seeds,blocked=None):
    barrier=np.zeros(n.shape,bool) if blocked is None else blocked
    ledger=load('radial_revision.source_ledger').freeze(n,barrier,seeds,np.zeros(n.shape,bool))[0]
    out,report=load('radial_revision.source_window').detect(n,barrier,ledger)
    merged={**ledger,**out}
    load('radial_revision.source_window').validate(merged,n.field_available['DBZH'],barrier)
    return merged,report


@pytest.mark.parametrize('dr',[250.,500.,1000.])
def test_window_source_handles_discrete_weak_tails_without_recursive_growth(dr):
    n,seeds,tail=case(dr)
    raw=n.fields['DBZH'].copy();out,report=detect(n,seeds)
    assert out[P+'QUALIFIED_MASK'][tail].all()
    assert not out[P+'SEED_ID'][tail].any()
    assert np.isin(out[P+'PARENT_ID'][tail],out[P+'SEED_ID'][seeds]).all()
    assert not report['source_claim'] and not report['recursive_growth']
    assert np.array_equal(raw,n.fields['DBZH'],equal_nan=True)
    n.fields['DBZH'][9,(n.ranges>=270000.)&(n.ranges<275000.)]=5.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    again,_=detect(n,seeds)
    assert not again[P+'QUALIFIED_MASK'][9].any() # No angular relay from row8.
    assert not again[P+'QUALIFIED_MASK'][:,n.ranges>=280000.].any()


def test_variable_width_and_small_local_flank_contamination():
    n,seeds,tail=case()
    n.fields['DBZH'][6:9,100:110]=30.
    n.fields['DBZH'][6:9,150:160]=30.
    # One measured polluting flank gate must be held locally, while its clean
    # neighbours can use pooled evidence rather than losing the entire track.
    n.fields['DBZH'][6,200]=5.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,_=detect(n,seeds)
    assert out[P+'QUALIFIED_MASK'][7,201:205].all()
    assert not out[P+'QUALIFIED_MASK'][7,200]
    assert out[P+'RIGHT_DEG'][7,202]-out[P+'LEFT_DEG'][7,202]<=8.


def test_unknown_flanks_are_recorded_without_a_clear_air_vote():
    n,seeds,tail=case();out,_=detect(n,seeds)
    assert (out[P+'WINDOW20_LEFT_AVAILABLE_FRACTION'][tail]==0).all()
    assert (out[P+'WINDOW20_RIGHT_AVAILABLE_FRACTION'][tail]==0).all()
    assert out[P+'QUALIFIED_MASK'][tail].all() # Existing independent parent only.
    unseeded,_=detect(n,np.zeros(n.shape,bool))
    assert not unseeded[P+'QUALIFIED_MASK'].any()


def test_missing_target_empty_weather_gap_and_geometry_discontinuity_are_not_crossed():
    n,seeds,tail=case();barrier=np.zeros(n.shape,bool);barrier[:,180:182]=True
    out,_=detect(n,seeds,barrier)
    assert not out[P+'QUALIFIED_MASK'][tail].any()
    n.gap_after[6]=True
    out,_=detect(n,seeds)
    assert not out[P+'QUALIFIED_MASK'].any()
    n,seeds,tail=case();n.field_available['DBZH'][tail]=False
    out,_=detect(n,seeds)
    assert not out[P+'CANDIDATE_MASK'][tail].any()


def test_insufficient_source_and_broad_weather_band_cannot_acquire_qualification():
    n,seeds,tail=case();seeds[:,100:]=False
    out,_=detect(n,seeds)
    assert not out[P+'QUALIFIED_MASK'].any()
    n,seeds,tail=case();n.fields['DBZH'][2:13,20:270]=25.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,_=detect(n,seeds)
    assert not out[P+'QUALIFIED_MASK'].any()


def test_multiple_original_parents_permanently_abstain():
    n,seeds,tail=case();n.fields['DBZH'][:]=np.nan;seeds[:]=False;tail[:]=False
    for row in (6,7,8):
        for a,b in ((50,60),(100,110),(150,160)):
            seeds[row,a:b]=True;n.fields['DBZH'][row,a:b]=30.
    n.fields['DBZH'][7,200:205]=5.;tail[7,200:205]=True
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,_=detect(n,seeds)
    assert not out[P+'QUALIFIED_MASK'][tail].any()
    assert (out[P+'HOLD_REASON'][tail]==2).all()


def test_serialization_rejects_forged_parent_distance_window_and_source_identity():
    n,seeds,tail=case();out,_=detect(n,seeds)
    for key,value,where in (('PARENT_ID',9999,tail),('ANCHOR_DISTANCE_M',1.,tail),
                            ('WINDOW20_INTERIOR_FRACTION',.1,tail),('SEED_ID',9999,seeds)):
        bad={k:v.copy() for k,v in out.items()};bad[P+key][where]=value
        with pytest.raises(ValueError):load('radial_revision.source_window').validate(bad,n.field_available['DBZH'],np.zeros(n.shape,bool))


def test_engine_window_tracks_persist_with_full_identity_and_audit_has_no_action():
    n,seeds,tail=case()
    for key,value in (('SNR',25.),('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[key]=np.where(seeds,value,np.nan).astype('float32');n.field_available[key]=seeds.copy()
    cfg=load('radial_revision.config').RadialRevisionConfig(step=3,mode='experiment_quarantine',fragment_line={
        'raw_fragment_families_enabled':True,'source_ledger_enabled':True,'source_window_tracks_enabled':True})
    out,_=evaluate(n,cfg,seeds)
    assert out['RV2_ACTION_PROPOSAL_MASK'][tail].all()
    assert out['RV2_OBJECT_ID'][tail].all()
    validate=load('radial_revision.validation').validate_revision_fields
    validate(out,n.field_available['DBZH'],seeds,np.zeros(n.shape,bool))
    audit,_=evaluate(n,cfg.model_copy(update={'mode':'audit'}),seeds)
    assert audit[P+'QUALIFIED_MASK'][tail].all()
    assert not audit['RV2_ACTION_PROPOSAL_MASK'].any()
    baseline,_=evaluate(n,cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(update={'source_window_tracks_enabled':False})}),seeds)
    for key in ('RV2_MODEL_ID','RV2_SEGMENT_FOLD_ID','RV2_SEGMENT_RESIDUAL_DB','RV2_FIT_AVAILABLE_MASK'):
        assert np.array_equal(out[key],baseline[key],equal_nan=True)
    config=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision=cfg.model_dump(mode='json'))
    _,arrays,_=load('source').source_additions(n,config,seeds,np.zeros(n.shape,'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=seeds.astype('uint32')
    load('source_validation').validate_source_fields(arrays,n.field_available['DBZH'])
    assert arrays[P+'QUALIFIED_MASK'][tail].all()
    with pytest.raises(ValueError):load('radial_revision.config').FragmentLineConfig(source_window_tracks_enabled=True)
