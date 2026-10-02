"""Detection-floor censor: SNRH below the floor is noise, not echo."""
import numpy as np
import pytest

from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut

from .helpers import config, fixture, station


def weather_volume_with_ghosts():
    # Rain-like cut (rho .999, zdr .3): no radial or clutter path may fire,
    # so any proposal here comes from the censor alone.
    v, row = fixture("weather", rays=120, gates=600)
    f = v.sweeps[0].fields
    f["SNRH"][:] = 20.
    f["DBZH"][20:26, 400:560] = 18.  # ghost "echo" rendered from noise floor
    f["SNRH"][20:26, 400:560] = 1.
    return v


def test_noise_floor_gates_are_censored_and_rejected():
    v = weather_volume_with_ghosts()
    c = config(mode="quarantine", receiver_enabled=False, clutter_enabled=False,
               isolation_enabled=False, radial_objects_enabled=False,
               noise_censor_snr_db=3.)
    ev = evaluate_cut(v.sweeps[0], v.metadata, c)
    assert ev.record["module_records"]["noise_censor"]["status"] == "APPLIED"
    assert ev.arrays["XQC_NOISE_FLOOR_MASK"][20:26, 400:560].all()
    assert int(ev.arrays["XQC_NOISE_FLOOR_MASK"].sum()) == 6 * 160
    out = x_qc(v, station(c), "b" * 64).sweeps[0].fields
    assert (out["QC_ACTION"][20:26, 400:560] == 2).all()
    assert np.isnan(out["DBZH_QC"][20:26, 400:560]).all()


def test_censor_keeps_detectable_echo_on_the_same_cut():
    v = weather_volume_with_ghosts()
    c = config(mode="quarantine", receiver_enabled=False, clutter_enabled=False,
               isolation_enabled=False, radial_objects_enabled=False,
               noise_censor_snr_db=3.)
    out = x_qc(v, station(c), "b" * 64).sweeps[0].fields
    # SNRH 20 dB rain-like echo is never rejected (baseline may mark it 3).
    assert (out["QC_ACTION"][0:20] != 2).all()
    assert not np.isnan(out["DBZH_QC"][0:20]).any()


def test_disabled_by_default():
    v = weather_volume_with_ghosts()
    ev = evaluate_cut(v.sweeps[0], v.metadata,
                      config(receiver_enabled=False, clutter_enabled=False,
                             isolation_enabled=False, radial_objects_enabled=False))
    assert ev.record["module_records"]["noise_censor"]["status"] == "DISABLED"
    assert not ev.arrays["XQC_NOISE_FLOOR_MASK"].any()
    assert XQCConfig.model_validate({}).noise_censor_snr_db is None


def test_broken_snr_field_abstains_entirely():
    v = weather_volume_with_ghosts()
    v.sweeps[0].fields["SNRH"][:] = -5.  # whole field below any floor
    c = config(receiver_enabled=False, clutter_enabled=False,
               isolation_enabled=False, radial_objects_enabled=False,
               noise_censor_snr_db=3.)
    ev = evaluate_cut(v.sweeps[0], v.metadata, c)
    rec = ev.record["module_records"]["noise_censor"]
    assert rec["status"] == "ABSTAINED_SNR_FIELD_INVALID"
    assert rec["censored_gates"] == 0
    assert not ev.arrays["XQC_NOISE_FLOOR_MASK"].any()
    assert not ev.arrays["XQC_PROPOSED_MASK"].any()


def test_missing_snr_field_abstains():
    v = weather_volume_with_ghosts()
    del v.sweeps[0].fields["SNRH"]
    c = config(receiver_enabled=False, clutter_enabled=False,
               isolation_enabled=False, radial_objects_enabled=False,
               noise_censor_snr_db=3.)
    ev = evaluate_cut(v.sweeps[0], v.metadata, c)
    assert ev.record["module_records"]["noise_censor"]["status"] == "ABSTAINED_SNR_FIELD_INVALID"
    assert not ev.arrays["XQC_NOISE_FLOOR_MASK"].any()


