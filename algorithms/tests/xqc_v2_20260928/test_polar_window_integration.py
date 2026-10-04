"""Incremental shape evidence cannot undo completed parent dispositions."""

import hashlib
import json

import numpy as np
import pytest
from pydantic import ValidationError

from rainpulse_algo.multiband.model import Sweep, Volume
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.core import empty
from rainpulse_algo.multiband.xqc_v2.polar_morphology import MorphologyPolicy
from rainpulse_algo.multiband.xqc_v2.polar_window_integration import extend
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .helpers import config, fixture, station
from .test_polar_window_objects import mixed


def case(**changes):
    native, source = mixed()
    fields = {k: a.copy() for k, a in native.fields.items()}
    fields.update(
        OBSERVED_MASK=np.ones(native.shape, "uint8"), NO_ECHO_MASK=np.zeros(native.shape, "uint8")
    )
    cut = Sweep(
        0, native.azimuth, native.ranges, native.elevation, 1787875200.0 + native.ray_time_s, fields
    )
    cfg = config(
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
        morphology=MorphologyPolicy(
            version="x-polar-morphology-20261002-v7",
            grouped_envelopes_enabled=True,
            branching_envelopes_enabled=True,
            compact_counterexamples_enabled=True,
        ).model_dump(),
        polar_window_candidates_enabled=True,
        **changes,
    )
    parent = empty(cut, "EVALUATED")
    parent.record["module_records"] = {"morphology": {"status": "EVALUATED"}}
    parent.arrays["XQC_AVAILABLE_MASK"][:] = 1
    return cut, source, cfg, parent


