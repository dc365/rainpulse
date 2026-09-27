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
