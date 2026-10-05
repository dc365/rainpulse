"""An action cap refuses confirmation; it is not evidence of clean weather."""

import copy

import numpy as np
import pytest

from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2 import (
    fragmented_carrier_integration,
    polar_window_integration,
    snr_carrier_integration,
)
from rainpulse_algo.multiband.xqc_v2.core import Reason
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from . import test_fragmented_carrier_integration as fragments
from . import test_snr_carrier_integration as snr
from .helpers import fixture, station
from .test_polar_window_integration import case as window_case


def scene(prefix):
    if prefix == "POLAR_WINDOW":
        native, _, conf, previous = window_case()
        return native, conf, previous
    factory = fragments if prefix == "FRAGMENTED_CARRIER" else snr
    native = factory.cut()
    return native, factory.cfg(), factory.parent(native)


@pytest.mark.parametrize(
    "module,prefix,cause",
    [
        (polar_window_integration, "POLAR_WINDOW", Reason.POLAR_WINDOW),
        (fragmented_carrier_integration, "FRAGMENTED_CARRIER", Reason.FRAGMENTED_CARRIER),
        (snr_carrier_integration, "SNR_CARRIER", Reason.SNR_CARRIER),
    ],
)
def test_refused_qualified_increment_survives_as_review_without_becoming_accepted(
    module, prefix, cause
):
    native, conf, previous = scene(prefix)
    previous.arrays["XQC_PROPOSED_MASK"][100, 80:85] = 1
    previous.arrays["XQC_QUARANTINE_MASK"][100, 80:85] = 1
    previous.arrays["XQC_REASON"][100, 90:95] = int(Reason.ACTION_BUDGET)
    snapshot = copy.deepcopy(previous)
    accepted = module.extend(native, conf, previous)
    qualified = accepted.arrays["XQC_" + prefix + "_MASK"].astype(bool)
    assert qualified.any()
    refused = module.extend(
        native, conf.model_copy(update={"maximum_new_exclusion_fraction": 0.001}), previous
    )
    assert (refused.arrays["XQC_" + prefix + "_STATE"] == 4).all()
    assert not refused.arrays["XQC_" + prefix + "_MASK"].any()
    name = "XQC_" + prefix + "_BUDGET_REVIEW_MASK"
    assert name in refused.arrays, "qualified source evidence was discarded on refusal"
    review = refused.arrays[name].astype(bool)
    # Window detection may include an already nominated parent member. Review
    # increments have their own original membership and cannot reclaim it.
    existing = snapshot.arrays["XQC_PROPOSED_MASK"].astype(bool) | (
        (snapshot.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET)) != 0
    )
    np.testing.assert_array_equal(review, qualified & ~existing)
    assert review.any()
    assert ((refused.arrays["XQC_REASON"][review] & int(Reason.ACTION_BUDGET)) != 0).all()
    assert ((refused.arrays["XQC_REASON"][review] & int(cause)) != 0).all()
    np.testing.assert_array_equal(
        refused.arrays["XQC_PROPOSED_MASK"], snapshot.arrays["XQC_PROPOSED_MASK"]
    )
    np.testing.assert_array_equal(
        refused.arrays["XQC_QUARANTINE_MASK"], snapshot.arrays["XQC_QUARANTINE_MASK"]
    )
    for key, value in snapshot.arrays.items():
        np.testing.assert_array_equal(previous.arrays[key], value)
    assert not review[existing].any()


MODULES = [
    (polar_window_integration, "POLAR_WINDOW"),
    (fragmented_carrier_integration, "FRAGMENTED_CARRIER"),
    (snr_carrier_integration, "SNR_CARRIER"),
]


def test_window_increment_cannot_promote_an_existing_budget_review():
    native, conf, previous = scene("POLAR_WINDOW")
    qualified = polar_window_integration.extend(native, conf, previous)
    selected = qualified.arrays["XQC_POLAR_WINDOW_MASK"].astype(bool)
    assert selected.any()
    previous.arrays["XQC_REASON"][selected] |= int(Reason.ACTION_BUDGET)
    snapshot = copy.deepcopy(previous)
    out = polar_window_integration.extend(native, conf, previous)
    assert not out.arrays["XQC_PROPOSED_MASK"].any()
    assert not out.arrays["XQC_POLAR_WINDOW_MASK"].any()
    for name, value in snapshot.arrays.items():
        np.testing.assert_array_equal(out.arrays[name], value)


@pytest.mark.parametrize("module,prefix", MODULES)
@pytest.mark.parametrize("failure", ["protection", "resource"])
def test_unavailable_review_has_no_partial_admission(monkeypatch, module, prefix, failure):
    native, conf, previous = scene(prefix)
    conf = conf.model_copy(update={"maximum_new_exclusion_fraction": 0.001})
    assert (
        module.extend(native, conf, previous).arrays["XQC_" + prefix + "_BUDGET_REVIEW_MASK"].any()
    )
    if failure == "protection":
        previous.record["module_records"]["morphology"]["status"] = "RESOURCE_LIMIT_ABSTAINED"
    else:

        def unavailable(*args, **kwargs):
            raise ResourceLimit("bounded source review refused")

        monkeypatch.setattr(module, "detect" if prefix == "POLAR_WINDOW" else "review", unavailable)
    snapshot = copy.deepcopy(previous)
    out = module.extend(native, conf, previous)
    assert not out.arrays["XQC_" + prefix + "_BUDGET_REVIEW_MASK"].any()
    assert not out.arrays["XQC_" + prefix + "_MASK"].any()
    assert (out.arrays["XQC_" + prefix + "_STATE"] == (2 if failure == "protection" else 3)).all()
    for name, value in snapshot.arrays.items():
        np.testing.assert_array_equal(out.arrays[name], value)
        np.testing.assert_array_equal(previous.arrays[name], value)


