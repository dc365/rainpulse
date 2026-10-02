"""Anchored radial morphology does not require a constant receiver response."""

from types import SimpleNamespace

import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import (
    anchored_radial_shape as m,
)


def fixture():
    shape = (5, 340)
    z = np.full(shape, np.nan, "float32")
    z[2, 20:320] = 25 + 8 * np.sin(np.arange(300) / 7)
    seed = np.zeros(shape, "uint32")
    columns = np.arange(20, 320)
    seed[2, columns[columns % 20 < 10]] = 7
    native = SimpleNamespace(
        shape=shape,
        ranges=np.arange(340) * 1000.0,
        azimuth=np.arange(5) * 1.0,
        geometry_good=np.ones(5, bool),
        gap_after=np.zeros(5, bool),
        fields={"DBZH": z, "SNR": np.full(shape, 8, "float32")},
        field_available={"DBZH": np.isfinite(z), "SNR": np.ones(shape, bool)},
    )
    return (
        native,
        np.zeros(shape, bool),
        {
            "RV2_SOURCE_LEDGER_SEED_ID": seed,
            "RV2_RAW_FAN_ID": np.zeros(shape, "uint32"),
        },
    )


def test_same_ray_radial_shape_with_original_source_can_qualify():
    n, b, g = fixture()
    raw = n.fields["DBZH"].copy()
    seed = g["RV2_SOURCE_LEDGER_SEED_ID"].copy()
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155] == 1
    assert not out[m.PREFIX + "QUALIFIED_MASK"][[0, 1, 3, 4]].any()
    assert not out[m.PREFIX + "QUALIFIED_MASK"][:, 320:].any()
    assert np.array_equal(raw, n.fields["DBZH"], equal_nan=True)
    assert np.array_equal(seed, g["RV2_SOURCE_LEDGER_SEED_ID"])
    for record in report["records"]:
        assert all(abs(v - record["target_block"]) > 1 for v in record["shape_reference_blocks"])
        assert all(abs(v - record["target_block"]) > 1 for v in record["original_reference_blocks"])
    m.validate(out, n, b, g)


@pytest.mark.parametrize("case", ["unanchored", "broad_weather", "unknown_sides", "gap"])
def test_radial_coordinates_alone_do_not_qualify(case):
    n, b, g = fixture()
    if case == "unanchored":
        g["RV2_SOURCE_LEDGER_SEED_ID"][:] = 0
    elif case == "broad_weather":
        n.fields["DBZH"][1:4] = n.fields["DBZH"][2]
        n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    elif case == "unknown_sides":
        n.field_available["SNR"][[1, 3]] = False
    else:
        n.gap_after[1] = True
    out, _ = m.qualify(n, b, g)
    assert not out[m.PREFIX + "QUALIFIED_MASK"].any()


def test_weather_and_barriers_and_altered_proof_are_retained():
    n, b, g = fixture()
    n.fields["RHOHV"] = np.full(n.shape, np.nan, "float32")
    n.field_available["RHOHV"] = np.zeros(n.shape, bool)
    n.fields["RHOHV"][2, 155] = 0.99
    n.fields["SNR"][2, 155] = 20
    n.field_available["RHOHV"][2, 155] = True
    b[2, 156] = True
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155] == 0
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 156] == 0
    for record in report["records"]:
        assert record["end_m"] <= 156000 or record["start_m"] > 156000
    out[m.PREFIX + "QUALIFIED_MASK"][2, 156] = 1
    with pytest.raises(ValueError, match="replay differs"):
        m.validate(out, n, b, g)


def test_competing_original_sources_cannot_select_a_convenient_identity():
    n, b, g = fixture()
    seed = g["RV2_SOURCE_LEDGER_SEED_ID"]
    columns = np.arange(20, 320)
    seed[2, columns[columns % 20 < 5]] = 8
    out, _ = m.qualify(n, b, g)
    assert out[m.PREFIX + "AMBIGUOUS_MASK"][2, 155] == 1
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155] == 0
    assert out[m.PREFIX + "SOURCE_ID"][2, 155] == 0


def test_a_local_radial_fragment_uses_original_support_not_other_weak_tails():
    n, b, g = fixture()
    z = n.fields["DBZH"]
    keep = g["RV2_SOURCE_LEDGER_SEED_ID"] > 0
    keep[2, 150:159] = True
    z[~keep] = np.nan
    n.field_available["DBZH"] = np.isfinite(z)
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155] == 1
    record = next(v for v in report["records"] if v["target_block"] == 7)
    assert record["shape_mode"] == "anchored_local_radial_fragment"
    assert not record["shape_reference_blocks"]