def test_hard_weather_is_never_censored():
    v = weather_volume_with_ghosts()
    shape = v.sweeps[0].fields["DBZH"].shape
    v.sweeps[0].fields["WEATHER_PROTECTED_MASK"] = np.zeros(shape, "uint8")
    v.sweeps[0].fields["WEATHER_PROTECTED_MASK"][20:26, 400:560] = 1
    c = config(receiver_enabled=False, clutter_enabled=False,
               isolation_enabled=False, radial_objects_enabled=False,
               noise_censor_snr_db=3.)
    ev = evaluate_cut(v.sweeps[0], v.metadata, c)
    assert ev.record["module_records"]["noise_censor"]["status"] == "APPLIED"
    assert not ev.arrays["XQC_NOISE_FLOOR_MASK"].any()


def test_censor_bypasses_but_never_rescues_the_heuristic_budget():
    # Clutter mass drives the heuristic budget into abstention; the censor is
    # a calibration floor and still applies on its own.
    v, _ = fixture("clutter", gates=180)
    f = v.sweeps[0].fields
    f["DBZH"][:20, 100:140] = 18.
    f["SNRH"][:20, 100:140] = 1.
    c = config(mode="quarantine", receiver_enabled=False, radial_objects_enabled=False,
               noise_censor_snr_db=3., maximum_new_exclusion_fraction=.001)
    out = x_qc(v, station(c), "b" * 64).sweeps[0]
    assert out.xqc_diagnostics["status"] == "ACTION_BUDGET_ABSTAINED"
    np.testing.assert_equal(out.fields["XQC_PROPOSED_MASK"],
                            out.fields["XQC_NOISE_FLOOR_MASK"])
    assert out.fields["XQC_NOISE_FLOOR_MASK"].sum() > 0
    assert (out.fields["QC_ACTION"][:20, 100:140] == 2).all()


def repeated_floor_volume():
    from dataclasses import replace
    v = weather_volume_with_ghosts()
    cut = v.sweeps[0]
    # Two independent acquisitions at nearly the same bearing. Neither is a
    # reliable angular neighbour; each still has its own measured gate SNR.
    fields = {k: np.concatenate([a, a[20:21]]) for k, a in cut.fields.items()}
    repeated = len(cut.azimuth_deg)
    fields["SNRH"][repeated, 400:560] = 2.
    fields["DBZH"][repeated, 400:560] = 18.
    cut = replace(cut, fields=fields,
                  azimuth_deg=np.r_[cut.azimuth_deg, cut.azimuth_deg[20] + .005],
                  elevation_deg=np.r_[cut.elevation_deg, cut.elevation_deg[20]],
                  ray_time_epoch=np.r_[cut.ray_time_epoch, cut.ray_time_epoch[20] + 31])
    return replace(v, sweeps=[cut]), repeated


def floor_config(**changes):
    return config(mode="quarantine", receiver_enabled=False, clutter_enabled=False,
                  isolation_enabled=False, radial_objects_enabled=False,
                  noise_censor_snr_db=3., **changes)


def test_repeated_bearings_retain_independent_native_floor_censor():
    from rainpulse_algo.multiband.xqc_v2.geometry import adapt
    v, repeated = repeated_floor_volume()
    c = floor_config()
    view = adapt(v.sweeps[0], c)
    assert not view.restore(view.sweep.good)[[20, repeated]].any()
    assert not view.restore(view.sweep.available["SNR"])[[20, repeated]].any()
    raw = v.sweeps[0].fields["DBZH"].copy()
    out = x_qc(v, station(c), "b" * 64).sweeps[0].fields
    assert out["XQC_NOISE_FLOOR_MASK"][[20, repeated], 400:560].all()
    assert np.isnan(out["DBZH_QC"][[20, repeated], 400:560]).all()
    assert not out["REFLECTIVITY_ELIGIBLE_FOR_CR"][[20, repeated], 400:560].any()
    assert not out["XQC_AVAILABLE_MASK"][[20, repeated]].any()
    assert np.isfinite(out["DBZH_QC"][[20, repeated], :400]).all()
    np.testing.assert_array_equal(raw, out["DBZH"])


