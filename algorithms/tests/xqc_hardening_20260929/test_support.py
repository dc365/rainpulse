import copy
from dataclasses import replace
import numpy as np
import pytest
from rainpulse_algo.multiband.moment_support import moment_support, binary_mask
from rainpulse_algo.multiband.xqc_v2.pipeline import prepare_phase
from rainpulse_algo.multiband.quality import phase_linear, x_qc
from rainpulse_algo.multiband.candidate_kernel import nonmet_candidate
from .helpers import fixture, config, station


@pytest.mark.parametrize('name', ['PHIDP','RHOHV','ZDR','DBZH','SNR','SNRH'])
@pytest.mark.parametrize('suffix', ['_VALID_MASK','_AVAILABLE_MASK'])
def test_phase_honours_every_actual_mask(name, suffix):
    v,_=fixture('weather',rays=8,gates=40,phase_contract=True)
    c=config(receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    cut=v.sweeps[0];shape=cut.fields['DBZH'].shape
    raw=copy.deepcopy(cut.fields)
    cut.fields[name+suffix]=np.zeros(shape,np.uint8)
    work,valid,_=prepare_phase(cut,np.zeros(shape,bool),c,station(phase=True).x_qc)
    assert not valid.any()
    pia,kdp,_=phase_linear(work,station(phase=True).x_qc,anchor_verified=True,initial_pia_db=0)
    assert not np.isfinite(pia).any() and not np.isfinite(kdp).any()
    for k,a in raw.items():np.testing.assert_array_equal(cut.fields[k],a)


@pytest.mark.parametrize('maskname',['PHIDP_VALID_MASK','RHOHV_AVAILABLE_MASK','SNR_VALID_MASK','ZDR_AVAILABLE_MASK'])
def test_one_bad_gate_breaks_propagation_without_a_restart(maskname):
    v,_=fixture('weather',rays=8,gates=40,phase_contract=True);cut=v.sweeps[0]
    a=np.ones(cut.fields['DBZH'].shape,np.uint8);a[:,15]=0;cut.fields[maskname]=a
    work,valid,_=prepare_phase(cut,np.zeros(a.shape,bool),config(),station(phase=True).x_qc)
    assert valid[:,:15].all() and valid[:,16:].all() and not valid[:,15].any()
    pia,_,_=phase_linear(work,station(phase=True).x_qc,anchor_verified=True,initial_pia_db=0)
    assert np.isfinite(pia[:,:15]).all() and np.isnan(pia[:,15:]).all()


@pytest.mark.parametrize('bad',[2,-1,np.nan])
def test_malformed_masks_are_not_silently_cast(bad):
    f={'SNRH':np.ones((2,3)), 'SNR_VALID_MASK':np.full((2,3),bad)}
    with pytest.raises(ValueError):moment_support(f,'SNRH',(2,3))


def test_alias_masks_intersect_but_do_not_invent_or_mutate_values():
    a=np.arange(6,dtype='float32').reshape(2,3)
    f={'SNRH':a,'SNR':a+1,'SNRH_VALID_MASK':np.array([[1,0,1],[1,1,1]]),
       'SNR_AVAILABLE_MASK':np.array([[1,1,0],[1,1,1]])}
    out=moment_support(f,'SNR',(2,3))
    assert out.source=='SNRH' and out.values is a
    np.testing.assert_array_equal(out.valid,[[1,0,0],[1,1,1]])
    assert not moment_support({},'SNR',(2,3)).valid.any()
    with pytest.raises(ValueError):binary_mask({'a':np.ones((1,3))},'a',(2,3))


@pytest.mark.parametrize('key',['RHOHV_VALID_MASK','SNRH_AVAILABLE_MASK','DBZH_VALID_MASK'])
def test_baseline_candidate_cannot_use_declared_invalid_moments(key):
    v,row=fixture('flat',rays=12,gates=150);cut=v.sweeps[0];f=cut.fields
    f['DBZH'][row,70]=60
    cfg=station().x_qc
    echo=f['OBSERVED_MASK']==1;good=np.ones(echo.shape,bool)
    baseline=nonmet_candidate(f,echo,good,cfg,cut.range_m)
    assert baseline[row,70]
    f[key]=np.zeros(echo.shape,np.uint8)
    assert not nonmet_candidate(f,echo,good,cfg,cut.range_m).any()


def test_active_invalid_ref_is_uncertain_not_noecho():
    v,_=fixture('weather',rays=24,gates=40)
    v.sweeps[0].fields['DBZH_VALID_MASK']=np.zeros((24,40),np.uint8)
    c=config(mode='quarantine',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    out=x_qc(v,station(c),'a'*64).sweeps[0].fields
    assert not out['REFLECTIVITY_ELIGIBLE_FOR_CR'].any()
    assert (out['QC_ACTION']==3).all() and not out['NO_ECHO_MASK'].any()
    assert np.isfinite(out['DBZH_RAW']).all() and np.isfinite(out['DBZH_QC_DISPLAY']).all()


@pytest.mark.parametrize('key',['DBZH_VALID_MASK','SNR_AVAILABLE_MASK','SNRH_VALID_MASK'])
def test_uncalibrated_experiment_does_not_admit_declared_invalid_values(key):
    from rainpulse_algo.multiband.experimental import experimental_fields
    v,_=fixture('weather',rays=24,gates=40)
    v.sweeps[0].fields[key]=np.zeros((24,40),np.uint8)
    c=config(mode='quarantine',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    qc=x_qc(v,station(c),'a'*64).sweeps[0].fields
    _,admitted,_=experimental_fields(qc,'X',allow_missing_snr=True)
    assert not admitted.any()
    assert not qc['NO_ECHO_MASK'].any() and np.isfinite(qc['DBZH_RAW']).all()


def test_snr_alias_only_native_bundle_reaches_baseline_with_same_measurement():
    v,_=fixture('weather',rays=24,gates=40)
    f=v.sweeps[0].fields;f['SNR']=f.pop('SNRH')
    c=config(mode='quarantine',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    qc=x_qc(v,station(c),'a'*64).sweeps[0].fields
    np.testing.assert_array_equal(qc['SNRH'],f['SNR'])
    from rainpulse_algo.multiband.quality import Flag
    assert not ((qc['MB_QC_FLAGS'] & int(Flag.LOW_SNR))!=0).any()


def test_invalid_numeric_reflectivity_breaks_local_phase_path():
    v,_=fixture('weather',rays=8,gates=40,phase_contract=True);cut=v.sweeps[0]
    cut.fields['DBZH'][:,15]=1000
    work,valid,_=prepare_phase(cut,np.zeros((8,40),bool),config(),station(phase=True).x_qc)
    assert not valid[:,15].any()
    pia,_,_=phase_linear(work,station(phase=True).x_qc,anchor_verified=True,initial_pia_db=0)
    assert np.isnan(pia[:,15:]).all()