def neighbouring_fixture():
    n, b, g = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][2, 20:320] = 7
    g["RV2_RAW_FAN_ID"][2, 20:320] = 11
    n.fields["DBZH"][3, 150:159] = 15
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    g["RV2_RAW_FAN_ID"][3, 150:159] = 11
    return n, b, g


def test_neighbouring_beam_retains_original_parent_and_source_range():
    n, b, g = neighbouring_fixture()
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, 155] == 1
    assert out[m.PREFIX + "SOURCE_ID"][3, 155] == 7
    record = next(v for v in report["records"] if v["row"] == 3)
    assert record["source_row"] == 2
    assert record["original_parent_ids"] == [11]
    m.validate(out, n, b, g)


@pytest.mark.parametrize(
    "case", ["different_parent", "gap", "barrier", "outside_range", "too_far", "weather"]
)
def test_neighbour_association_cannot_cross_frozen_object_guards(case):
    n, b, g = neighbouring_fixture()
    if case == "different_parent":
        g["RV2_RAW_FAN_ID"][3] = 12
    elif case == "gap":
        n.gap_after[2] = True
    elif case == "barrier":
        b[2, [145, 185]] = True
    elif case == "outside_range":
        n.fields["DBZH"][3, 330:339] = 15
        n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
        g["RV2_RAW_FAN_ID"][3, 330:339] = 11
    elif case == "too_far":
        n.azimuth[2] = -0.5
    else:
        n.fields["RHOHV"] = np.full(n.shape, 0.99, "float32")
        n.field_available["RHOHV"] = np.ones(n.shape, bool)
        n.fields["SNR"][3] = 20
    out, _ = m.qualify(n, b, g)
    target = 335 if case == "outside_range" else 155
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, target] == 0


def test_new_members_never_become_sources_for_farther_rows():
    n, b, g = neighbouring_fixture()
    # Seven native rays allow a target three beams from original row 2.
    for name in n.fields:
        n.fields[name] = np.pad(n.fields[name], ((0, 2), (0, 0)), constant_values=np.nan)
        n.field_available[name] = np.pad(n.field_available[name], ((0, 2), (0, 0)))
    for name in g:
        g[name] = np.pad(g[name], ((0, 2), (0, 0)))
    n.shape = (7, 340)
    n.azimuth = np.arange(7) * 1.0
    n.geometry_good = np.ones(7, bool)
    n.gap_after = np.zeros(7, bool)
    b = np.zeros(n.shape, bool)
    n.fields["DBZH"][5, 150:159] = 15
    n.fields["SNR"][4:] = 8
    n.field_available["SNR"][4:] = True
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    g["RV2_RAW_FAN_ID"][5, 150:159] = 11
    out, _ = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, 155] == 1
    assert out[m.PREFIX + "QUALIFIED_MASK"][5, 155] == 0
    assert not g["RV2_SOURCE_LEDGER_SEED_ID"][3:].any()


def test_failed_nearest_source_cannot_fall_back_to_farther_source():
    n, b, g = neighbouring_fixture()
    # A nearer original identity is too short to qualify; ancestry selection
    # must nevertheless keep it rather than falling back to row 2's long fit.
    g["RV2_SOURCE_LEDGER_SEED_ID"][3, 40:43] = 9
    g["RV2_RAW_FAN_ID"][3, 40:43] = 11
    n.fields["DBZH"][3, 40:43] = 20
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    out, _ = m.qualify(n, b, g)
    assert not out[m.PREFIX + "QUALIFIED_MASK"][3].any()


def band_fixture():
    n, b, g = fixture()
    n.fields["DBZH"][3] = n.fields["DBZH"][2]
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    g["RV2_SOURCE_LEDGER_SEED_ID"][3] = g["RV2_SOURCE_LEDGER_SEED_ID"][2]
    g["RV2_SOURCE_LEDGER_SEED_ID"][3, g["RV2_SOURCE_LEDGER_SEED_ID"][3] > 0] = 8
    g["RV2_RAW_FAN_ID"][2:4, 20:320] = 11
    return n, b, g


def test_multi_ray_residue_is_measured_against_outside_band_shoulders():
    n, b, g = band_fixture()
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2:4, 155].all()
    assert all(v["frozen_band_rows"] == [2, 4] for v in report["records"])
    assert all(set(v["band_reference_ids"]) == {2, 3} for v in report["records"])
    m.validate(out, n, b, g)


