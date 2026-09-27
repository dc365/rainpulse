import numpy as np
from rainpulse_algo.multiband.experimental import experimental_fields, update_samples
from rainpulse_algo.multiband.quality import Flag


def test_unknown_calibration_retained_but_hard_bad_echoes_excluded():
    f={'DBZH_QC_DISPLAY':np.array([[20.,30.,40.,50.]]),'OBSERVED_MASK':np.ones((1,4)),
       'NO_ECHO_MASK':np.zeros((1,4)), 'MB_QC_FLAGS':np.array([[int(Flag.CALIBRATION_UNKNOWN),int(Flag.NONMET_CONFIRMED),int(Flag.LOW_SNR),int(Flag.BLOCKED)]])}
    values, admitted, _ = experimental_fields(f, 'X')
    assert admitted.tolist()==[[True,False,False,False]]
    assert values[0,0]==20


def test_horizontal_max_keeps_missing_clear_and_winner_distinct():
    out={'CR_DBZH':np.full(3,np.nan),'OBSERVED_MASK':np.zeros(3,np.uint8),'WINNER_SOURCE':np.full(3,-1),'WINNER_RAY':np.full(3,-1),'WINNER_GATE':np.full(3,-1),'WINNER_AGE_SECONDS':np.full(3,np.nan),'WINNER_QUALITY_SCORE':np.full(3,np.nan)}
    update_samples(out,np.array([20.,np.nan,np.nan]),np.array([1,1,0],bool),np.zeros(3,int),np.arange(3),np.zeros(3),np.ones(3),0)
    update_samples(out,np.array([30.,np.nan,np.nan]),np.array([1,0,0],bool),np.zeros(3,int),np.arange(3),np.zeros(3),np.ones(3),1)
    assert out['CR_DBZH'][0]==30 and out['WINNER_SOURCE'][0]==1
    assert out['OBSERVED_MASK'].tolist()==[1,1,0]
    assert np.isnan(out['CR_DBZH'][1:]).all()


def test_experiment_does_not_enable_verified_fusion():
    import json
    from rainpulse_algo.multiband.model import Network
    n=Network.from_bytes(json.dumps({'schema_version':'1.0','release_id':'test','stations':{'x1':{'band':'X','source':'normalized_zarr','experimental_enabled':True}},'products':{}}).encode())
    assert n.stations['x1'].experimental_enabled
    assert not n.stations['x1'].enabled
    assert not n.stations['x1'].geometry_verified
    assert not n.stations['x1'].calibration_verified


def test_s_hard_rejection_still_applies():
    f={'DBZH_QC':np.array([[20.,30.]]),'OBSERVED_MASK':np.ones((1,2)), 'NO_ECHO_MASK':np.zeros((1,2)),
       'REFLECTIVITY_ELIGIBLE_FOR_CR':np.ones((1,2)), 'QUALITY_INDEX':np.ones((1,2)), 'CR_WITHHELD_MASK':np.array([[0,1]])}
    assert experimental_fields(f,'S')[1].tolist()==[[True,False]]


def test_duplicate_bearings_use_latest_ray_with_original_index():
    from rainpulse_algo.multiband.experimental import unique_rays
    from rainpulse_algo.multiband.model import Sweep
    sweep=Sweep(0,np.array([0.,1.,0.]),np.array([100.,200.]),np.ones(3),np.array([1.,2.,3.]),{'DBZH':np.array([[10.,10.],[20.,20.],[30.,30.]])})
    selected, original=unique_rays(sweep)
    assert original.tolist()==[1,2]
    assert selected.fields['DBZH'][:,0].tolist()==[20.,30.]


def test_missing_snr_experiment_is_distinct_from_measured_low_snr():
    f={'DBZH_QC_DISPLAY':np.array([[20.]]),'OBSERVED_MASK':np.ones((1,1)),
       'NO_ECHO_MASK':np.zeros((1,1)), 'MB_QC_FLAGS':np.array([[int(Flag.LOW_SNR|Flag.CALIBRATION_UNKNOWN)]])}
    assert experimental_fields(f,'X',allow_missing_snr=True)[1].item()
    f['SNRH']=np.array([[0.]])
    assert not experimental_fields(f,'X',allow_missing_snr=True)[1].item()


def test_float_epoch_rounding_respects_catalog_microsecond_precision():
    import pytest
    from rainpulse_algo.multiband.model import Station,Sweep,Volume,epoch
    station=Station('s1','S','s_qc_zarr')
    start='2026-08-28T00:14:59.580104Z'; end='2026-08-28T00:20:09.988932Z'
    times=np.array([np.nextafter(epoch(start),-np.inf),np.nextafter(epoch(end),np.inf)])
    sweep=Sweep(0,np.array([0.,1.]),np.array([100.,200.]),np.ones(2),times,{'DBZH':np.ones((2,2)),'OBSERVED_MASK':np.ones((2,2)),'NO_ECHO_MASK':np.zeros((2,2))})
    v=Volume({'radar_id':'s1','scan_id':'s','band':'S','volume_start':start,'volume_end':end,'available_at':end,'asset_sha256':'a'*64,'scan_type':'volume'},[sweep])
    v.validate(station,require_geometry=False)
    sweep.ray_time_epoch[1]=epoch(end)+.00001
    with pytest.raises(ValueError,match='acquisition interval'):
        v.validate(station,require_geometry=False)


def test_requested_coverage_preserves_absent_stations_and_old_requests():
    from rainpulse_algo.multiband.experimental import missing_sources
    payload = {'sources': [{'radar_id': 's1'}], 'requested_radars': ['s1', 'x1']}
    assert missing_sources(payload) == [{'radar_id': 'x1', 'reason': 'no_usable_causal_input'}]
    assert missing_sources({'sources': payload['sources']}) == []
