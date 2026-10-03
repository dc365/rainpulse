"""Missing shoulder telemetry cannot erase an independently held-out family."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.source_family_geometry import source_view
from rainpulse_algo.multiband.xqc_v2.source_fans import detect_complete

from .test_complete_family_integration import policy
from .test_complete_source_families import wide


def case(bearing=180, elevation=6.0, barrier="one_missing"):
    cut = wide(bearing=bearing, elevation=elevation)
    fields = {key: value.copy() for key, value in cut.fields.items()}
    blocks = (cut.range_m // 5000).astype(int)
    missing = np.isin(blocks, [3, 4, 5, 8, 11])
    # Exactly the actual first 8 dB receiver shoulder, independently measured
    # in the other original range blocks. No target or adjacent block trains.
    fields["SNRH"][(bearing + 20) % 360, missing] = np.nan
    if barrier == "both_missing":
        fields["SNRH"][(bearing - 20) % 360, missing] = np.nan
    elif barrier == "entire_missing":
        fields["SNRH"][(bearing + 20) % 360] = np.nan
    elif barrier == "interior_missing":
        fields["SNRH"][(bearing + 10) % 360, missing] = np.nan
    elif barrier == "stronger_core":
        target = blocks == 11
        fields["DBZH"][bearing, target] += 15
        fields["SNRH"][bearing, target] += 15
    return replace(cut, fields=fields), blocks == 11


def detect(cut, *, protected=None):
    cfg = policy(complete_source_family_reference_mode="relative_receiver")
    view = source_view(cut, cfg)
    mask, record = detect_complete(
        view.sweep, cfg,
        protected=np.zeros(view.sweep.shape, bool) if protected is None else protected,
    )
    return view.restore(mask), record


@pytest.mark.parametrize("bearing,elevation", [(180, 0.47), (350, 3.36), (70, 9.88)])
def test_independently_measured_original_family_predicts_missing_target_shoulder(bearing, elevation):
    cut, target = case(bearing, elevation)
    assert target.sum() >= 20
    before = {k: value.copy() for k, value in cut.fields.items()}
    mask, record = detect(cut)
    assert mask[bearing, target].mean() > 0.9
    assert record["missing_ref_is_continuity_failure"] is False
    for key, value in before.items():
        np.testing.assert_array_equal(cut.fields[key], value)


@pytest.mark.parametrize("barrier", ["both_missing", "entire_missing", "interior_missing", "stronger_core"])
def test_missing_boundary_transfer_cannot_invent_boundaries_or_predict_stronger_weather(barrier):
    cut, target = case(barrier=barrier)
    mask, _ = detect(cut)
    assert not mask[180, target].any()


def test_missing_boundary_prediction_still_honors_hard_weather():
    cut, target = case()
    protected = np.zeros(cut.fields["DBZH"].shape, bool)
    protected[180, target] = True
    mask, _ = detect(cut, protected=protected)
    assert not mask[180, target].any()


def test_constant_ref_weather_with_missing_shoulder_is_not_receiver_source():
    cut, target = case()
    fields = {k: a.copy() for k, a in cut.fields.items()}
    fields["DBZH"][np.isfinite(fields["DBZH"])] = 40
    mask, _ = detect(replace(cut, fields=fields))
    assert not mask[180, target].any()


def test_known_opposing_blocks_still_count_as_failed_reference_continuity():
    cut, target = case()
    fields = {k: a.copy() for k, a in cut.fields.items()}
    blocks = (cut.range_m // 5000).astype(int)
    # Three geometrically positive remote blocks cannot erase the negative
    # history of a measured full circle in every intervening distance block.
    known_negative = ~np.isin(blocks, [1, 6, 10, 11])
    fields["SNRH"][:, known_negative] = 40
    mask, _ = detect(replace(cut, fields=fields))
    assert not mask[180, target].any()


def test_target_and_guards_cannot_supply_missing_boundary_training():
    cut, target = case()
    fields = {k: a.copy() for k, a in cut.fields.items()}
    blocks = (cut.range_m // 5000).astype(int)
    fields["SNRH"][200, ~np.isin(blocks, [10, 11, 12])] = np.nan
    mask, _ = detect(replace(cut, fields=fields))
    assert not mask[180, target].any()
