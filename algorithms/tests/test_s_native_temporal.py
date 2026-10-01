"""Independent native time/availability fixtures for the read-only recurrence audit."""
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('s_native_temporal_audit_test', SCRIPTS/'audit_s_native_temporal.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sys.path.pop(0)


def scene(*, age=0., elevation=.5, angles=None, ranges=None):
    angles = np.array([350.,355.,0.,5.]) if angles is None else np.asarray(angles)
    ranges = np.arange(1000.,7000.,1000.) if ranges is None else np.asarray(ranges)
    shape = (len(angles),len(ranges))
    a = dict(RAW=np.full(shape,20.),AVAILABLE_DBZH=np.ones(shape,bool),
        MOMENT_SNR=np.full(shape,12.),AVAILABLE_SNR=np.ones(shape,bool),
        MOMENT_RHOHV=np.full(shape,.99),AVAILABLE_RHOHV=np.ones(shape,bool),
        AZIMUTH=angles,RANGE=ranges,ELEVATION=np.full(len(angles),elevation),
        RAY_TIME=np.datetime64('2026-08-28T00:30:00','ns') +
            (np.arange(len(angles))*1000000000-int(age*1000000000)).astype('timedelta64[ns]'),
        GEOMETRY_GOOD=np.ones(len(angles),bool),GAP_AFTER=np.zeros(len(angles),bool),
        BEFORE=np.ones(shape,bool),ADDED=np.zeros(shape,bool),
        WEATHER=np.zeros(shape,'uint8'),CONFLICTS=np.zeros(shape,'uint8'),
        RV2_BARRED_MASK=np.zeros(shape,'uint8'),RV2_RAW_FAN_ID=np.ones(shape,'uint32'))
    return a


def save(tmp_path,name,arrays,**metadata):
    path=tmp_path/(name+'.npz')
    meta=dict(radar_id='site-a',sweep=0,scan_id=name,raw_artifact_sha256=name)
    meta.update(metadata)
    np.savez_compressed(path,**arrays,METADATA=np.array(json.dumps(meta)))
    return path


def test_measured_recurrence_does_not_delete_weather_or_claim_sources(tmp_path):
    target,past=scene(),scene(age=360.)
    paths=[save(tmp_path,'target',target),save(tmp_path,'past',past)]
    report=audit.audit(paths[0],paths[1:],azimuth=[345.,10.])
    assert report['remaining_gates']==24
    assert report['references'][0]['remaining_counts']['stable_snr']==24
    assert report['parents'][0]['weather_like_gates']==24
    assert report['actions']==0 and report['product_writes'] is False and report['source_claim'] is False
    with np.load(paths[0]) as stored:
        assert np.array_equal(stored['RAW'],target['RAW'])


def test_missing_echo_is_unknown_even_with_measured_noise(tmp_path):
    past=scene(age=360.);past['RAW'][:]=np.nan;past['AVAILABLE_DBZH'][:]=False
    past['MOMENT_SNR'][:]=0.
    report=audit.audit(save(tmp_path,'target',scene()),[save(tmp_path,'past',past)])
    counts=report['references'][0]['remaining_counts']
    assert counts['native_coverage']==24 and counts['observed_snr']==24
    assert counts['observed_echo']==counts['stable_snr']==0
    assert report['references'][0]['typed_radial_definition_available'] is False


def test_native_alignment_limits_gaps_range_elevation_and_age():
    target=scene();past=scene(age=360.,ranges=[2000.,3000.,4000.,5000.])
    _,covered,_=audit.mapping(target,past)
    assert covered.sum()==16 and not covered[:,[0,-1]].any()
    past['GAP_AFTER'][1]=True
    _,covered,_=audit.mapping(target,past)
    assert covered.sum()==8 and not covered[1:3].any()
    past['ELEVATION'][:]=1.
    assert not audit.mapping(target,past)[1].any()
    assert not audit.mapping(target,scene(age=1801.))[1].any()
    assert not audit.mapping(target,scene(age=360.,angles=[352.6,357.6,2.6,7.6]))[1][0].any()


@pytest.mark.parametrize('mode',['future','same_volume','wrong_cut','wrong_radar'])
def test_references_cannot_supply_false_independent_votes(tmp_path,mode):
    target=save(tmp_path,'target',scene())
    kwargs={};past=scene(age=-360. if mode=='future' else 360.)
    if mode=='same_volume':kwargs['raw_artifact_sha256']='target'
    if mode=='wrong_cut':kwargs['sweep']=2
    if mode=='wrong_radar':kwargs['radar_id']='site-b'
    with pytest.raises(ValueError):audit.audit(target,[save(tmp_path,'past',past,**kwargs)])


def test_typed_radial_votes_require_actual_named_flags_and_preserve_barriers(tmp_path):
    target=scene();target['WEATHER'][0,:]=1
    past=scene(age=360.);past['STORED_QC_FLAGS']=np.full(past['RAW'].shape,8,'uint32')
    report=audit.audit(save(tmp_path,'target',target),[save(tmp_path,'past',past,
        stored_qc_flag_definitions={'RADIAL_INTERFERENCE':8})])
    assert report['references'][0]['remaining_counts']['typed_radial']==18
    assert report['actions']==0


def test_duplicate_reference_raw_sha_is_not_another_vote(tmp_path):
    target=save(tmp_path,'target',scene())
    p1=save(tmp_path,'past1',scene(age=360.))
    p2=save(tmp_path,'past2',scene(age=720.),raw_artifact_sha256='past1')
    with pytest.raises(ValueError,match='independent'):audit.audit(target,[p1,p2])
