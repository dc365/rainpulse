"""Complete carrier review reaches one writer without rewriting old decisions."""

import copy

import numpy as np
import pytest

from rainpulse_algo.multiband.model import Sweep
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
from rainpulse_algo.multiband.xqc_v2.core import Reason, empty
from rainpulse_algo.multiband.xqc_v2.fragmented_carrier_integration import extend
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .helpers import config, fixture, station
from .test_fragmented_carriers import fragmented, policy


def cut():
    native, _ = fragmented()
    shape = native.shape
    return Sweep(
        0, native.azimuth, native.ranges, native.elevation,
        1787875200.0 + native.ray_time_s,
        dict(DBZH=native.fields["DBZH"], SNRH=native.fields["SNR"],
             OBSERVED_MASK=np.ones(shape, "uint8"), NO_ECHO_MASK=np.zeros(shape, "uint8")),
    )


def cfg(**kwargs):
    return config(
        morphology=policy(), fragmented_carrier_candidates_enabled=True,
        receiver_enabled=False, radial_objects_enabled=False,
        clutter_enabled=False, isolation_enabled=False, **kwargs,
    )


def parent(c):
    ev = empty(c, "EVALUATED")
    ev.arrays["XQC_CONTEXT_WEATHER_MASK"] = np.zeros(c.fields["DBZH"].shape, "uint8")
    ev.record["module_records"] = {"morphology": {"status": "EVALUATED"}}
    return ev


@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_real_pipeline_preserves_raw_and_applies_only_candidate_actions(mode):
    c = cut()
    v, _ = fixture("empty")
    v.sweeps = [c]
    before = {k: value.copy() for k, value in c.fields.items()}
    base = cfg(mode=mode)
    disabled = base.model_copy(update={"fragmented_carrier_candidates_enabled": False})
    old = x_qc(v, station(disabled), "b" * 64).sweeps[0]
    out = x_qc(v, station(base), "b" * 64).sweeps[0]
    a = out.fields
    new = a["XQC_FRAGMENTED_CARRIER_MASK"] == 1
    assert new.sum() > 1200  # positive oracle; old compact fragments remain visible
    assert np.isfinite(old.fields["DBZH_QC"][new]).all()
    assert not a["XQC_QUARANTINE_MASK"][new].any()
    assert not a["QPE_ELIGIBLE_MASK"].any()
    if mode == "audit":
        for name in ("QC_ACTION", "DBZH_QC", "DBZH_QC_DISPLAY", "REFLECTIVITY_ELIGIBLE_FOR_CR"):
            np.testing.assert_array_equal(a[name], old.fields[name])
    else:
        assert (a["QC_ACTION"][new] == 3).all()
        assert not a["REFLECTIVITY_ELIGIBLE_FOR_CR"][new].any()
        assert np.isnan(a["DBZH_QC"][new]).all()
        assert np.isnan(a["DBZH_QC_DISPLAY"][new]).all()
    for name, value in before.items():
        np.testing.assert_array_equal(c.fields[name], value)
    for name in ("XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK", "XQC_QUARANTINE_MASK"):
        np.testing.assert_array_equal(a[name], old.fields[name])


def test_disabled_flag_is_identity_and_has_legacy_digest():
    c = cut()
    ev = parent(c)
    disabled = cfg().model_copy(update={"fragmented_carrier_candidates_enabled": False})
    assert extend(c, disabled, ev) is ev
    import hashlib
    import json

    payload = disabled.model_dump(mode="json")
    for name in ("fragmented_carrier_candidates_enabled", "polar_window_candidates_enabled",
                 "complete_source_families_enabled", "complete_source_family_reference_mode"):
        payload.pop(name)
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode()).hexdigest()
    assert disabled.digest == expected and disabled.digest != cfg().digest
    with pytest.raises(ValueError):
        XQCConfig(fragmented_carrier_candidates_enabled=True)
    with pytest.raises(ValueError):
        XQCConfig(fragmented_carrier_candidates_enabled=1)


def test_old_budget_review_is_not_promoted_and_protection_remains_authoritative():
    c = cut()
    ev = parent(c)
    ev.arrays["XQC_REASON"][53, 620:625] = int(Reason.ACTION_BUDGET)
    ev.arrays["XQC_PROPOSED_MASK"][53, 630:635] = 1
    ev.arrays["XQC_QUARANTINE_MASK"][53, 630:635] = 1
    ev.arrays["XQC_HARD_WEATHER_MASK"][53, 820:825] = 1
    ev.arrays["XQC_CONTEXT_WEATHER_MASK"][53, 900:905] = 1
    before = copy.deepcopy(ev)
    out = extend(c, cfg(), ev)
    added = out.arrays["XQC_FRAGMENTED_CARRIER_MASK"].astype(bool)
    assert added.any()
    assert not added[53, 620:635].any()
    assert not added[53, 820:825].any() and not added[53, 900:905].any()
    np.testing.assert_array_equal(out.arrays["XQC_PROPOSED_MASK"].astype(bool),
                                  before.arrays["XQC_PROPOSED_MASK"].astype(bool) | added)
    assert not out.arrays["XQC_PROPOSED_MASK"][53, 620:625].any()
    for name, value in before.arrays.items():
        np.testing.assert_array_equal(ev.arrays[name], value)
        if name not in ("XQC_REASON", "XQC_PROPOSED_MASK"):
            np.testing.assert_array_equal(out.arrays[name], value)


