import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.near_background import candidate


def scene():
    shape=(360,40)
    return [np.zeros(shape),np.full(shape,.75),np.full(shape,15.),np.ones(shape,bool),np.ones(shape,bool),np.zeros(shape,bool),np.arange(360.),np.arange(40)*250.]


def test_coherent_background_nonmet_needs_no_texture_or_zdr():
    a=scene();m,_,_=candidate(*a);assert m[:,10:30].all()


def test_rain_missing_low_snr_and_protection_preserved():
    for index,value in [(1,.98),(1,np.nan),(2,3),(3,False),(4,False),(5,True),(0,35)]:
        a=scene();a[index][:]=value;assert not candidate(*a)[0].any()


def test_speckled_polarization_not_enough():
    a=scene();a[1][:]=.98;a[1][::3,::3]=.75
    assert not candidate(*a)[0].any()


def test_missing_not_evidence_and_range_edge_abstains():
    a=scene();a[1][:,:20]=np.nan;m,_,_=candidate(*a)
    assert not m[:,:20].any();assert not m[:,0].any();assert not m[:,-1].any()
