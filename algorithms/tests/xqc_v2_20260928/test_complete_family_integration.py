"""Complete family candidates cannot withdraw old scientific dispositions."""

import copy
from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.complete_family_integration import extend
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
from rainpulse_algo.multiband.xqc_v2.core import Reason, empty
from rainpulse_algo.multiband.xqc_v2.source_family_geometry import source_view
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .helpers import config, fixture, station
from .test_complete_source_families import wide


def policy(**kwargs):
    return config(
        radial_source_enabled=True,
        radial_source_fan_model_enabled=True,
        complete_source_families_enabled=True,
        noise_censor_snr_db=3.0,
        radial_source_local_policy="joint_evidence",
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_single_writer_full_family_is_candidate_and_keeps_raw(mode):
    cut = wide()
    v, _ = fixture("empty")
    v.sweeps = [cut]
    raw = copy.deepcopy(cut.fields)
    cfg = policy(mode=mode)
    out = x_qc(v, station(cfg), "b" * 64).sweeps[0].fields
    added = out["XQC_COMPLETE_FAMILY_MASK"] == 1
    assert added[180, cut.range_m > 25000].mean() > 0.9
    assert not out["XQC_SOURCE_KIND"][added].any()
    assert not out["XQC_QUARANTINE_MASK"][added].any()
    assert not out["XQC_RADIAL_SOURCE_MASK"][added].any()
    if mode == "audit":
        assert not out["XQC_WITHHELD_MASK"][added].any()
    else:
        assert out["XQC_WITHHELD_MASK"][added].all()
        assert np.isnan(out["DBZH_QC"][added]).all()
        assert (out["QC_ACTION"][added] == 3).all()
        assert not out["REFLECTIVITY_ELIGIBLE_FOR_CR"][added].any()
    assert not out["QPE_ELIGIBLE_MASK"].any()
    for k, value in raw.items():
        np.testing.assert_array_equal(cut.fields[k], value)


def test_budget_review_keeps_completed_parent_actions():
    cut = wide()
    parent = empty(cut, "EVALUATED")
    p = parent.arrays
    p["XQC_PROPOSED_MASK"][0, 50:55] = 1
    p["XQC_QUARANTINE_MASK"][0, 50:55] = 1
    p["XQC_RADIAL_SOURCE_MASK"][0, 50:55] = 1
    p["XQC_SOURCE_KIND"] = np.zeros(cut.fields["DBZH"].shape, "uint8")
    p["XQC_SOURCE_KIND"][0, 50:55] = 4
    p["XQC_REASON"][0, 50:55] = int(Reason.RADIAL_SOURCE)
    before = {k: v.copy() for k, v in p.items()}
    out = extend(cut, policy(maximum_new_exclusion_fraction=0.001), parent)
    selected = out.arrays["XQC_COMPLETE_FAMILY_MASK"].astype(bool)
    assert selected.any() and (out.arrays["XQC_COMPLETE_FAMILY_STATE"] == 4).all()
    assert ((out.arrays["XQC_REASON"][selected] & int(Reason.ACTION_BUDGET)) != 0).all()
    for name in (
        "XQC_SOURCE_KIND",
        "XQC_QUARANTINE_MASK",
        "XQC_RADIAL_SOURCE_MASK",
        "XQC_PROPOSED_MASK",
    ):
        np.testing.assert_array_equal(out.arrays[name], before[name])
    np.testing.assert_array_equal(
        out.arrays["XQC_REASON"][~selected], before["XQC_REASON"][~selected]
    )


def test_accepted_increment_does_not_promote_parent_budget_review():
    cut = wide()
    parent = empty(cut, "EVALUATED")
    p = parent.arrays
    p["XQC_REASON"][0, 100:107] = int(Reason.ACTION_BUDGET)
    p["XQC_PROPOSED_MASK"][1, 100:105] = 1
    p["XQC_REASON"][1, 100:105] = int(Reason.RADIAL_SOURCE)
    before = {k: v.copy() for k, v in p.items()}
    out = extend(cut, policy(maximum_new_exclusion_fraction=1.0), parent)
    selected = out.arrays["XQC_COMPLETE_FAMILY_MASK"].astype(bool)
    assert selected.any() and (out.arrays["XQC_COMPLETE_FAMILY_STATE"] == 1).all()
    assert out.arrays["XQC_PROPOSED_MASK"][selected].all()
    np.testing.assert_array_equal(
        out.arrays["XQC_PROPOSED_MASK"].astype(bool),
        before["XQC_PROPOSED_MASK"].astype(bool) | selected,
    )
    np.testing.assert_array_equal(
        out.arrays["XQC_REASON"][~selected], before["XQC_REASON"][~selected]
    )
    for name, value in before.items():
        np.testing.assert_array_equal(parent.arrays[name], value)


@pytest.mark.parametrize("failure", ["resource", "evidence"])
def test_failed_increment_never_changes_parent_masks(monkeypatch, failure):
    cut = wide()
    parent = empty(cut, "EVALUATED")

    def refused(*args, **kwargs):
        raise ResourceLimit("frozen allowance")

    target = "detect_complete" if failure == "resource" else "bounded"
    monkeypatch.setattr(
        "rainpulse_algo.multiband.xqc_v2.complete_family_integration." + target, refused
    )
    out = extend(cut, policy(), parent)
    assert not out.arrays["XQC_COMPLETE_FAMILY_MASK"].any()
    for name, value in parent.arrays.items():
        np.testing.assert_array_equal(out.arrays[name], value)
    assert out.arrays["XQC_COMPLETE_FAMILY_STATE"][0, 0] == (3 if failure == "resource" else 5)


def test_extreme_valid_receiver_is_supported_but_invalid_not_repaired():
    cut = wide()
    f = {k: v.copy() for k, v in cut.fields.items()}
    f["DBZH"][180, 100:104] = [81, 94, 101, 90]
    f["DBZH_VALID_MASK"] = np.isfinite(f["DBZH"]).astype("uint8")
    f["DBZH_VALID_MASK"][180, 103] = 0
    cut = replace(cut, fields=f)
    view = source_view(cut, policy())
    _, valid = view.sweep.moment("DBZH")
    assert valid[180, 100:102].all()
    assert not valid[180, 102:104].any()
    np.testing.assert_array_equal(view.sweep.fields["DBZH"][180, 100:104], [81, 94, 101, 90])


def test_relative_receiver_mode_requires_distinct_explicit_contract():
    with pytest.raises(ValueError):
        XQCConfig(complete_source_family_reference_mode="relative_receiver")
    c = policy(complete_source_family_reference_mode="relative_receiver")
    assert c.digest != policy().digest


def test_missing_parent_protection_is_unavailable_and_has_no_partial_actions():
    cut = wide()
    parent = empty(cut, "EVALUATED")
    del parent.arrays["XQC_HARD_WEATHER_MASK"]
    out = extend(cut, policy(), parent)
    assert (out.arrays["XQC_COMPLETE_FAMILY_STATE"] == 2).all()
    assert not out.arrays["XQC_COMPLETE_FAMILY_MASK"].any()
    assert out.record["status"] == "DEGRADED_COMPLETE_FAMILY_PROTECTION_UNAVAILABLE"
    for name, value in parent.arrays.items():
        np.testing.assert_array_equal(out.arrays[name], value)