@pytest.mark.parametrize(
    "case",
    [
        "no_original_side_source",
        "different_parent",
        "unknown_outer_side",
        "protected_outer_side",
        "broad_foreground",
        "target_only_source",
    ],
)
def test_band_membership_cannot_be_inferred_from_new_weak_members(case):
    n, b, g = band_fixture()
    if case == "no_original_side_source":
        g["RV2_SOURCE_LEDGER_SEED_ID"][3] = 0
    elif case == "different_parent":
        g["RV2_RAW_FAN_ID"][3] = 12
    elif case == "unknown_outer_side":
        n.field_available["SNR"][4] = False
    elif case == "protected_outer_side":
        b[4, [145, 185]] = True
    elif case == "broad_foreground":
        n.fields["DBZH"][1] = n.fields["DBZH"][2]
        n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    else:
        g["RV2_SOURCE_LEDGER_SEED_ID"][3] = 0
        g["RV2_SOURCE_LEDGER_SEED_ID"][3, 140:159] = 8
    out, _ = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155] == 0


def test_local_parent_tile_recovers_complete_ids_before_band_support():
    n, b, g = band_fixture()
    g["RV2_RAW_FAN_ID"][:] = 0
    # Only one distance-local RAW tile carries the parent clue. The frozen
    # source identities retain their complete original 300 km range.
    g["RV2_RAW_FAN_ID"][2:4, 140:160] = 11
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2:4, 155].all()
    assert all(v["frozen_band_rows"] == [2, 4] for v in report["records"])
    assert any(v["qualified_gates"] > 0 for v in report["records"])


def test_cleared_strong_band_member_is_not_an_artificial_shape_hole():
    n, b, g = band_fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][3, 20:320] = 8
    out, _ = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155] == 1
    assert not out[m.PREFIX + "QUALIFIED_MASK"][3].any()


@pytest.mark.parametrize("offset", [-12.0, 12.0])
def test_anchored_native_contrast_recovers_stripe_inside_observed_background(offset):
    n, b, g = fixture()
    z = n.fields["DBZH"]
    z[1] = z[2] - offset
    z[3] = z[2] - offset
    n.field_available["DBZH"] = np.isfinite(z)
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155] == 1
    assert any(v["shape_mode"] == "heldout_native_transverse_contrast" for v in report["records"])


def test_target_only_angular_contrast_cannot_train_its_own_radial_template():
    n, b, g = fixture()
    n.fields["DBZH"][1] = n.fields["DBZH"][2]
    n.fields["DBZH"][3] = n.fields["DBZH"][2]
    n.fields["DBZH"][1, 140:160] -= 12
    n.fields["DBZH"][3, 140:160] -= 12
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    out, _ = m.qualify(n, b, g)
    assert not out[m.PREFIX + "QUALIFIED_MASK"][2, 155]


def test_radial_contrast_magnitude_may_vary_without_becoming_constant_power_fit():
    n, b, g = fixture()
    offset = 14 + 6 * np.sin(np.arange(n.shape[1]) / 40)
    n.fields["DBZH"][1] = n.fields["DBZH"][2] - offset
    n.fields["DBZH"][3] = n.fields["DBZH"][2] - offset
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    out, _ = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][2, 155]


@pytest.mark.parametrize(
    "case", ["linear_background", "alternating_polarity", "weather", "no_source"]
)
def test_coherent_stripe_rule_retains_weather_and_incoherent_angular_background(case):
    n, b, g = fixture()
    offset = np.full(n.shape[1], 12.0)
    if case == "alternating_polarity":
        offset *= np.where((n.ranges // 20000).astype(int) % 2, 1, -1)
    n.fields["DBZH"][1] = n.fields["DBZH"][2] - offset
    n.fields["DBZH"][3] = n.fields["DBZH"][2] - offset
    if case == "linear_background":
        n.fields["DBZH"][3] = n.fields["DBZH"][2] + offset
    elif case == "weather":
        n.fields["RHOHV"] = np.full(n.shape, 0.99, "float32")
        n.field_available["RHOHV"] = np.ones(n.shape, bool)
        n.fields["SNR"][2] = 20
    elif case == "no_source":
        g["RV2_SOURCE_LEDGER_SEED_ID"][:] = 0
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    out, _ = m.qualify(n, b, g)
    assert not out[m.PREFIX + "QUALIFIED_MASK"].any()
