"""Discontinuous RAW nomination does not confer contamination or fill gates."""
from dataclasses import replace
import numpy as np
import pytest
from .helpers import fixture, config
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.fragment_geometry import nominate


def sample(row=70, elevation=2.4):
    volume, _ = fixture('empty', rays=360, gates=1000)
    cut = volume.sweeps[0]
    cut.fields['DBZH'][:] = 0
    cut.fields['SNRH'][:] = -4
    target = np.zeros(1000, bool)
    target[200:900] = True
    target[::4] = False  # Every raw component is much shorter than a radial.
    cut.fields['DBZH'][row, target] = 15
    cut.fields['SNRH'][row, target] = 5
    cfg = config(noise_censor_snr_db=3, radial_source_maximum_width_deg=7)
    cut = replace(cut, elevation_deg=np.full(360, elevation))
    return adapt(cut, cfg).sweep, cfg, target


@pytest.mark.parametrize('row,elevation', [(0,.5), (70,2.4), (359,14.5)])
def test_discontinuous_geometry_preserves_raw_gates(row,elevation):
    sweep,cfg,target=sample(row,elevation)
    before=sweep.digest
    nominated,record=nominate(sweep,cfg)
    assert nominated[row,target].all()
    assert not nominated[row,~target].any()
    assert not nominated[np.arange(360)!=row].any()
    assert sweep.digest==before
    assert record['diagnostic_only'] is True
    assert record['families'] and record['candidate_gates']==int(target.sum())


def test_missing_receiver_interval_splits_family():
    sweep,cfg,target=sample()
    available=dict(sweep.available)
    available['SNR']=available['SNR'].copy()
    available['SNR'][70,500:600]=False
    sweep=replace(sweep,available=available)
    nominated,record=nominate(sweep,cfg)
    assert not nominated[70,500:600].any()
    assert len(record['families'])==2
    assert all(f['range_max_m']<sweep.ranges[500] or f['range_min_m']>sweep.ranges[599] for f in record['families'])


def test_wide_weather_without_quiet_shoulders_is_not_nominated():
    volume,_=fixture('weather',rays=360,gates=1000)
    cfg=config(noise_censor_snr_db=3,radial_source_maximum_width_deg=7)
    nominated,record=nominate(adapt(volume.sweeps[0],cfg).sweep,cfg)
    assert not nominated.any()
    assert not record['families']


def test_acquisition_gap_cannot_supply_quiet_angular_boundary():
    sweep,cfg,target=sample()
    gap=sweep.gap_after.copy();gap[70]=True
    nominated,record=nominate(replace(sweep,gap_after=gap),cfg)
    assert not nominated.any()


def test_unknown_angular_receiver_is_not_a_quiet_flank():
    sweep,cfg,target=sample()
    available=dict(sweep.available)
    available['SNR']=available['SNR'].copy()
    available['SNR'][71]=False
    nominated,_=nominate(replace(sweep,available=available),cfg)
    assert not nominated.any()


def test_measured_long_range_gap_is_not_bridged():
    sweep,cfg,target=sample()
    fields=dict(sweep.fields); fields['DBZH']=fields['DBZH'].copy()
    fields['DBZH'][70,500:540]=0  # 3km, larger than the frozen 1.5km limit.
    nominated,record=nominate(replace(sweep,fields=fields),cfg)
    assert not nominated[70,500:540].any()
    assert len(record['families'])==2


def test_wide_fan_does_not_become_narrow_by_per_ray_grouping():
    sweep,cfg,target=sample()
    fields=dict(sweep.fields)
    fields['DBZH']=fields['DBZH'].copy(); fields['SNR']=fields['SNR'].copy()
    fields['DBZH'][60:81,target]=15;fields['SNR'][60:81,target]=5
    nominated,_=nominate(replace(sweep,fields=fields),cfg)
    assert not nominated.any()


def test_high_rho_radial_geometry_is_unqualified_and_unchanged():
    sweep,cfg,target=sample()
    nominated,record=nominate(sweep,cfg)
    assert nominated[70,target].all()
    assert (sweep.fields['RHOHV'][70,target]>.97).all()
    assert all(f['classification']=='UNQUALIFIED_GEOMETRY' for f in record['families'])
    assert 'QC_ACTION' not in sweep.fields
