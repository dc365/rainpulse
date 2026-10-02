"""Full fan tracking must not manufacture dry flanks or recursive ancestry."""

from types import SimpleNamespace

import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import original_fan_shape as m


def fixture():
    shape = (16, 360)
    z = np.full(shape, np.nan, "float32")
    z[3:12, 20:340] = 25 + 7 * np.sin(np.arange(320) / 6)
    seed = np.zeros(shape, "uint32")
    cols = np.arange(20, 340)
    for row in (4, 7, 10):
        seed[row, cols[cols % 20 < 10]] = row + 100
    parent = np.zeros(shape, "uint32")
    parent[3:12, 20:340] = 17
    native = SimpleNamespace(
        shape=shape,
        ranges=np.arange(360) * 1000.0,
        azimuth=np.arange(16) * 1.0,
        geometry_good=np.ones(16, bool),
        gap_after=np.zeros(16, bool),
        fields={"DBZH": z, "SNR": np.full(shape, 8.0, "float32")},
        field_available={"DBZH": np.isfinite(z), "SNR": np.ones(shape, bool)},
    )
    return (
        native,
        np.zeros(shape, bool),
        {"RV2_SOURCE_LEDGER_SEED_ID": seed, "RV2_RAW_FAN_ID": parent},
    )


def test_full_original_fan_includes_edge_and_sparse_members_without_power_fit():
    n, b, g = fixture()
    # Sparse weak target row, still a full original fan measured in held-out
    # windows. Local points are neither new anchors nor training references.
    n.fields["DBZH"][5, 140:160] = np.nan
    n.fields["DBZH"][5, [142, 147, 153, 158]] = 15
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    raw = n.fields["DBZH"].copy()
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, 155] == 1
    assert out[m.PREFIX + "QUALIFIED_MASK"][5, 153] == 1
    assert not out[m.PREFIX + "QUALIFIED_MASK"][:, :20].any()
    assert not out[m.PREFIX + "QUALIFIED_MASK"][:, 340:].any()
    for record in report["records"]:
        assert all(abs(v - record["target_block"]) > 1 for v in record["reference_blocks"])
    assert np.array_equal(raw, n.fields["DBZH"], equal_nan=True)
    m.validate(out, n, b, g)


@pytest.mark.parametrize(
    "case",
    [
        "broad",
        "unanchored",
        "unknown",
        "gap",
        "barrier",
        "weather",
        "target_only",
        "unrelated_parent",
    ],
)
@pytest.mark.parametrize("sparse", [False, True])
def test_full_fan_guards_keep_weather_missing_and_unanchored_returns(case, sparse):
    n, b, g = fixture()
    if case == "broad":
        n.fields["DBZH"][:] = n.fields["DBZH"][6]
        n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    elif case == "unanchored":
        g["RV2_SOURCE_LEDGER_SEED_ID"][:] = 0
    elif case == "unknown":
        n.field_available["SNR"][[2, 12]] = False
    elif case == "gap":
        n.gap_after[6] = True
    elif case == "barrier":
        b[3, 155] = True
    elif case == "weather":
        n.fields["RHOHV"] = np.full(n.shape, 0.99, "float32")
        n.field_available["RHOHV"] = np.ones(n.shape, bool)
        n.fields["SNR"][:] = 20
    elif case == "target_only":
        n.fields["DBZH"][:, :140] = np.nan
        n.fields["DBZH"][:, 160:] = np.nan
        n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    else:
        g["RV2_RAW_FAN_ID"][3] = 99
    out, _ = m.qualify(n, b, g, sparse_target_enabled=sparse)
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, 155] == 0


def test_saved_proof_tampering_cannot_authorize_a_new_member():
    n, b, g = fixture()
    out, _ = m.qualify(n, b, g)
    out[m.PREFIX + "QUALIFIED_MASK"][0, 155] = 1
    with pytest.raises(ValueError, match="replay differs"):
        m.validate(out, n, b, g)


def test_local_weather_is_neither_target_nor_reference_and_does_not_blank_whole_fan():
    n, b, g = fixture()
    n.fields["RHOHV"] = np.full(n.shape, np.nan, "float32")
    n.field_available["RHOHV"] = np.zeros(n.shape, bool)
    for col in (60, 155, 240):
        n.fields["RHOHV"][6, col] = 0.99
        n.field_available["RHOHV"][6, col] = True
        n.fields["SNR"][6, col] = 20
    out, report = m.qualify(n, b, g)
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, 153] == 1
    assert not out[m.PREFIX + "QUALIFIED_MASK"][6, [60, 155, 240]].any()
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, 155] == 1
    refs = out[m.PREFIX + "SOURCE_REFERENCE_MASK"].astype(bool)
    assert not refs[6, [60, 155, 240]].any()
    for record in report["records"]:
        for ref in record["source_reference_gates"].values():
            assert ref["gate_count"] > 0 and len(ref["columns_sha256"]) == 64


