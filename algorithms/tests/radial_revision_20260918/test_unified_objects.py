"""Object decisions retain local uncertainty without discarding whole history."""

import numpy as np
import pytest

from .conftest import Native, load
from .test_variable_morphology import fixture


def run(native, **kwargs):
    return load("radial_revision.unified_objects").evaluate(
        native, np.zeros(native.shape, bool), **kwargs
    )


@pytest.mark.parametrize("spacing", [0.5, 1.0])
def test_original_variable_object_needs_no_previous_source(spacing):
    native = fixture(spacing=spacing)
    raw = native.fields["DBZH"].copy()
    arrays, report = run(native)
    assert arrays["RV2_UNIFIED_PROPOSAL_MASK"].any()
    assert report["action_gates"] == 0
    assert not arrays["RV2_UNIFIED_ACTION_MASK"].any()
    assert np.array_equal(raw, native.fields["DBZH"], equal_nan=True)
    rotated, _ = run(fixture(rotation=53.0, spacing=spacing))
    assert np.array_equal(arrays["RV2_UNIFIED_PROPOSAL_MASK"], rotated["RV2_UNIFIED_PROPOSAL_MASK"])


def test_local_contaminated_window_cannot_veto_other_measured_segments():
    native = fixture()
    cols = (native.ranges >= 200000) & (native.ranges < 210000)
    # Measured surrounding rain obscures the source boundary locally.
    native.fields["DBZH"][:, cols] = 24.0
    native.field_available["DBZH"] = np.isfinite(native.fields["DBZH"])
    native.fields["SNR"][:, cols] = 8.0
    arrays, _ = run(native)
    hit = arrays["RV2_UNIFIED_PROPOSAL_MASK"] == 1
    assert hit[:, native.ranges < 180000].any()
    assert hit[:, native.ranges >= 250000].any()
    assert not hit[:, cols].any()


def test_unknown_window_retains_targets_but_known_windows_can_qualify():
    native = fixture()
    cols = (native.ranges >= 200000) & (native.ranges < 210000)
    native.fields["SNR"][:, cols] = np.nan
    native.field_available["SNR"][:, cols] = False
    arrays, _ = run(native)
    hit = arrays["RV2_UNIFIED_PROPOSAL_MASK"] == 1
    assert hit.any() and not hit[:, cols].any()


@pytest.mark.parametrize("kind", ["constant_km", "curved"])
def test_full_history_weather_counterexamples_remain(kind):
    arrays, _ = run(fixture(kind))
    assert not arrays["RV2_UNIFIED_PROPOSAL_MASK"].any()


def test_quarantine_changes_only_eligible_observed_members_and_replays():
    native = fixture()
    module = load("radial_revision.unified_objects")
    arrays, report = run(native, mode="quarantine")
    hit = arrays["RV2_UNIFIED_PROPOSAL_MASK"] == 1
    assert np.array_equal(hit, arrays["RV2_UNIFIED_ACTION_MASK"] == 1)
    assert report["action_gates"] == int(hit.sum())
    assert not hit[~native.field_available["DBZH"]].any()
    module.validate(arrays, native, np.zeros(native.shape, bool), mode="quarantine")
    arrays["RV2_UNIFIED_PROPOSAL_MASK"][0, 0] = 1
    with pytest.raises(ValueError, match="proof"):
        module.validate(arrays, native, np.zeros(native.shape, bool), mode="quarantine")


def test_positive_weather_and_external_protection_always_retain():
    native = fixture()
    native.fields["RHOHV"] = np.full(native.shape, 0.99, "float32")
    native.field_available["RHOHV"] = native.field_available["DBZH"].copy()
    native.fields["SNR"][native.field_available["DBZH"]] = 20.0
    arrays, _ = run(native, mode="quarantine")
    assert not arrays["RV2_UNIFIED_ACTION_MASK"].any()
    native = fixture()
    module = load("radial_revision.unified_objects")
    blocked = np.zeros(native.shape, bool)
    blocked[20:23, 200:220] = True
    arrays, _ = module.evaluate(native, blocked, mode="quarantine")
    assert not arrays["RV2_UNIFIED_ACTION_MASK"][blocked].any()


