from dataclasses import replace
import numpy as np
import pytest
from .helpers import config,fixture,station
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig


def test_x_coherent_short_range_and_raw():
    vol,row=fixture();s=vol.sweeps[0];before={k:v.copy() for k,v in s.fields.items()}
    ev=evaluate_cut(s,vol.metadata,config(clutter_enabled=False,isolation_enabled=False,radial_objects_enabled=False))
    assert ev.arrays["XQC_RECEIVER_MASK"][row].sum()>300
    for k,v in before.items():np.testing.assert_equal(v,s.fields[k])


def test_flat_radial_uses_shared_objects_and_polar():
    vol,row=fixture("flat"); ev=evaluate_cut(vol.sweeps[0],vol.metadata,config(receiver_enabled=False,clutter_enabled=False,isolation_enabled=False))
    assert ev.arrays["XQC_RADIAL_POLAR_MASK"][row].sum()>400


def test_flat_high_rho_not_deleted_from_shape():
    vol,row=fixture("flat");vol.sweeps[0].fields["RHOHV"][row]=.99
    ev=evaluate_cut(vol.sweeps[0],vol.metadata,config(receiver_enabled=False,clutter_enabled=False,isolation_enabled=False))
    assert not ev.arrays["XQC_RADIAL_POLAR_MASK"].any()


def test_weather_and_clutter():
    for kind in ("weather","clutter"):
        v,_=fixture(kind,gates=180)
        ev=evaluate_cut(v.sweeps[0],v.metadata,config(receiver_enabled=False,radial_objects_enabled=False,isolation_enabled=False))
        if kind=="weather":assert not ev.arrays["XQC_PROPOSED_MASK"].any()
        else:assert ev.arrays["XQC_CLUTTER_MASK"].sum()>100


def test_hard_weather_blocks_source():
    v,row=fixture();v.sweeps[0].fields["WEATHER_PROTECTED_MASK"]=np.ones(v.sweeps[0].fields["DBZH"].shape,"uint8")
    ev=evaluate_cut(v.sweeps[0],v.metadata,config(clutter_enabled=False,isolation_enabled=False))
    assert not ev.arrays["XQC_PROPOSED_MASK"].any()


def test_independent_snr_support_and_original_order():
    v,row=fixture();s=v.sweeps[0];s.fields["DBZH"][row,200:250]=np.nan;s.fields["OBSERVED_MASK"][row,200:250]=0
    order=np.random.default_rng(4).permutation(len(s.azimuth_deg))
    s=replace(s,azimuth_deg=s.azimuth_deg[order],elevation_deg=s.elevation_deg[order],ray_time_epoch=s.ray_time_epoch[order],fields={k:a[order] for k,a in s.fields.items()})
    view=adapt(s,config())
    np.testing.assert_equal(view.restore(view.sweep.fields["DBZH"]),s.fields["DBZH"])
    rr=int(np.where(view.sweep.azimuth==row*3)[0][0])
    assert view.sweep.available["SNR"][rr,200:250].all()
    assert not view.sweep.available["DBZH"][rr,200:250].any()


def test_duplicate_rows_are_not_deleted_or_donated():
    v,row=fixture();s=v.sweeps[0];s.azimuth_deg[1]=s.azimuth_deg[0]
    view=adapt(s,config());assert (~view.sweep.good).sum()==2
    np.testing.assert_equal(view.restore(view.sweep.fields["DBZH"]),s.fields["DBZH"])


@pytest.mark.parametrize("mode",["audit","cr_only","quarantine"])
def test_pipeline_action_contract(mode):
    v,row=fixture();c=config(mode=mode,clutter_enabled=False,isolation_enabled=False,radial_objects_enabled=False)
    original={k:a.copy() for k,a in v.sweeps[0].fields.items()}
    parent=x_qc(v,station(),"b"*64)
    out=x_qc(v,station(c),"b"*64);f=out.sweeps[0].fields
    assert not f["QPE_ELIGIBLE_MASK"].any()
    if mode=="audit":
        for k,a in parent.sweeps[0].fields.items():np.testing.assert_equal(f[k],a)
    else:
        assert f["XQC_WITHHELD_MASK"].sum()>300
        assert not np.any(f["REFLECTIVITY_ELIGIBLE_FOR_CR"] & f["XQC_WITHHELD_MASK"])
        if mode=="quarantine":assert (f["QC_ACTION"][row]==2).sum()>300
        else:assert not (f["QC_ACTION"]==2).any()
    for k,a in original.items():np.testing.assert_equal(a,v.sweeps[0].fields[k])