def test_weather_on_a_shoulder_cannot_be_deleted_to_manufacture_a_dry_edge():
    n, b, g = fixture()
    n.fields["DBZH"][2, 20:340] = 18
    n.fields["RHOHV"] = np.full(n.shape, np.nan, "float32")
    n.field_available["RHOHV"] = np.zeros(n.shape, bool)
    n.fields["RHOHV"][2, 20:340] = 0.99
    n.field_available["RHOHV"][2, 20:340] = True
    n.fields["SNR"][2, 20:340] = 20
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    out, _ = m.qualify(n, b, g)
    assert not out[m.PREFIX + "QUALIFIED_MASK"].any()


def test_original_to_target_bridge_cannot_cross_a_weather_island():
    n, b, g = fixture()
    b[6, 155] = True
    out, _ = m.qualify(n, b, g)
    # Row5 ties original4/7: nearest4 remains valid. Row6 is retained; no
    # accepted row5 can be promoted to a source to cross the island at6.
    assert out[m.PREFIX + "QUALIFIED_MASK"][5, 155] == 1
    assert out[m.PREFIX + "QUALIFIED_MASK"][6, 155] == 0
    assert out[m.PREFIX + "SOURCE_ID"][5, 155] == 104


def test_protected_original_seed_is_not_in_source_reference_proof():
    n, b, g = fixture()
    b[4, 60] = True
    out, _ = m.qualify(n, b, g)
    refs = out[m.PREFIX + "SOURCE_REFERENCE_MASK"].astype(bool)
    assert not (refs & b).any()
    assert not refs[4, 60]
    assert refs.any()


def test_one_original_identity_can_have_separate_native_ray_references():
    n, b, g = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][g["RV2_SOURCE_LEDGER_SEED_ID"] > 0] = 104
    b[6, 155] = True
    out, report = m.qualify(n, b, g)
    # Keeping only the last ray for ID104 would falsely trace row3 through6.
    assert out[m.PREFIX + "QUALIFIED_MASK"][3, 155]
    record = next(v for v in report["records"] if v["target_block"] == 7)
    assert {v["row"] for v in record["source_reference_gates"].values()} == {4, 7, 10}


@pytest.mark.parametrize("side_case", ["measured", "unknown", "foreground"])
def test_sparse_target_window_uses_heldout_original_shape_without_inventing_dry_sides(side_case):
    n, b, g = fixture()
    # The target window no longer contains a dense fan. Two short weak pieces
    # remain within the same immutable original parent/range. Other distances
    # establish the object; the target cannot train the angular template.
    n.fields["DBZH"][3:12, 140:160] = np.nan
    g["RV2_SOURCE_LEDGER_SEED_ID"][:, 140:160] = 0
    n.fields["DBZH"][3, [146, 147, 153, 154]] = 15
    if side_case == "unknown":
        n.fields["SNR"][[2, 12], 140:160] = np.nan
        n.field_available["SNR"] = np.isfinite(n.fields["SNR"])
    elif side_case == "foreground":
        n.fields["DBZH"][[2, 12], 140:160] = 20
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    raw = n.fields["DBZH"].copy()
    original = g["RV2_SOURCE_LEDGER_SEED_ID"].copy()
    out, report = m.qualify(n, b, g, sparse_target_enabled=True)
    if side_case == "measured":
        assert out[m.PREFIX + "QUALIFIED_MASK"][3, [146, 147, 153, 154]].all()
        assert not out[m.PREFIX + "SOURCE_REFERENCE_MASK"][:, 140:160].any()
        records = [v for v in report["records"] if v["target_block"] == 7]
        assert records
        assert all(abs(v - 7) > 1 for rec in records for v in rec["reference_blocks"])
        m.validate(out, n, b, g, sparse_target_enabled=True)
    else:
        assert not out[m.PREFIX + "QUALIFIED_MASK"][:, 140:160].any()
    assert np.array_equal(original, g["RV2_SOURCE_LEDGER_SEED_ID"])
    assert np.array_equal(raw, n.fields["DBZH"], equal_nan=True)
