"""Fan width and reflectivity alone are not precipitation evidence."""
import numpy as np
import pytest
from .helpers import config
from .test_radial_source import narrow_source


def fan(elevation=.5, bearing=120, strength=50):
    v,row=narrow_source();cut=v.sweeps[0];cut.elevation_deg[:]=elevation
    f=cut.fields;r=cut.range_m
    for offset in range(-10,11):
        j=(bearing+offset)%360
        power=strength-2*abs(offset)
        f['SNRH'][j]=power
        f['DBZH'][j]=power+20*np.log10(r/1000)-22
        f['DBZH'][j,::3]=np.nan
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH'])
    return v,bearing


def detect(v, protected=None):
    from rainpulse_algo.multiband.xqc_v2.geometry import adapt
    from rainpulse_algo.multiband.xqc_v2.radial_source import detect
    cfg=config(radial_source_enabled=True,radial_source_fan_model_enabled=True,noise_censor_snr_db=3.)
    s=adapt(v.sweeps[0],cfg).sweep
    return detect(s,cfg,protected=np.zeros(s.shape,bool) if protected is None else protected)[0]


@pytest.mark.parametrize('elevation,bearing,strength',[(.47,180,33),(3.36,195,58),(6.1,350,45)])
def test_strong_receiver_fan_at_arbitrary_elevation_and_bearing(elevation,bearing,strength):
    v,row=fan(elevation,bearing,strength)
    cut=v.sweeps[0];m=detect(v)
    target=np.isfinite(cut.fields['DBZH'][row])&(cut.range_m>25000)
    assert m[row,target].mean()>.9


@pytest.mark.parametrize("scale", [4000.,7000.,10000.,20000.])
def test_true_range_varying_weather_is_not_a_receiver_fan(scale):
    v,row=fan();f=v.sweeps[0].fields;r=v.sweeps[0].range_m
    for j in range(row-10,row+11):
        f['DBZH'][j]=35+5*np.sin(r/scale)
        f['SNRH'][j]=f['DBZH'][j]-20*np.log10(r/1000)+22
    assert not detect(v)[row-10:row+11].any()


def test_strong_local_rain_core_and_missing_shoulders_are_preserved():
    v,row=fan();r=v.sweeps[0].range_m;g=(r>=35000)&(r<40000)
    for k in ['DBZH','SNRH']:v.sweeps[0].fields[k][row,g]+=15
    assert not detect(v)[row,g].any()
    v,row=fan();v.sweeps[0].fields['SNRH'][:]=np.nan
    assert not detect(v).any()


def test_protected_fan_and_flat_reflectivity_are_preserved():
    v,row=fan();shape=v.sweeps[0].fields['DBZH'].shape
    assert not detect(v,np.ones(shape,bool)).any()
    v,row=fan();f=v.sweeps[0].fields
    f['DBZH'][row-10:row+11]=40.
    assert not detect(v)[row-10:row+11].any()


def test_fan_does_not_bridge_an_unobserved_angular_sector():
    from dataclasses import replace
    v,row=fan();cut=v.sweeps[0]
    keep=np.ones(len(cut.azimuth_deg),bool);keep[row+11:row+25]=False
    v.sweeps[0]=replace(cut,azimuth_deg=cut.azimuth_deg[keep],elevation_deg=cut.elevation_deg[keep],
        ray_time_epoch=cut.ray_time_epoch[keep],fields={k:a[keep] for k,a in cut.fields.items()})
    assert not detect(v)[row-10:row+11].any()