def test_fork_and_resource_limit_never_promote_partial_result():
    native = fixture()
    middle = np.abs(native.azimuth - 180) < 2
    cols = native.ranges >= 240000
    native.fields["DBZH"][np.ix_(middle, cols)] = np.nan
    native.field_available["DBZH"] = np.isfinite(native.fields["DBZH"])
    native.fields["SNR"][np.ix_(middle, cols)] = -2.0
    arrays, _ = run(native)
    assert not arrays["RV2_UNIFIED_PROPOSAL_MASK"].any()
    with pytest.raises(load("radial_revision.geometry").ResourceLimit):
        run(fixture(), maximum_objects=1)


def disconnected():
    ranges = np.arange(0.0, 420000.0, 500.0)
    z = np.full((41, len(ranges)), np.nan, "float32")
    for start in (60000.0, 140000.0, 220000.0, 300000.0, 380000.0):
        cols = (ranges >= start) & (ranges < start + 15000.0)
        z[20:22, cols] = 18.0
    return Native(z, dr=500.0, start=0.0, fields={"SNR": np.where(np.isfinite(z), 8.0, -2.0)})


def test_global_original_projection_recovers_disconnected_unanchored_segments():
    native = disconnected()
    prior, _ = run(native, projection_enabled=False)
    assert not prior["RV2_UNIFIED_PROPOSAL_MASK"].any()
    current, report = run(native)
    assert current["RV2_UNIFIED_PROPOSAL_MASK"][native.field_available["DBZH"]].all()
    assert not current["RV2_UNIFIED_CANDIDATE_MASK"][~native.field_available["DBZH"]].any()
    assert any(o["nomination_kind"] == "projected" and o["confirmed"] for o in report["objects"])


def test_global_projection_requires_multiple_measured_flanks_and_stable_boundaries():
    native = disconnected()
    # A geometrical alignment does not qualify when surrounding observations
    # are missing, or when independent original boundaries keep drifting.
    native.fields["SNR"][:] = np.nan
    native.field_available["SNR"][:] = False
    arrays, _ = run(native)
    assert not arrays["RV2_UNIFIED_PROPOSAL_MASK"].any()


def test_local_weather_shoulders_cannot_borrow_bright_object_mean():
    native = fixture()
    col = int(np.flatnonzero(native.ranges == 200000.0)[0])
    rows = np.flatnonzero(native.field_available["DBZH"][:, col])
    native.fields["DBZH"][rows, col] = 10.0
    for side in (rows[0] - 1, rows[-1] + 1):
        native.fields["DBZH"][side, col] = 9.0
        native.field_available["DBZH"][side, col] = True
    arrays, _ = run(native)
    assert arrays["RV2_UNIFIED_PROPOSAL_MASK"].any()
    assert not arrays["RV2_UNIFIED_PROPOSAL_MASK"][rows, col].any()


def test_short_near_line_requires_dense_support_and_strong_geometry():
    ranges = np.arange(0.0, 100000.0, 500.0)
    z = np.full((21, len(ranges)), np.nan, "float32")
    z[10, (ranges >= 20000) & (ranges < 50000)] = 18.0
    native = Native(z, dr=500.0, start=0.0, fields={"SNR": np.where(np.isfinite(z), 8.0, -2.0)})
    arrays, _ = run(native)
    assert arrays["RV2_UNIFIED_PROPOSAL_MASK"][native.field_available["DBZH"]].all()


def test_projection_cannot_join_same_angles_across_native_sector_gap():
    model = load("radial_revision.object_model")
    projected = load("radial_revision.projected_objects")
    ranges = np.arange(0.0, 420000.0, 500.0)
    objects = []
    for identity, start in enumerate(
        (60000, 70000, 80000, 90000, 250000, 260000, 270000, 280000), 1
    ):
        segment = 0 if identity <= 4 else 21
        row = 10 if segment == 0 else 31
        cols = np.flatnonzero((ranges >= start) & (ranges < start + 5000))
        window = model.MeasuredWindow(
            block=start // 5000,
            start_m=float(start),
            end_m=float(start + 5000),
            left_deg=9.5,
            right_deg=10.5,
            left_row=row - 1,
            right_row=row + 1,
            members=tuple(map(int, row * len(ranges) + cols)),
            known_fraction=1.0,
            contrast_fraction=1.0,
            anchor_support_m=5000.0,
        )
        objects.append(
            model.RawObject(
                identity=identity,
                kind="line",
                scale_m=5000.0,
                level_dbz=10.0,
                start_m=float(start),
                end_m=float(start + 5000),
                support_m=5000.0,
                windows=(window,),
                history_holds=("insufficient_object_geometry",),
                native_segment_start=segment,
            )
        )
    assert not projected.nominate(objects, 1.0, ranges, 500.0)
