"""Sparse whole carriers cannot become weather merely through contour breaks."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.polar_morphology import MorphologyPolicy, detect
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit, Sweep


def policy():
    return MorphologyPolicy(
        version="x-polar-morphology-20261002-v9",
        expanding_fans_enabled=True,
        anchored_fans_enabled=True,
        pulsing_fans_enabled=True,
        grouped_envelopes_enabled=True,
        branching_envelopes_enabled=True,
        compact_counterexamples_enabled=True,
        transverse_counterexamples_enabled=True,
        centered_pulsing_fans_enabled=True,
    )


def fragmented(bearing=53.0, elevation=0.5):
    az = np.arange(360.0)
    r = np.arange(250.0, 275000.1, 250.0)
    corridor = (abs((az[:, None] - bearing + 180) % 360 - 180) <= 1.5) & (
        (r[None, :] >= 150000) & (r[None, :] <= 270000)
    )
    body = corridor & (((r[None, :] - 150000) % 17000) < 15000)
    z = np.zeros(body.shape, "float32")
    z[body] = 20
    sn = np.full(body.shape, -2.0, "float32")
    sn[corridor] = 15
    s = Sweep(
        "complete_original",
        az,
        np.full(len(az), elevation),
        r,
        {"DBZH": z, "SNR": sn},
        {"DBZH": np.ones(body.shape, bool), "SNR": np.ones(body.shape, bool)},
        np.ones(len(az), bool),
        np.zeros(len(az), bool),
        np.arange(len(az)) * 0.1,
    )
    return s, body


@pytest.mark.parametrize("bearing,elevation", [(53.0, 0.5), (178.0, 3.36), (359.0, 8.0)])
def test_exact_whole_membership_preserves_parent_and_contour_diagnostics(bearing, elevation):
    s, body = fragmented(bearing, elevation)
    before = s.digest
    parent = detect(s, policy())
    exported = detect(s, policy(), collect_carriers=True)
    whole = np.zeros(s.shape, bool)
    for indices in exported.carriers:
        assert not indices.flags.writeable
        whole.flat[indices] = True
    assert (parent.counterexample_mask & body).sum() > 1200  # positive defect oracle
    assert (parent.mask & body).sum() < 20
    assert (whole & body).sum() >= 1272
    assert not whole[~body].any()
    np.testing.assert_array_equal(parent.mask, exported.mask)
    np.testing.assert_array_equal(parent.object_id, exported.object_id)
    np.testing.assert_array_equal(parent.counterexample_mask, exported.counterexample_mask)
    assert s.digest == before


def test_local_excess_missing_and_explicit_weather_remain():
    from rainpulse_algo.multiband.xqc_v2.fragmented_carriers import review

    s, body = fragmented()
    fields = {k: v.copy() for k, v in s.fields.items()}
    core = body & (s.ranges[None, :] >= 190000) & (s.ranges[None, :] < 195000)
    fields["SNR"][core] += 15
    available = {k: v.copy() for k, v in s.available.items()}
    missing = body & (s.ranges[None, :] >= 225000) & (s.ranges[None, :] < 230000)
    available["SNR"][missing] = False
    s = replace(s, fields=fields, available=available)
    protected = body & (s.ranges[None, :] >= 250000) & (s.ranges[None, :] < 255000)
    out = review(s, policy(), protected=protected)
    assert out.candidate.sum() > 500
    assert not out.candidate[core | missing | protected | ~body].any()
    assert out.local_excess[core].any() and out.unknown[missing].any()


def test_localized_compact_weather_and_narrowing_history_never_inherit():
    from rainpulse_algo.multiband.xqc_v2.fragmented_carriers import review

    from .test_polar_morphology import scene

    for kind in ("blob", "fixed_km", "curved"):
        s, body = scene(kind)
        assert body.any() and not review(s, policy()).candidate.any()


def test_bounded_review_is_atomic_and_does_not_mutate_raw():
    from rainpulse_algo.multiband.xqc_v2.fragmented_carriers import review

    s, body = fragmented()
    before = s.digest
    result = review(s, policy())
    assert (result.candidate & body).sum() > 1200
    with pytest.raises(ResourceLimit):
        review(s, policy().model_copy(update={"maximum_work": 1}))
    assert s.digest == before


def test_unknown_shoulders_and_native_angular_barriers_prevent_override():
    from rainpulse_algo.multiband.xqc_v2.fragmented_carriers import review

    s, body = fragmented()
    availability = {k: v.copy() for k, v in s.available.items()}
    for field in availability:
        availability[field][~body] = False
    assert not review(replace(s, available=availability), policy()).candidate.any()
    gaps = s.gap_after.copy()
    gaps[53] = True
    assert not review(replace(s, gap_after=gaps), policy()).candidate.any()


def test_override_is_exact_compact_membership_and_heldout_work_is_bounded():
    from rainpulse_algo.multiband.xqc_v2.fragmented_carriers import review

    s, _ = fragmented()
    exported = detect(s, policy(), collect_carriers=True)
    result = review(s, policy())
    whole = np.zeros(s.shape, bool)
    for indices in exported.carriers:
        whole.flat[indices] = True
    assert result.candidate.any()
    assert not result.candidate[~whole | ~exported.counterexample_mask].any()
    assert result.record["work"] > exported.record["work"]
    with pytest.raises(ResourceLimit, match="held-out"):
        review(s, policy().model_copy(update={"maximum_work": exported.record["work"]}))


@pytest.mark.parametrize("bearing,elevation", [(53.0, 0.5), (178.0, 3.36), (359.0, 8.0)])
def test_smooth_receiver_drift_is_not_local_weather_excess(bearing, elevation):
    from rainpulse_algo.multiband.xqc_v2.fragmented_carriers import review

    s, body = fragmented(bearing, elevation)
    fields = {k: v.copy() for k, v in s.fields.items()}
    # A known synthetic carrier with gradual drift, not a fixed power template.
    fields["SNR"][body] = np.broadcast_to(
        15.0 + (s.ranges[None, :] - 150000.0) / 25000.0, s.shape
    )[body]
    s = replace(s, fields=fields)
    before = s.digest
    result = review(s, policy())
    assert (result.candidate & body).sum() > 1200
    assert not result.local_excess[body].any()
    assert s.digest == before


def test_stationary_native_variability_is_not_local_weather_excess():
    from rainpulse_algo.multiband.xqc_v2.fragmented_carriers import review

    s, body = fragmented()
    fields = {k: v.copy() for k, v in s.fields.items()}
    variation = np.broadcast_to(np.arange(s.shape[1])[None, :] % 5 - 2, s.shape)
    fields["SNR"][body] += variation[body]
    s = replace(s, fields=fields)
    result = review(s, policy())
    assert (result.candidate & body).sum() > 1200
    assert not result.local_excess[body].any()