def test_mixed_response_modes_with_changing_proportions_still_form_source():
    v,row=fan();f=v.sweeps[0].fields;r=v.sweeps[0].range_m
    for j in range(row-10,row+11):
        # Same two source modes, but their proportions change with distance.
        gate=np.arange(len(r));low=(gate%10)<np.where((r//5000).astype(int)%2,7,2)
        f['DBZH'][j,low]-=23
    m=detect(v);target=np.isfinite(f['DBZH'][row])&(r>25000)
    assert m[row,target].mean()>.9


def test_small_range_response_trend_is_predicted_at_held_out_gates():
    v,row=fan();f=v.sweeps[0].fields;r=v.sweeps[0].range_m
    f['DBZH'][row-10:row+11]+=5*np.log10(r/1000)
    m=detect(v);target=np.isfinite(f['DBZH'][row])&(r>60000)
    assert m[row,target].mean()>.9


def test_strong_isolated_spoke_does_not_require_a_fan_neighbour():
    v,row=narrow_source();f=v.sweeps[0].fields
    f['SNRH'][row]+=40;f['DBZH'][row]+=40
    m=detect(v);target=np.isfinite(f['DBZH'][row])&(v.sweeps[0].range_m>25000)
    assert m[row,target].mean()>.9



def test_sparse_ref_rays_inside_measured_receiver_fan_keep_family_support():
    v,row=fan();f=v.sweeps[0].fields
    for j in range(row-10,row+11):
        if (j-row)%4:f['DBZH'][j]=np.nan
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH'])
    m=detect(v);target=np.isfinite(f['DBZH'][row])&(v.sweeps[0].range_m>25000)
    assert m[row,target].mean()>.9


def test_reflectivity_dropout_inside_confirmed_receiver_source_is_contaminated():
    v,row=fan();f=v.sweeps[0].fields;r=v.sweeps[0].range_m
    # REF drops while independent receiver power remains on the fitted source.
    gates=(np.arange(len(r))%31==1)&(r>25000)
    f['DBZH'][row,gates]-=25
    m=detect(v);target=gates&np.isfinite(f['DBZH'][row])
    assert m[row,target].mean()>.9


def test_intermittent_secondary_response_mode_does_not_break_primary_corridor():
    v,row=fan(strength=35)
    cut=v.sweeps[0];r=cut.range_m;f=cut.fields
    # The secondary processor mode crosses the 90th percentile from block to
    # block. The stationary primary receiver source must remain detectable.
    rng=np.random.default_rng(710)
    for j in range(row-10,row+11):
        gates=np.arange(len(r));fraction=np.where((r//5000).astype(int)%2,.06,.18)
        upper=rng.random(len(r))<fraction
        f['DBZH'][j,upper]+=10
    mask=detect(v);target=np.isfinite(f['DBZH'][row])&(r>25000)
    assert mask[row,target].mean()>.95


def test_measured_intermittent_fan_skirts_do_not_break_family_support():
    v,row=fan(strength=35);cut=v.sweeps[0];f=cut.fields;r=cut.range_m
    for j in range(row-10,row+11):
        power=np.full(len(r),5.)
        if j != row:
            power[:]=2.5;power[np.arange(len(r))%5<2]=5.5
        f['SNRH'][j]=power
        f['DBZH'][j]=power+20*np.log10(r/1000)-22
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH'])
    mask=detect(v);target=(r>25000)
    assert mask[row,target].mean()>.95


def test_single_observed_corridor_in_measured_fan_has_its_own_range_evidence():
    v,row=fan();cut=v.sweeps[0];f=cut.fields
    for j in range(row-10,row+11):
        if j != row:f['DBZH'][j]=np.nan
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH'])
    mask=detect(v);target=np.isfinite(f['DBZH'][row])&(cut.range_m>25000)
    assert mask[row,target].mean()>.95


def test_short_receiver_dropouts_inside_source_are_associated_but_rain_is_not():
    v,row=fan(strength=35);cut=v.sweeps[0];f=cut.fields;r=cut.range_m
    dropout=(np.arange(len(r))%37==5)&(r>25000)
    for key in ('DBZH','SNRH'):f[key][row,dropout]-=7
    target=dropout&np.isfinite(f['DBZH'][row])
    assert detect(v)[row,target].mean()>.95
    v,row=fan(strength=35);f=v.sweeps[0].fields
    for key in ('DBZH','SNRH'):f[key][row,dropout]+=15
    assert not detect(v)[row,dropout].any()


@pytest.mark.parametrize('barrier',['snr_missing','protected','quiet'])
def test_association_cannot_cross_unmeasured_receiver_or_protected_gate(barrier):
    v,row=fan(strength=35);cut=v.sweeps[0];f=cut.fields;r=cut.range_m
    # Restore REF at the local test interval so this only exercises SNR/protection.
    g=int(np.searchsorted(r,45000));f['DBZH'][row,g-2:g+4]=35+20*np.log10(r[g-2:g+4]/1000)-22
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH'])
    for key in ('DBZH','SNRH'):f[key][row,g:g+2]-=7
    protected=np.zeros(f['DBZH'].shape,bool)
    if barrier=='snr_missing':f['SNRH'][row,g]=np.nan
    elif barrier=='quiet':f['SNRH'][row,g]=0.
    else:protected[row,g]=True
    assert not detect(v,protected)[row,g+1]
