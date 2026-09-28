from dataclasses import replace
import io,json
import numpy as np
import pytest
from .helpers import fixture,config,station
from rainpulse_algo.multiband.quality import x_qc,phase_linear
from rainpulse_algo.multiband.xqc_v2.pipeline import prepare_phase
from rainpulse_algo.multiband.xqc_v2.export import export_sweep,encode_arrays
from rainpulse_algo.multiband.xqc_v2.profiles import generate


def test_phase_path_no_restart_after_pollution():
    v,_=fixture("weather",gates=100,phase_contract=True);s=v.sweeps[0]
    blocked=np.zeros(s.fields['DBZH'].shape,bool);blocked[:,40]=True
    p=station(phase=True).x_qc
    cut,valid,down=prepare_phase(s,blocked,config(),p)
    pia,_,_=phase_linear(cut,p,anchor_verified=True,initial_pia_db=0.)
    assert np.isfinite(pia[:,:40]).all();assert np.isnan(pia[:,40:]).all()
    assert down[:,40:].all();assert s.fields['PHASE_VALID_MASK'].all()


@pytest.mark.parametrize('missing',['LIQUID_MASK','PHASE_VALID_MASK','PHIDP','RHOHV','SNRH'])
def test_missing_phase_evidence_is_not_invented(missing):
    v,_=fixture("weather",gates=100,phase_contract=True);s=v.sweeps[0];del s.fields[missing]
    c,valid,_=prepare_phase(s,np.zeros(s.fields['DBZH'].shape,bool),config(),station(phase=True).x_qc)
    assert not valid.any()


def test_no_verified_anchor_keeps_no_correction():
    v,_=fixture("weather",gates=100,phase_contract=True)
    pia,_,_=phase_linear(v.sweeps[0],station(phase=True).x_qc,anchor_verified=False,initial_pia_db=0.)
    assert np.isnan(pia).all()


def test_pia_limit_and_double_correction_guard():
    v,_=fixture("weather",gates=100,phase_contract=True)
    v.sweeps[0].fields['PHIDP'][:]=np.arange(100)*2.
    p=station(phase=True).x_qc
    pia,kdp,limit=phase_linear(v.sweeps[0],replace(p,max_pia_db=5.),anchor_verified=True,initial_pia_db=0.)
    assert limit.any();assert np.nanmax(pia)<=5.
    v.metadata['attenuation_status']='corrected'
    with pytest.raises(ValueError):x_qc(v,station(config(),phase=True),'b'*64)


def test_quantitative_ready_not_qpe_activation():
    v,_=fixture("weather",gates=100,phase_contract=True)
    v.metadata.update(phase_anchor_verified=True,pia_at_first_gate_db=0.)
    c=config(mode='cr_only',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    out=x_qc(v,station(c,calibrated=True,phase=True),'b'*64)
    f=out.sweeps[0].fields
    assert f['XQC_QUANTITATIVE_READY_MASK'].any()
    assert not f['QPE_ELIGIBLE_MASK'].any();assert not out.metadata['qpe_enabled']


def test_deterministic_native_export_and_original_indices():
    v,_=fixture("weather",gates=100)
    out=x_qc(v,station(config(mode='audit',receiver_enabled=False,radial_objects_enabled=False)),'b'*64)
    a={};b={}
    x=export_sweep(out.sweeps[0],out.metadata,a);y=export_sweep(out.sweeps[0],out.metadata,b)
    assert x==y and a==b
    with np.load(io.BytesIO(a[x['native']['object_path']]),allow_pickle=False) as z:
        np.testing.assert_equal(z['DBZH_RAW'],v.sweeps[0].fields['DBZH'])
        assert 'XQC_REASON' in z
    assert json.loads(a[x['evidence_path']])['qc_action_semantics']['3']=='UNCERTAIN'


def test_native_budget_enforced():
    with pytest.raises(ValueError):encode_arrays({'DBZH_RAW':np.ones((100,100))},maximum_decoded_bytes=10)
    with pytest.raises(ValueError):encode_arrays({'../bad':np.ones(1)})


def test_s_and_flags_not_changed_by_profile_generator():
    parent={'release_id':'test','stations':{'s1':{'band':'S','enabled':False},
        'x1':{'band':'X','x_qc_enabled':True,'enabled':False,'calibration_verified':False,'x_qc':{'attenuation':'none'}}}}
    before=json.dumps(parent,sort_keys=True)
    result=generate(parent,['x1'],'numpy_reference')
    for p in result.values():
        assert p['stations']['s1']==parent['stations']['s1']
        assert p['stations']['x1']['enabled'] is False
        assert p['stations']['x1']['calibration_verified'] is False
        assert p['stations']['x1']['x_qc']['attenuation']=='none'
    assert json.dumps(parent,sort_keys=True)==before
    with pytest.raises(ValueError):generate(parent,['s1'],'numpy_reference')
