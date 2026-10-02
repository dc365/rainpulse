"""A local RAW tile must not truncate the independently frozen source ledger."""

import numpy as np
import pytest

from .conftest import Native, load

P = "RV2_SOURCE_FOOTPRINT_"


def fixture():
    n = Native(np.full((9, 300), 20.0))
    parent = np.zeros(n.shape, "uint32")
    parent[2:7, 120:180] = 1
    seed = np.zeros(n.shape, "uint32")
    for row in (3, 4, 5):
        seed[row, 10:290] = row + 10
    seed[4, 140:160] = 0
    return n, {"RV2_RAW_FAN_ID": parent, "RV2_SOURCE_LEDGER_SEED_ID": seed}, np.zeros(n.shape, bool)


def run(n, g, b):
    return load("radial_revision.source_footprint").qualify(n, b, g)


def test_complete_original_ids_recover_references_outside_local_tile():
    n, g, b = fixture()
    out, _ = run(n, g, b)
    assert out[P + "QUALIFIED_MASK"][4, 140:160].all()
    assert (out[P + "REFERENCE_BLOCKS"][4, 140:160] >= 3).all()
    assert not out[P + "QUALIFIED_MASK"][[2, 6]].any()
    assert not out[P + "QUALIFIED_MASK"][:, :120].any()
    assert not out[P + "QUALIFIED_MASK"][:, 180:].any()


def test_unrelated_original_ids_cannot_supply_far_reference_windows():
    n, g, b = fixture()
    for section in (slice(None, 120), slice(180, None)):
        ids = g["RV2_SOURCE_LEDGER_SEED_ID"][:, section]
        ids[ids > 0] += 100
    out, _ = run(n, g, b)
    assert not out[P + "QUALIFIED_MASK"][4, 140:160].any()


def test_protected_range_break_still_blocks_full_source_references():
    n, g, b = fixture()
    b[3:6, 119] = True
    b[3:6, 181] = True
    out, _ = run(n, g, b)
    assert not out[P + "QUALIFIED_MASK"][4, 140:160].any()


def test_distant_gates_with_same_id_cannot_expand_original_angular_rows():
    n, g, b = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][2, 10:120] = 13
    g["RV2_SOURCE_LEDGER_SEED_ID"][6, 180:290] = 15
    out, _ = run(n, g, b)
    assert out[P + "QUALIFIED_MASK"][4, 140:160].all()
    assert not out[P + "QUALIFIED_MASK"][[2, 6]].any()


def test_known_weather_is_retained_and_raw_targets_never_train():
    n, g, b = fixture()
    n.fields["RHOHV"] = np.full(n.shape, 0.99, "float32")
    n.fields["SNR"] = np.full(n.shape, 20, "float32")
    for key in ("RHOHV", "SNR"):
        n.field_available[key] = np.ones(n.shape, bool)
    out, _ = run(n, g, b)
    assert not out[P + "QUALIFIED_MASK"][4, 140:160].any()
    assert (out[P + "REJECTION_CODE"][4, 140:160] == 10).all()
    n, g, b = fixture()
    before, _ = run(n, g, b)
    n.fields["DBZH"][4, 140:160] = 55
    after, _ = run(n, g, b)
    for key in ("LEFT_DEG", "RIGHT_DEG", "REFERENCE_SUPPORT_M", "REFERENCE_BLOCKS"):
        assert np.array_equal(before[P + key], after[P + key], equal_nan=True)


def test_serialized_replay_binds_far_original_reference_gates():
    n, g, b = fixture()
    module = load("radial_revision.source_footprint")
    out, _ = run(n, g, b)
    arrays = {**out, **g, **module.evidence(n)}
    module.validate(arrays, n.field_available["DBZH"], b)
    bad = {k: v.copy() for k, v in arrays.items()}
    bad["RV2_SOURCE_LEDGER_SEED_ID"][:, :120] = 0
    bad["RV2_SOURCE_LEDGER_SEED_ID"][:, 180:] = 0
    with pytest.raises(ValueError, match="replay"):
        module.validate(bad, n.field_available["DBZH"], b)


def test_complete_history_revealing_unstable_boundary_must_abstain():
    n = Native(np.full((13, 300), 20.0))
    parent = np.zeros(n.shape, "uint32")
    parent[2:11, 10:180] = 1
    seed = np.zeros_like(parent)
    for row in range(3, 10):
        seed[row, 10:290] = row + 10
    seed[5:10, 180:] = 0
    seed[6, 140:160] = 0
    g = {"RV2_RAW_FAN_ID": parent, "RV2_SOURCE_LEDGER_SEED_ID": seed}
    out, _ = run(n, g, np.zeros(n.shape, bool))
    assert not out[P + "QUALIFIED_MASK"][6, 140:160].any()
    assert (out[P + "REJECTION_CODE"][6, 140:160] == 8).all()