def test_disabled_is_exact_parent_and_legacy_parameter_digest():
    cfg = config()
    old = cfg.model_dump(mode="json")
    old.pop("polar_window_candidates_enabled")
    old.pop("fragmented_carrier_candidates_enabled")
    old.pop("complete_source_families_enabled")
    old.pop("complete_source_family_reference_mode")
    old.pop("snr_carrier_policy")
    digest = hashlib.sha256(
        json.dumps(old, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert cfg.digest == digest
    v, _ = fixture("empty")
    parent = empty(v.sweeps[0], "EVALUATED")
    assert extend(v.sweeps[0], cfg, parent) is parent


@pytest.mark.parametrize("value", [1, "true", None])
def test_switch_is_strict(value):
    with pytest.raises(ValidationError):
        config(polar_window_candidates_enabled=value)


def test_complete_counterexample_protection_required():
    with pytest.raises(ValidationError):
        config(polar_window_candidates_enabled=True)


@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_normal_sole_writer_candidate_semantics(mode):
    cut, source, cfg, _ = case(mode=mode)
    vol, _ = fixture("empty")
    vol = Volume(vol.metadata, [cut])
    before = cut.fields["DBZH"].copy()
    # Parent evidence is independently supplied so this test exercises the
    # integration and actual writer rather than depending on another proposer.
    from unittest.mock import patch

    parent = empty(cut, "EVALUATED")
    parent.arrays["XQC_AVAILABLE_MASK"][:] = 1
    parent.record["module_records"] = {"morphology": {"status": "EVALUATED"}}
    with patch("rainpulse_algo.multiband.xqc_v2.pipeline.evaluate_cut", return_value=parent):
        out = x_qc(vol, station(cfg), "b" * 64)
    f = out.sweeps[0].fields
    assert f["XQC_POLAR_WINDOW_MASK"][source].all()
    assert not f["XQC_POLAR_WINDOW_MASK"][~source].any()
    assert not f["XQC_QUARANTINE_MASK"].any() and not f["QPE_ELIGIBLE_MASK"].any()
    if mode == "audit":
        assert not f["XQC_WITHHELD_MASK"].any()
        np.testing.assert_array_equal(f["QC_ACTION"], f["XQC_BASELINE_ACTION"])
    else:
        assert (f["QC_ACTION"][source] == 3).all()
        assert not f["REFLECTIVITY_ELIGIBLE_FOR_CR"][source].any()
        assert not np.isfinite(f["DBZH_QC"][source]).any()
        assert not np.isfinite(f["DBZH_QC_DISPLAY"][source]).any()
    np.testing.assert_array_equal(cut.fields["DBZH"], before)


@pytest.mark.parametrize(
    "key", ["HARD_WEATHER", "LOCAL_WEATHER", "MORPHOLOGY_COUNTEREXAMPLE", "CONTEXT_WEATHER"]
)
def test_supplied_protection_is_not_overruled(key):
    cut, source, cfg, parent = case()
    parent.arrays["XQC_" + key + "_MASK"] = source.astype("uint8")
    out = extend(cut, cfg, parent)
    assert not out.arrays["XQC_POLAR_WINDOW_MASK"].any()
    assert not out.arrays["XQC_PROPOSED_MASK"].any()


def assert_parent_unchanged(parent, out):
    for k, a in parent.arrays.items():
        np.testing.assert_array_equal(out.arrays[k], a)
    assert not out.arrays["XQC_POLAR_WINDOW_MASK"].any()


def test_incremental_action_budget_preserves_parent_without_new_withholding():
    cut, source, cfg, parent = case(maximum_new_exclusion_fraction=0.001)
    parent.arrays["XQC_PROPOSED_MASK"][0, 0] = 1
    parent.arrays["XQC_QUARANTINE_MASK"][0, 0] = 1
    parent.arrays["XQC_REASON"][0, 0] = 8
    out = extend(cut, cfg, parent)
    assert_parent_unchanged(parent, out)
    assert out.record["module_records"]["polar_windows"]["status"] == "ACTION_BUDGET_ABSTAINED"
    assert out.record["module_records"]["polar_windows"]["qualified_gates"] > 0


def test_resource_failure_has_no_partial_new_mask(monkeypatch):
    cut, _, cfg, parent = case()

    def failed(*args, **kwargs):
        raise ResourceLimit("forced bounded computation")

    monkeypatch.setattr("rainpulse_algo.multiband.xqc_v2.polar_window_integration.detect", failed)
    out = extend(cut, cfg, parent)
    assert_parent_unchanged(parent, out)
    assert out.record["module_records"]["polar_windows"]["status"] == "RESOURCE_LIMIT_ABSTAINED"
    assert out.record["status"] == "DEGRADED_POLAR_WINDOW_RESOURCE_LIMIT_ABSTAINED"
    assert "polar_windows" in out.record["degraded_modules"]


def test_combined_evidence_overflow_preserves_parent(monkeypatch):
    cut, source, cfg, parent = case(maximum_evidence_bytes=4096)
    import rainpulse_algo.multiband.xqc_v2.polar_window_integration as module

    original = module.detect

    def huge(*args, **kwargs):
        result = original(
            args[0], args[1].model_copy(update={"maximum_evidence_bytes": 4 * 1024**2}), **kwargs
        )
        # Unique random bytes cannot be compressed into the record allowance.
        result.record["detail"] = np.random.default_rng(20).bytes(20000).hex()
        return result

    monkeypatch.setattr(module, "detect", huge)
    out = extend(cut, cfg, parent)
    assert_parent_unchanged(parent, out)
    assert out.record["module_records"]["polar_windows"]["status"] == "EVIDENCE_BUDGET_ABSTAINED"
    assert out.record["status"] == "DEGRADED_POLAR_WINDOW_EVIDENCE_BUDGET_ABSTAINED"


def test_failed_parent_does_not_claim_absence_of_counterexamples():
    cut, _, cfg, parent = case()
    parent.record["module_records"]["morphology"]["status"] = "RESOURCE_LIMIT_ABSTAINED"
    out = extend(cut, cfg, parent)
    assert_parent_unchanged(parent, out)
    assert (
        out.record["module_records"]["polar_windows"]["status"] == "UNAVAILABLE_COMPACT_PROTECTION"
    )


def test_missing_parent_protection_array_is_unknown():
    cut, _, cfg, parent = case()
    del parent.arrays["XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK"]
    out = extend(cut, cfg, parent)
    assert_parent_unchanged(parent, out)
    assert (
        out.record["module_records"]["polar_windows"]["status"] == "UNAVAILABLE_COMPACT_PROTECTION"
    )


def test_contract_schema_matches_activation_dependency():
    from pathlib import Path

    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/x-qc-v2.schema.json"
        ).read_text()
    )
    _, _, cfg, _ = case()
    jsonschema.validate(cfg.model_dump(mode="json"), schema)
    for bad in (dict(polar_window_candidates_enabled="true"), dict(morphology=None)):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(cfg.model_dump(mode="json") | bad, schema)


