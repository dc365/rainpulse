"""Whole original RAW edges require measured independent reference windows."""

from types import SimpleNamespace

import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import original_boundary as m
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.geometry import ResourceLimit


def fixture():
    shape = (11, 180)
    z = np.full(shape, np.nan, "float32")
    z[3:7, 10:170] = 20
    z[4:6, 10:170] = 30
    seed = np.zeros(shape, "uint32")
    seed[4:6, 10:170] = 1
    parent = np.zeros(shape, "uint32")
    parent[4:6, 10:170] = 3
    n = SimpleNamespace(
        shape=shape, ranges=np.arange(180)*1000., azimuth=np.arange(11)*1.,
        geometry_good=np.ones(11, bool), gap_after=np.zeros(11, bool),
        fields={"DBZH": z, "SNR": np.zeros(shape, "float32")},
        field_available={"DBZH": np.isfinite(z), "SNR": np.ones(shape, bool)},
    )
    return n, np.zeros(shape, bool), {
        "RV2_SOURCE_LEDGER_SEED_ID": seed, "RV2_RAW_FAN_ID": parent,
    }


def test_recover_whole_original_edges_without_local_parent_or_new_seed():
    n, blocked, group = fixture()
    raw = n.fields["DBZH"].copy()
    original = group["RV2_SOURCE_LEDGER_SEED_ID"].copy()
    arrays, report = m.qualify(n, blocked, group)
    assert arrays[m.PREFIX+"QUALIFIED_MASK"][3, 80] == 1
    assert arrays[m.PREFIX+"QUALIFIED_MASK"][6, 80] == 1
    assert group["RV2_RAW_FAN_ID"][3, 80] == 0
    assert np.array_equal(original, group["RV2_SOURCE_LEDGER_SEED_ID"])
    assert np.array_equal(raw, n.fields["DBZH"], equal_nan=True)
    assert report["action_authority"] is False
    m.validate(arrays, n, blocked, group)


@pytest.mark.parametrize(
    "kind", ["unknown", "nonquiet", "invalid_noise", "barrier", "gap", "unstable"]
)
def test_failed_complete_raw_history_cannot_restart_far_tail(kind):
    n, blocked, group = fixture()
    if kind == "unknown":
        n.field_available["SNR"][2, 10:60] = False
    elif kind == "nonquiet":
        n.fields["SNR"][2, 10:60] = 8
    elif kind == "invalid_noise":
        n.fields["SNR"][2, 10:60] = -100
    elif kind == "barrier":
        blocked[3, 10:20] = True
    elif kind == "gap":
        n.gap_after[3] = True
    else:
        n.fields["DBZH"][2, 10:20] = 20
        n.field_available["DBZH"][2, 10:20] = True
        n.fields["DBZH"][1, 20:40] = 20
        n.fields["DBZH"][2, 20:40] = 20
        n.field_available["DBZH"][1:3, 20:40] = True
    arrays, _ = m.qualify(n, blocked, group)
    assert not arrays[m.PREFIX+"QUALIFIED_MASK"].any()


def test_current_weather_retained_and_qualified_members_cannot_grow():
    n, blocked, group = fixture()
    n.fields["RHOHV"] = np.full(n.shape, np.nan, "float32")
    n.field_available["RHOHV"] = np.zeros(n.shape, bool)
    n.fields["RHOHV"][3, 80] = .99
    n.field_available["RHOHV"][3, 80] = True
    n.fields["SNR"][3, 80] = 20
    n.fields["DBZH"][1, 80:90] = 25
    n.field_available["DBZH"][1, 80:90] = True
    arrays, _ = m.qualify(n, blocked, group)
    assert arrays[m.PREFIX+"QUALIFIED_MASK"][3, 80] == 0
    assert not arrays[m.PREFIX+"QUALIFIED_MASK"][1].any()


def test_target_and_adjacent_windows_do_not_train_boundaries():
    n, blocked, group = fixture()
    arrays, report = m.qualify(n, blocked, group)
    target = next(v for v in report["records"] if v["target_block"] == 4)
    assert all(abs(block-4) > 1 for block in target["reference_blocks"])
    arrays[m.PREFIX+"PARENT_ID"][3, 80] = 99
    with pytest.raises(ValueError, match="replay differs"):
        m.validate(arrays, n, blocked, group)
    with pytest.raises(ResourceLimit, match="no partial"):
        m.qualify(n, blocked, group, maximum_work=1)