@pytest.mark.parametrize("failure,state", [("protection", 2), ("resource", 3),
                                         ("action", 4), ("evidence", 5)])
def test_failed_increment_keeps_parent_and_native_warning(monkeypatch, failure, state):
    c = cut()
    ev = parent(c)
    conf = cfg(maximum_new_exclusion_fraction=0.001) if failure == "action" else cfg()
    if failure == "protection":
        del ev.arrays["XQC_CONTEXT_WEATHER_MASK"]
    if failure in ("resource", "evidence"):
        def refused(*args, **kwargs):
            raise ResourceLimit("frozen allowance")

        monkeypatch.setattr("rainpulse_algo.multiband.xqc_v2.fragmented_carrier_integration."
                            + ("review" if failure == "resource" else "bounded"), refused)
    before = copy.deepcopy(ev)
    out = extend(c, conf, ev)
    assert not out.arrays["XQC_FRAGMENTED_CARRIER_MASK"].any()
    assert (out.arrays["XQC_FRAGMENTED_CARRIER_STATE"] == state).all()
    for name, value in before.arrays.items():
        np.testing.assert_array_equal(out.arrays[name], value)
        np.testing.assert_array_equal(ev.arrays[name], value)


@pytest.mark.parametrize("source_policy,morph_policy", [
    ("protect", "protect"), ("protect", "joint_review"), ("joint_evidence", "protect"),
])
def test_local_proxy_protection_cannot_be_silently_overridden(source_policy, morph_policy):
    c = cut()
    ev = parent(c)
    conf = cfg(radial_source_local_policy=source_policy).model_copy(update={
        "morphology": policy().model_copy(update={"local_weather_policy": morph_policy}),
    })
    assert extend(c, conf, ev).arrays["XQC_FRAGMENTED_CARRIER_MASK"].any()
    ev.arrays["XQC_LOCAL_WEATHER_MASK"][53, 600:1080] = 1
    out = extend(c, conf, ev)
    # Full-track protection can also bar both neighboring subcarriers; do not
    # require a deletion elsewhere merely to satisfy a nonempty assertion.
    assert not out.arrays["XQC_FRAGMENTED_CARRIER_MASK"][53].any()


def test_evidence_overflow_warning_survives_pipeline(monkeypatch):
    def refused(*args, **kwargs):
        raise ResourceLimit("entire evidence allowance already held by parent")

    monkeypatch.setattr(
        "rainpulse_algo.multiband.xqc_v2.fragmented_carrier_integration.bounded", refused,
    )
    v, _ = fixture("empty")
    v.sweeps = [cut()]
    out = x_qc(v, station(cfg(mode="cr_only")), "b" * 64).sweeps[0]
    assert (out.fields["XQC_FRAGMENTED_CARRIER_STATE"] == 5).all()
    assert out.xqc_diagnostics["status"].startswith("DEGRADED")
    assert not out.fields["XQC_FRAGMENTED_CARRIER_MASK"].any()


def test_schema_has_strict_activation_and_protection_dependency():
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[3]
                         / "contracts/internal/multiband/x-qc-v2.schema.json").read_text())
    payload = cfg().model_dump(mode="json")
    jsonschema.validate(payload, schema)
    for bad in ({"fragmented_carrier_candidates_enabled": "true"},
                {"fragmented_carrier_candidates_enabled": 1}, {"morphology": None},
                {"morphology": payload["morphology"] | {"compact_counterexamples_enabled": False}}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload | bad, schema)


def test_enabled_native_export_retains_exact_actions_and_new_evidence():
    import hashlib
    import json

    from rainpulse_algo.multiband.codec import decode_arrays
    from rainpulse_algo.multiband.xqc_v2.export import export_sweep

    v, _ = fixture("empty")
    v.sweeps = [cut()]
    out = x_qc(v, station(cfg(mode="cr_only")), "b" * 64)
    objects = {}
    reference = export_sweep(out.sweeps[0], out.metadata, objects)
    proof = json.loads(objects[reference["evidence_path"]])
    blob = objects[reference["native"]["object_path"]]
    assert hashlib.sha256(blob).hexdigest() == reference["native"]["sha256"]
    arrays = decode_arrays(blob, maximum_bytes=256 * 1024**2)
    assert arrays["XQC_FRAGMENTED_CARRIER_MASK"].sum() > 1200
    for name in ("DBZH_RAW", "DBZH_QC", "QC_ACTION", "XQC_REASON",
                 "REFLECTIVITY_ELIGIBLE_FOR_CR", "XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK",
                 "XQC_FRAGMENTED_CARRIER_MASK", "XQC_FRAGMENTED_CARRIER_STATE"):
        np.testing.assert_array_equal(arrays[name], out.sweeps[0].fields[name])
    assert proof["module_records"]["fragmented_carriers"]["weather_truth"] is False
    assert not arrays["QPE_ELIGIBLE_MASK"].any()
