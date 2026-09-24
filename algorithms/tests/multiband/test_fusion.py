# ruff: noqa: E501, E701, E702, I001, E402
import numpy as np
import pytest
from dataclasses import replace
from rainpulse_algo.multiband.quality import x_qc, accept_s_qc
from rainpulse_algo.multiband.fusion import build_composite, _nearest_ray
from conftest import volume, TARGET

CUTOFF = "2026-09-23T00:10:10Z"


def processed(net):
    s = accept_s_qc(volume(net.stations["s1"], age=300, values=(40.,55.), elevations=(1.,5.)), net.stations["s1"], net.sha256)
    x = x_qc(volume(net.stations["x1"]), net.stations["x1"], net.sha256)
    return s, x


def test_same_height_select_then_vertical_max(net):
    s, x = processed(net)
    result = build_composite([x, s], net, "local", TARGET, CUTOFF)
    assert result.arrays["CR_DBZH"][0,0] == 55.
    winner = result.metadata["sources"][result.arrays["WINNER_SOURCE"][0,0]]
    assert winner["band"] == "S" and winner["sweep_number"] == 1
    assert result.arrays["WINNER_AGE_SECONDS"][0,0] == 300.
    assert result.arrays["VALID_LAYER_COUNT"][0,0] == 2
    assert not result.metadata["operational_eligible"] and not result.metadata["qpe_eligible"]


def test_six_minute_product_rejects_intermediate_minutes(net):
    net.products["local"] = replace(net.products["local"], cadence_seconds=360)
    s, x = processed(net)
    with pytest.raises(ValueError, match="boundary"):
        build_composite([s, x], net, "local", TARGET, CUTOFF)
    out = build_composite([s, x], net, "local", "2026-09-23T00:12:00Z", "2026-09-23T00:12:10Z")
    assert out.metadata["cadence_seconds"] == 360


def test_new_x_wins_comparable_lower_level_and_expiry_removes_old(net):
    s, x = processed(net); s.sweeps = s.sweeps[:1]
    result = build_composite([s,x], net,"local", TARGET,CUTOFF)
    assert result.arrays["CR_DBZH"][0,0] == 35.
    assert result.metadata["sources"][result.arrays["WINNER_SOURCE"][0,0]]["band"] == "X"
    # No new input does NOT mean stale pixels can be held forever.
    expired = build_composite([s,x],net,"local","2026-09-23T00:21:00Z","2026-09-23T00:21:10Z")
    assert np.isnan(expired.arrays["CR_DBZH"]).all() and not expired.arrays["OBSERVED_MASK"].any()
    assert len(expired.metadata["skipped"]) == 2


def test_uncertain_x_does_not_beat_trusted_s(net):
    s, x = processed(net); s.sweeps = s.sweeps[:1]
    f = x.sweeps[0].fields; f["DBZH_QC"][:] = 80; f["REFLECTIVITY_ELIGIBLE_FOR_CR"][:] = 0; f["CR_UNCERTAIN_MASK"][:] = 1
    out = build_composite([s,x],net,"local",TARGET,CUTOFF)
    assert out.arrays["CR_DBZH"][0,0] == 40
    assert out.arrays["CR_UNCERTAIN_DBZH"][0,0] == 80


def test_order_independent(net):
    a,b = processed(net)
    first = build_composite([a,b],net,"local",TARGET,CUTOFF)
    second = build_composite([b,a],net,"local",TARGET,CUTOFF)
    for key in first.arrays:
        np.testing.assert_array_equal(first.arrays[key], second.arrays[key])
    assert first.metadata == second.metadata


def test_missing_vs_noecho(net):
    s = net.stations["x1"]
    for noecho, missing in [(True, False),(False,True)]:
        v=x_qc(volume(s,noecho=noecho,missing=missing),s,net.sha256)
        out=build_composite([v],net,"local",TARGET,CUTOFF)
        assert np.isnan(out.arrays["CR_DBZH"]).all()
        assert bool(out.arrays["NO_ECHO_MASK"][0,0]) is noecho
        assert bool(out.arrays["OBSERVED_MASK"][0,0]) is (not missing)


def test_future_and_cutoff(net):
    s,x = processed(net)
    out=build_composite([x],net,"local","2026-09-23T00:09:00Z",CUTOFF)
    assert not out.arrays["OBSERVED_MASK"].any()
    x.metadata["available_at"] = "2026-09-23T00:11:00Z"
    out=build_composite([x],net,"local",TARGET,CUTOFF)
    assert not out.arrays["OBSERVED_MASK"].any()


def test_sector_gap_and_circular_azimuth(net):
    indices, offsets=_nearest_ray(np.array([359.,1.,90.]),np.array([0.,89.]))
    assert list(offsets)==[1.,1.]
    _,x=processed(net)
    x.sweeps[0].azimuth_deg=(x.sweeps[0].azimuth_deg+20)%360
    x.sweeps[0].fields["OBSERVED_MASK"][:]=0
    # Only northward rays observed; cannot fill an eastward sector hole.
    x.sweeps[0].fields["OBSERVED_MASK"][340,:]=1
    x.sweeps[0].fields["REFLECTIVITY_ELIGIBLE_FOR_CR"][:]=x.sweeps[0].fields["OBSERVED_MASK"]
    out=build_composite([x],net,"local",TARGET,CUTOFF)
    assert not out.arrays["OBSERVED_MASK"].any()


def test_duplicate_station_and_changed_release_rejected(net):
    s,x=processed(net)
    with pytest.raises(ValueError,match="one causal"):
        build_composite([x,x],net,"local",TARGET,CUTOFF)
    x.metadata["network_sha256"]="f"*64
    with pytest.raises(ValueError,match="release"):
        build_composite([x],net,"local",TARGET,CUTOFF)
