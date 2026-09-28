import numpy as np
from .helpers import config
from .test_radial_source import narrow_source


def detect(v, protected=None):
    from rainpulse_algo.multiband.xqc_v2.geometry import adapt
    from rainpulse_algo.multiband.xqc_v2.radial_source import detect
    cfg=config(radial_source_enabled=True, radial_source_block_model_enabled=True,
               noise_censor_snr_db=3., radial_source_maximum_width_deg=7., radial_maximum_dbzh=45.)
    s=adapt(v.sweeps[0],cfg).sweep
    return detect(s,cfg,protected=np.zeros(s.shape,bool) if protected is None else protected)[0]


def bursty():
    v,row=narrow_source();f=v.sweeps[0].fields
    f['DBZH'][row,::2]=np.nan
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH'])
    return v,row


def test_intermittent_receiver_spoke_is_not_rejected_for_missing_ref():
    v,row=bursty();m=detect(v)
    assert m[row,400:].sum()>250
    assert not m[~np.isfinite(v.sweeps[0].fields['DBZH'])].any()


def test_distant_stationary_regime_not_vetoed_by_near_range_power_change():
    v,row=bursty();f=v.sweeps[0].fields;r=v.sweeps[0].range_m
    f['SNRH'][row,r<20000]-=7
    f['DBZH'][row,r<20000]-=7
    assert detect(v)[row,r>25000].sum()>250


def test_broad_rain_flat_reflectivity_and_short_fragment_do_not_form_source():
    v,row=bursty()
    for j in range(row-5,row+6):
        for k in ('DBZH','SNRH'):v.sweeps[0].fields[k][j]=v.sweeps[0].fields[k][row]
    assert not detect(v).any()
    v,row=bursty();f=v.sweeps[0].fields
    f['DBZH'][row,np.isfinite(f['DBZH'][row])]=25.
    assert not detect(v).any()
    v,row=bursty();v.sweeps[0].fields['DBZH'][row,200:]=np.nan
    assert not detect(v).any()


def test_missing_shoulder_and_explicit_weather_protection_do_not_act():
    v,row=bursty();v.sweeps[0].fields['SNRH'][row+1:row+7]=np.nan
    assert not detect(v).any()
    v,row=bursty();protected=np.zeros(v.sweeps[0].fields['DBZH'].shape,bool);protected[row]=True
    assert not detect(v,protected).any()


def test_target_rain_core_cannot_fit_itself():
    v,row=bursty();f=v.sweeps[0].fields;r=v.sweeps[0].range_m
    rain=(r>=35000)&(r<40000);f['DBZH'][row,rain]+=12;f['SNRH'][row,rain]+=12
    m=detect(v);assert m[row].sum()>200
    assert not m[row,rain].any()


def test_repeated_gate_amplitude_modes_are_not_left_as_dotted_spokes():
    v,row=bursty();f=v.sweeps[0].fields
    f['SNRH'][row,1::4]+=5
    f['DBZH'][row,1::4]+=5
    m=detect(v);valid=np.isfinite(f['DBZH'][row])&(v.sweeps[0].range_m>25000)
    assert m[row,valid].mean()>.95
    v,row=bursty();f=v.sweeps[0].fields;f['DBZH'][row,1::4]-=10
    m=detect(v);valid=np.isfinite(f['DBZH'][row])&(v.sweeps[0].range_m>25000)
    assert m[row,valid].mean()>.95


def test_block_source_obeys_budget_and_preserves_raw():
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
    v,row=bursty();raw=v.sweeps[0].fields['DBZH'].copy()
    cfg=config(radial_source_enabled=True,radial_source_block_model_enabled=True,
               noise_censor_snr_db=3.,maximum_new_exclusion_fraction=.1)
    ev=evaluate_cut(v.sweeps[0],v.metadata,cfg)
    assert ev.arrays['XQC_RADIAL_SOURCE_MASK'].any()
    assert ev.record['status']=='ACTION_BUDGET_ABSTAINED'
    assert not ev.arrays['XQC_QUARANTINE_MASK'].any()
    np.testing.assert_array_equal(raw,v.sweeps[0].fields['DBZH'])


def test_missing_angular_sector_cannot_supply_source_shoulders():
    from dataclasses import replace
    v,row=bursty();cut=v.sweeps[0]
    keep=np.ones(len(cut.azimuth_deg),bool);keep[row+1:row+9]=False
    v.sweeps[0]=replace(cut,azimuth_deg=cut.azimuth_deg[keep],elevation_deg=cut.elevation_deg[keep],
                       ray_time_epoch=cut.ray_time_epoch[keep],fields={k:a[keep] for k,a in cut.fields.items()})
    assert not detect(v).any()