def test_repeated_floor_respects_original_masks_no_echo_and_weather():
    v, repeated = repeated_floor_volume()
    f = v.sweeps[0].fields
    shape = f["DBZH"].shape
    for name in ("SNRH_VALID_MASK", "SNR_AVAILABLE_MASK", "DBZH_VALID_MASK"):
        f[name] = np.ones(shape, "uint8")
    f["WEATHER_PROTECTED_MASK"] = np.zeros(shape, "uint8")
    f["MIXED_WEATHER_MASK"] = np.zeros(shape, "uint8")
    f["SNRH_VALID_MASK"][repeated, 410] = 0
    f["SNR_AVAILABLE_MASK"][repeated, 411] = 0
    f["DBZH_VALID_MASK"][repeated, 412] = 0
    f["OBSERVED_MASK"][repeated, 413] = 0
    f["NO_ECHO_MASK"][repeated, 414] = 1
    f["WEATHER_PROTECTED_MASK"][repeated, 415] = 1
    f["MIXED_WEATHER_MASK"][repeated, 416] = 1
    f["SNRH"][repeated, 417] = np.nan
    f["SNRH"][repeated, 418] = 3.  # strict floor, equality is detectable
    f["DBZH"][repeated, 419] = 90.  # invalid moment is not observed noise
    ev = evaluate_cut(v.sweeps[0], v.metadata, floor_config())
    assert not ev.arrays["XQC_NOISE_FLOOR_MASK"][repeated, 410:420].any()
    assert ev.arrays["XQC_NOISE_FLOOR_MASK"][repeated, 420:560].all()


def test_repeated_only_low_snr_cannot_bypass_whole_field_integrity_cap():
    v, repeated = repeated_floor_volume()
    f = v.sweeps[0].fields
    f["OBSERVED_MASK"][:] = 0
    f["OBSERVED_MASK"][[20, repeated], 400:560] = 1
    ev = evaluate_cut(v.sweeps[0], v.metadata, floor_config())
    assert ev.record["module_records"]["noise_censor"]["status"] == "ABSTAINED_SNR_FIELD_INVALID"
    assert not ev.arrays["XQC_NOISE_FLOOR_MASK"].any()


@pytest.mark.parametrize("angle,elevation,reverse", [(0., .5, False), (299.9, 3.4, True),
                                                     (123.5, 8.5, False)])
def test_native_floor_is_independent_of_bearing_elevation_and_acquisition_order(
    angle, elevation, reverse,
):
    from dataclasses import replace
    v, _ = repeated_floor_volume()
    cut = v.sweeps[0]
    c = floor_config()
    expected = evaluate_cut(cut, v.metadata, c).arrays["XQC_NOISE_FLOOR_MASK"]
    order = np.arange(len(cut.azimuth_deg))
    if reverse:
        order = order[::-1]
    cut = replace(cut, fields={k: a[order] for k, a in cut.fields.items()},
                  azimuth_deg=(cut.azimuth_deg[order] + angle) % 360,
                  elevation_deg=np.full(len(order), elevation),
                  ray_time_epoch=cut.ray_time_epoch[order] + 1800)
    actual = evaluate_cut(cut, v.metadata, c).arrays["XQC_NOISE_FLOOR_MASK"]
    np.testing.assert_array_equal(actual, expected[order])
    assert actual.any()


def test_native_floor_missing_receiver_coverage_cannot_become_noise():
    v, _ = repeated_floor_volume()
    f = v.sweeps[0].fields
    f["SNRH_AVAILABLE_MASK"] = np.zeros(f["DBZH"].shape, "uint8")
    f["SNRH_AVAILABLE_MASK"][:10] = 1
    ev = evaluate_cut(v.sweeps[0], v.metadata, floor_config())
    assert ev.record["module_records"]["noise_censor"]["status"] == "ABSTAINED_SNR_FIELD_INVALID"
    assert not ev.arrays["XQC_NOISE_FLOOR_MASK"].any()


def test_native_floor_audit_preserves_display_and_never_promotes_geometry():
    v, repeated = repeated_floor_volume()
    c = floor_config().model_copy(update={"mode": "audit"})
    out = x_qc(v, station(c), "b" * 64).sweeps[0].fields
    assert out["XQC_NOISE_FLOOR_MASK"][[20, repeated], 400:560].all()
    assert np.isfinite(out["DBZH_QC"][[20, repeated], 400:560]).all()
    assert not out["XQC_AVAILABLE_MASK"][[20, repeated]].any()