@pytest.mark.parametrize("module,prefix", MODULES)
def test_explicit_weather_barrier_never_enters_budget_review(module, prefix):
    native, conf, previous = scene(prefix)
    selected = module.extend(native, conf, previous).arrays["XQC_" + prefix + "_MASK"].astype(bool)
    assert selected.any()
    previous.arrays["XQC_HARD_WEATHER_MASK"][selected] = 1
    out = module.extend(
        native, conf.model_copy(update={"maximum_new_exclusion_fraction": 0.001}), previous
    )
    assert not out.arrays["XQC_" + prefix + "_BUDGET_REVIEW_MASK"].any()
    assert not out.arrays["XQC_" + prefix + "_MASK"].any()


@pytest.mark.parametrize("module,prefix", MODULES)
@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_budget_review_reaches_sole_writer_and_exact_native_export(
    monkeypatch, module, prefix, mode
):
    from rainpulse_algo.multiband.codec import decode_arrays
    from rainpulse_algo.multiband.xqc_v2 import complete_family_integration
    from rainpulse_algo.multiband.xqc_v2.export import export_sweep

    native, conf, previous = scene(prefix)
    conf = conf.model_copy(update={"mode": mode, "maximum_new_exclusion_fraction": 0.001})
    before = {key: value.copy() for key, value in native.fields.items()}
    volume, _ = fixture("empty")
    volume.sweeps = [native]
    monkeypatch.setattr(
        "rainpulse_algo.multiband.xqc_v2.pipeline.evaluate_cut", lambda *a, **kw: previous
    )
    monkeypatch.setattr(complete_family_integration, "extend", lambda c, cfg, ev: ev)
    # The disabled counterpart uses the same writer/config/baseline; only the
    # target increment is withheld, so audit equivalence is an actual oracle.
    switch = {
        "POLAR_WINDOW": {"polar_window_candidates_enabled": False},
        "FRAGMENTED_CARRIER": {"fragmented_carrier_candidates_enabled": False},
        "SNR_CARRIER": {"snr_carrier_policy": None},
    }[prefix]
    control = x_qc(volume, station(conf.model_copy(update=switch)), "b" * 64).sweeps[0]
    out = x_qc(volume, station(conf), "b" * 64).sweeps[0]
    review = out.fields["XQC_" + prefix + "_BUDGET_REVIEW_MASK"].astype(bool)
    assert review.any()
    assert np.isfinite(control.fields["DBZH_QC"][review]).any()
    assert not out.fields["XQC_PROPOSED_MASK"].any()
    assert not out.fields["XQC_QUARANTINE_MASK"].any()
    assert not out.fields["QPE_ELIGIBLE_MASK"].any()
    for name in ("QC_ACTION", "DBZH_QC", "DBZH_QC_DISPLAY", "REFLECTIVITY_ELIGIBLE_FOR_CR"):
        if mode == "audit":
            np.testing.assert_array_equal(out.fields[name], control.fields[name])
        else:
            np.testing.assert_array_equal(out.fields[name][~review], control.fields[name][~review])
    if mode != "audit":
        assert (out.fields["QC_ACTION"][review] == 3).all()
        assert np.isnan(out.fields["DBZH_QC"][review]).all()
        assert np.isnan(out.fields["DBZH_QC_DISPLAY"][review]).all()
        assert not out.fields["REFLECTIVITY_ELIGIBLE_FOR_CR"][review].any()
    objects = {}
    reference = export_sweep(out, volume.metadata, objects)
    exported = decode_arrays(
        objects[reference["native"]["object_path"]], maximum_bytes=256 * 1024**2
    )
    for name in ("XQC_" + prefix + "_BUDGET_REVIEW_MASK", "QC_ACTION", "XQC_REASON", "DBZH_RAW"):
        np.testing.assert_array_equal(exported[name], out.fields[name])
    for name, value in before.items():
        np.testing.assert_array_equal(native.fields[name], value)


@pytest.mark.parametrize("module,prefix", MODULES)
def test_budget_review_atomically_rolls_back_on_evidence_refusal(monkeypatch, module, prefix):
    native, conf, previous = scene(prefix)
    conf = conf.model_copy(update={"maximum_new_exclusion_fraction": 0.001})
    qualified = module.extend(native, conf, previous)
    name = "XQC_" + prefix + "_BUDGET_REVIEW_MASK"
    assert qualified.arrays[name].any()  # proves this exercises the rollback path
    snapshot = copy.deepcopy(previous)

    def refused(*args, **kwargs):
        raise ResourceLimit("forced combined-evidence refusal")

    monkeypatch.setattr(module, "bounded", refused)
    out = module.extend(native, conf, previous)
    assert not out.arrays[name].any()
    assert (out.arrays["XQC_" + prefix + "_STATE"] == 5).all()
    for key, value in snapshot.arrays.items():
        np.testing.assert_array_equal(out.arrays[key], value)
        np.testing.assert_array_equal(previous.arrays[key], value)
