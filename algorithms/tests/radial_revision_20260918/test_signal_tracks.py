import numpy as np
from .conftest import Native,load


def fixture():
    z=np.full((13,320),np.nan,'float32');z[5:8]=25.
    n=Native(z,start=50000.)
    n.fields['SNR']=np.full(n.shape,1.,'float32');n.fields['SNR'][5:8]=8.
    n.field_available['SNR']=np.ones(n.shape,bool)
    return n,np.zeros(n.shape,bool)


def run(n,b):return load('radial_revision.signal_tracks').diagnose(n,b)


def test_multiscale_measured_track_has_guarded_models_without_any_action():
    n,b=fixture();raw=n.fields['DBZH'].copy();availability=n.field_available['DBZH'].copy()
    out,report=run(n,b)
    assert out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'][6,100:120].all()
    assert not out['RV2_SIGNAL_TRACK_NOMINATED_MASK'][4].any()
    assert report['actions']==0 and not report['source_claim'] and not report['recursive_growth']
    assert np.array_equal(raw,n.fields['DBZH'],equal_nan=True)
    assert np.array_equal(availability,n.field_available['DBZH'])


def test_missing_flanks_or_measured_contamination_do_not_count_as_quiet():
    n,b=fixture();n.field_available['SNR'][4]=False
    out,_=run(n,b);assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'].any()
    n,b=fixture();n.fields['SNR'][4,100:180]=4.
    out,_=run(n,b);assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'][6,100:180].any()


def test_target_cannot_train_reference_or_bridge_weather_and_native_gap():
    n,b=fixture();n.fields['SNR'][5:8,100:120]=20.
    out,_=run(n,b);assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'][6,100:120].any()
    n,b=fixture();b[:,99:101]=True;b[:,120:122]=True
    out,_=run(n,b);assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'][6,102:120].any()
    n,b=fixture();n.gap_after[5]=True
    out,_=run(n,b);assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'].any()


def test_short_objects_and_current_reliable_weather_polar_are_retained():
    n,b=fixture();n.field_available['DBZH'][:,:100]=False;n.field_available['DBZH'][:,140:]=False
    out,_=run(n,b);assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'].any()
    n,b=fixture();n.fields['SNR'][5:8]=11.;n.fields['RHOHV']=np.full(n.shape,.99,'float32')
    n.field_available['RHOHV']=np.ones(n.shape,bool)
    out,report=run(n,b);assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'].any()
    assert report['weather_like_retained_gates']>0


def test_small_local_flank_contamination_is_excluded_from_reference_not_target_permission():
    n,b=fixture();n.fields['SNR'][4,30:50]=4.
    out,_=run(n,b)
    assert out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'][6,100:120].all()
    assert not out['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK'][6,30:50].any()
    clean,barrier=fixture();baseline,_=run(clean,barrier)
    assert out['RV2_SIGNAL_TRACK_REFERENCE_BLOCKS'][6,110]==baseline['RV2_SIGNAL_TRACK_REFERENCE_BLOCKS'][6,110]-1