def test_entire_parent_record_allowance_never_blocks_completed_output():
    cut, _, cfg, parent = case(maximum_evidence_bytes=4096)
    raw = np.random.default_rng(200).bytes(2200).hex()
    parent.record["detail"] = raw[:3900]
    out = extend(cut, cfg, parent)
    assert_parent_unchanged(parent, out)
    assert (out.arrays["XQC_POLAR_WINDOW_STATE"] == 5).all()
    assert out.record == parent.record


def test_disabled_normal_output_has_exact_prior_numerical_fields():
    cut, _, cfg, _ = case(mode="quarantine")
    old_cfg = cfg.model_copy(update={"polar_window_candidates_enabled": False})
    volume, _ = fixture("empty")
    volume = Volume(volume.metadata, [cut])
    prior = x_qc(volume, station(old_cfg), "b" * 64)
    current = x_qc(volume, station(cfg), "b" * 64)
    assert (
        current.sweeps[0].xqc_diagnostics["implementation_revision"]
        == "xqc-polar-window-integration-20261003-v2"
    )
    selected = current.sweeps[0].fields["XQC_POLAR_WINDOW_MASK"].astype(bool)
    # A complete actual parent run is required; no evidence/writer mocks here.
    assert (
        current.sweeps[0].xqc_diagnostics["module_records"]["morphology"]["status"] == "EVALUATED"
    )
    for k in (
        "DBZH_RAW",
        "XQC_QUARANTINE_MASK",
        "XQC_MORPHOLOGY_MASK",
        "XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK",
    ):
        np.testing.assert_array_equal(current.sweeps[0].fields[k], prior.sweeps[0].fields[k])
    assert not (selected & current.sweeps[0].fields["XQC_HARD_WEATHER_MASK"].astype(bool)).any()


def test_normal_completion_exports_incremental_refusal_warning(monkeypatch):
    from unittest.mock import patch

    cut, _, cfg, parent = case(mode="quarantine")
    volume, _ = fixture("empty")
    volume = Volume(volume.metadata, [cut])

    def failed(*args, **kwargs):
        raise ResourceLimit("forced completion refusal")

    monkeypatch.setattr("rainpulse_algo.multiband.xqc_v2.polar_window_integration.detect", failed)
    with patch("rainpulse_algo.multiband.xqc_v2.pipeline.evaluate_cut", return_value=parent):
        out = x_qc(volume, station(cfg), "b" * 64).sweeps[0]
    assert out.xqc_diagnostics["status"].startswith("DEGRADED_POLAR_WINDOW_")
    assert not out.fields["XQC_WITHHELD_MASK"].any()
    np.testing.assert_array_equal(out.fields["QC_ACTION"], out.fields["XQC_BASELINE_ACTION"])
    from rainpulse_algo.multiband.xqc_v2.export import export_sweep

    objects = {}
    exported = export_sweep(out, volume.metadata, objects)
    assert exported["status"] == out.xqc_diagnostics["status"]
