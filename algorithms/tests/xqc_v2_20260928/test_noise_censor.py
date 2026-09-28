"""Detection-floor censor: SNRH below the floor is noise, not echo."""
import numpy as np
from .helpers import config, fixture, station
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig


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