def test_budget_abstains_from_rejection_but_preserves_admission_hold():
    v,_=fixture("clutter",gates=180)
    c=config(mode="quarantine",receiver_enabled=False,radial_objects_enabled=False,isolation_enabled=False,maximum_new_exclusion_fraction=.001)
    out=x_qc(v,station(c),"b"*64).sweeps[0]
    assert out.xqc_diagnostics["status"]=="ACTION_BUDGET_ABSTAINED"
    assert out.fields["XQC_WITHHELD_MASK"].any()
    assert not out.fields["XQC_REJECTED_MASK"].any()


@pytest.mark.parametrize("bad",[{"unexpected":1},{"doppler_verified":True},{"mode":"prod"},{"maximum_sweep_gates":0}])
def test_invalid_config(bad):
    with pytest.raises(ValueError):XQCConfig.model_validate(bad)


def test_isolation_known_noecho_is_not_unknown():
    v,_=fixture('empty',rays=180,gates=300);f=v.sweeps[0].fields
    f['DBZH'][:]=np.nan;f['NO_ECHO_MASK'][:]=1;f['SNRH'][:]=20.;f['RHOHV'][:]=.5;f['ZDR'][:]=5.
    f['DBZH'][90:93,140:152]=20.;f['NO_ECHO_MASK'][90:93,140:152]=0
    f['PHIDP'][:]=np.random.default_rng(4).uniform(0,360,f['PHIDP'].shape)
    c=config(receiver_enabled=False,radial_objects_enabled=False)
    a=evaluate_cut(v.sweeps[0],v.metadata,c)
    assert a.arrays['XQC_ISOLATED_MASK'].sum()==36
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH']).astype('uint8');f['NO_ECHO_MASK'][:]=0
    b=evaluate_cut(v.sweeps[0],v.metadata,c)
    assert not b.arrays['XQC_ISOLATED_MASK'].any()


def test_phase_spacing_75m_not_s_100m_default():
    v,_=fixture('clutter',gates=100)
    e=evaluate_cut(v.sweeps[0],v.metadata,config(receiver_enabled=False,radial_objects_enabled=False,isolation_enabled=False))
    assert np.isfinite(e.arrays['XQC_PHI_JITTER_DEG']).any()


def test_nonuniform_geometry_abstains():
    v,_=fixture(gates=100);v.sweeps[0].range_m[20]+=.5
    e=evaluate_cut(v.sweeps[0],v.metadata,config())
    assert e.record['status']=='RESOURCE_OR_GEOMETRY_ABSTAINED'
    assert not e.arrays['XQC_PROPOSED_MASK'].any()


def test_cut_metadata_constant_across_stream():
    v,_=fixture('weather',gates=60)
    c=config(receiver_enabled=False,radial_objects_enabled=False,isolation_enabled=False)
    a=x_qc(v,station(c),'b'*64)
    v.sweeps[0].number=1;v.sweeps[0].fields['RHOHV'][:]=.5
    b=x_qc(v,station(c),'b'*64)
    assert a.metadata==b.metadata
    assert a.sweeps[0].xqc_diagnostics['sweep_number']!=b.sweeps[0].xqc_diagnostics['sweep_number']


def test_doppler_contract_not_inferred_from_fields():
    v,_=fixture('clutter',gates=60)
    v.sweeps[0].fields['VR']=np.zeros((120,60),'float32')
    v.sweeps[0].fields['SW']=np.full((120,60),.2,'float32')
    c=config(receiver_enabled=False,radial_objects_enabled=False,isolation_enabled=False,
             doppler_verified=True,doppler_verification_id='receipt',doppler_waveform='vcp',nyquist_velocity_mps=15.)
    e=evaluate_cut(v.sweeps[0],v.metadata,c)
    assert e.record['geometry']['doppler_action_verified'] is False
    v.metadata.update(doppler_verification_id='receipt',doppler_waveform='vcp',nyquist_velocity_mps=15.)
    e=evaluate_cut(v.sweeps[0],v.metadata,c)
    assert e.record['geometry']['doppler_action_verified'] is True
