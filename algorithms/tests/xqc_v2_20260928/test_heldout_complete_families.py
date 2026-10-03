"""Complete RAW source families predict only measured, held-out candidates."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.source_family_geometry import source_view
from rainpulse_algo.multiband.xqc_v2.source_fans import detect_complete

from .test_complete_family_integration import policy
from .test_complete_family_missing_boundaries import case, detect


def run(cut, protected=None):
    cfg = policy(complete_source_family_reference_mode="heldout_family")
    view = source_view(cut, cfg)
    mask, record = detect_complete(
        view.sweep, cfg,
        protected=np.zeros(view.sweep.shape, bool) if protected is None else protected,
    )
    return view.restore(mask), record


@pytest.mark.parametrize("barrier", ["one_missing", "both_missing", "interior_missing"])
def test_full_family_predicts_target_from_independent_raw_boundaries(barrier):
    cut, target = case(barrier=barrier)
    old, _ = detect(cut)
    if barrier != "one_missing":
        assert not old[180, target].any()
    else:
        assert old[180, target].mean() > 0.9
    new, record = run(cut)
    assert new[180, target].mean() > 0.9
    assert record["source_gates"] > 0


@pytest.mark.parametrize("barrier", ["entire_missing", "stronger_core"])
def test_no_boundary_evidence_or_unexpected_weather_still_abstains(barrier):
    cut, target = case(barrier=barrier)
    mask, _ = run(cut)
    assert not mask[180, target].any()


def test_known_full_ring_contradictions_are_not_missing_references():
    cut, target = case()
    fields = {k: a.copy() for k, a in cut.fields.items()}
    blocks = (cut.range_m // 5000).astype(int)
    fields["SNRH"][:, ~np.isin(blocks, [1, 6, 10, 11])] = 40
    mask, _ = run(replace(cut, fields=fields))
    assert not mask[180, target].any()


def test_full_family_cannot_learn_target_or_adjacent_guard_boundaries():
    cut, target = case()
    fields = {k: a.copy() for k, a in cut.fields.items()}
    blocks = (cut.range_m // 5000).astype(int)
    fields["SNRH"][200, ~np.isin(blocks, [10, 11, 12])] = np.nan
    mask, _ = run(replace(cut, fields=fields))
    assert not mask[180, target].any()


def test_constant_ref_weather_is_not_a_fitted_receiver_family():
    cut, target = case()
    fields = {k: a.copy() for k, a in cut.fields.items()}
    fields["DBZH"][np.isfinite(fields["DBZH"])] = 40
    mask, _ = run(replace(cut, fields=fields))
    assert not mask[180, target].any()


def test_native_geometry_and_hard_weather_remain_barriers():
    cut, target = case()
    protected = np.zeros(cut.fields["DBZH"].shape, bool)
    protected[180, target] = True
    mask, _ = run(cut, protected=protected)
    assert not mask[180, target].any()
    times = cut.ray_time_epoch.copy()
    times[190] += 1000
    mask, _ = run(replace(cut, ray_time_epoch=times))
    assert not mask[180, target].any()


@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_unknown_boundary_family_uses_candidate_writer_and_preserves_raw(mode):
    from rainpulse_algo.multiband.quality import x_qc

    from .helpers import fixture, station

    cut, target = case(barrier="both_missing")
    volume, _ = fixture("empty")
    volume.sweeps = [cut]
    raw = {key: a.copy() for key, a in cut.fields.items()}
    cfg = policy(mode=mode, complete_source_family_reference_mode="heldout_family")
    out = x_qc(volume, station(cfg), "b" * 64).sweeps[0].fields
    selected = out["XQC_COMPLETE_FAMILY_MASK"] == 1
    assert selected[180, target].mean() > 0.9
    assert not out["XQC_SOURCE_KIND"][180, target].any()
    assert not out["XQC_QUARANTINE_MASK"][180, target].any()
    if mode == "audit":
        assert not out["XQC_WITHHELD_MASK"][180, target].any()
    else:
        assert (out["QC_ACTION"][180, target] == 3).all()
        assert np.isnan(out["DBZH_QC"][180, target]).all()
        assert not out["REFLECTIVITY_ELIGIBLE_FOR_CR"][180, target].any()
    for key, a in raw.items():
        np.testing.assert_array_equal(cut.fields[key], a)


@pytest.mark.parametrize("bearing,elevation", [(350, 0.47), (70, 9.88)])
def test_missing_complete_objects_generalize_across_north_and_elevation(bearing, elevation):
    cut, target = case(bearing, elevation, barrier="both_missing")
    mask, _ = run(cut)
    assert mask[bearing, target].mean() > 0.9
