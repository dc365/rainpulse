"""An SNR nomination reaches the sole writer without broadening actions."""

import copy

import numpy as np
import pytest

from rainpulse_algo.multiband.model import Sweep
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
from rainpulse_algo.multiband.xqc_v2.core import Reason, empty
from rainpulse_algo.multiband.xqc_v2.snr_carrier_integration import extend
from rainpulse_algo.multiband.xqc_v2.snr_carriers import SNRCarrierPolicy
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .helpers import fixture, station
from .test_complete_family_integration import policy as source_policy
from .test_fragmented_carriers import policy as geometry_policy
from .test_polar_morphology import scene


def cut():
    s, _ = scene("fan")
    return Sweep(
        0,
        s.azimuth,
        s.ranges,
        s.elevation,
        1787875200.0 + s.ray_time_s,
        dict(
            DBZH=s.fields["DBZH"],
            SNRH=s.fields["SNR"],
            OBSERVED_MASK=np.ones(s.shape, "uint8"),
            NO_ECHO_MASK=np.zeros(s.shape, "uint8"),
        ),
    )


def cfg(**kwargs):
    geometry = geometry_policy()
    return source_policy(
        morphology=geometry,
        snr_carrier_policy=SNRCarrierPolicy(geometry=geometry, levels_db=(5.0, 15.0, 25.0)),
        **kwargs,
    )


def parent(c):
    ev = empty(c, "EVALUATED")
    ev.arrays["XQC_CONTEXT_WEATHER_MASK"] = np.zeros(c.fields["DBZH"].shape, "uint8")
    ev.arrays["XQC_COMPLETE_FAMILY_MASK"] = np.zeros(c.fields["DBZH"].shape, "uint8")
    ev.arrays["XQC_COMPLETE_FAMILY_STATE"] = np.ones(c.fields["DBZH"].shape, "uint8")
    ev.record["module_records"] = {"morphology": {"status": "EVALUATED"}}
    return ev


def test_nonempty_original_snr_increment_preserves_existing_decisions():
    c = cut()
    ev = parent(c)
    ev.arrays["XQC_PROPOSED_MASK"][100, 80:85] = 1
    ev.arrays["XQC_QUARANTINE_MASK"][100, 80:85] = 1
    ev.arrays["XQC_REASON"][100, 90:95] = int(Reason.ACTION_BUDGET)
    before = copy.deepcopy(ev)
    out = extend(c, cfg(), ev)
    added = out.arrays["XQC_SNR_CARRIER_MASK"].astype(bool)
    assert added.sum() > 100
    assert not added[100, 80:85].any() and not added[100, 90:95].any()
    assert not out.arrays["XQC_PROPOSED_MASK"][100, 90:95].any()
    for name, value in before.arrays.items():
        np.testing.assert_array_equal(ev.arrays[name], value)
        if name not in ("XQC_REASON", "XQC_PROPOSED_MASK"):
            np.testing.assert_array_equal(out.arrays[name], value)


@pytest.mark.parametrize(
    "source_local,morph_local",
    [("protect", "protect"), ("protect", "joint_review"), ("joint_evidence", "protect")],
)
def test_one_protection_policy_cannot_be_relaxed(source_local, morph_local):
    c = cut()
    ev = parent(c)
    config = cfg().model_copy(update={"radial_source_local_policy": source_local})
    geometry = geometry_policy().model_copy(update={"local_weather_policy": morph_local})
    config = config.model_copy(
        update={
            "morphology": geometry,
            "snr_carrier_policy": SNRCarrierPolicy(geometry=geometry, levels_db=(5.0, 15.0, 25.0)),
        }
    )
    assert extend(c, config, ev).arrays["XQC_SNR_CARRIER_MASK"].any()
    ev.arrays["XQC_LOCAL_WEATHER_MASK"][c.fields["DBZH"] >= 5] = 1
    assert not extend(c, config, ev).arrays["XQC_SNR_CARRIER_MASK"].any()


@pytest.mark.parametrize(
    "failure,state",
    [("source", 2), ("protection", 2), ("resource", 3), ("action", 4), ("evidence", 5)],
)
def test_refused_increment_retains_parent_and_its_native_warning(monkeypatch, failure, state):
    c = cut()
    ev = parent(c)
    conf = cfg(maximum_new_exclusion_fraction=0.001) if failure == "action" else cfg()
    if failure == "source":
        del c.fields["SNRH"]
    if failure == "protection":
        del ev.arrays["XQC_CONTEXT_WEATHER_MASK"]
    if failure in ("resource", "evidence"):

        def refused(*args, **kwargs):
            raise ResourceLimit("fixed allowance")

        monkeypatch.setattr(
            "rainpulse_algo.multiband.xqc_v2.snr_carrier_integration."
            + ("review" if failure == "resource" else "bounded"),
            refused,
        )
    before = copy.deepcopy(ev)
    out = extend(c, conf, ev)
    assert not out.arrays["XQC_SNR_CARRIER_MASK"].any()
    assert (out.arrays["XQC_SNR_CARRIER_STATE"] == state).all()
    review = out.arrays["XQC_SNR_CARRIER_BUDGET_REVIEW_MASK"].astype(bool)
    assert bool(review.any()) == (failure == "action")
    for name, value in before.arrays.items():
        if failure == "action" and name == "XQC_REASON":
            np.testing.assert_array_equal(out.arrays[name][~review], value[~review])
        else:
            np.testing.assert_array_equal(out.arrays[name], value)
        np.testing.assert_array_equal(ev.arrays[name], value)
    if failure != "evidence":
        assert out.record["status"].startswith("DEGRADED")


def test_disabled_policy_keeps_legacy_digest_identity_and_requires_explicit_contract():
    c = cut()
    ev = parent(c)
    disabled = cfg().model_copy(update={"snr_carrier_policy": None})
    assert extend(c, disabled, ev) is ev and disabled.digest != cfg().digest
    assert disabled.digest == source_policy(morphology=geometry_policy()).digest
    with pytest.raises(ValueError):
        XQCConfig(snr_carrier_policy=True)
    payload = cfg().model_dump(mode="json")
    for change in [{"complete_source_families_enabled": False}, {"morphology": None}]:
        with pytest.raises(ValueError):
            XQCConfig.model_validate({**payload, **change})


def test_schema_and_model_require_explicit_policy_and_native_protection():
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/x-qc-v2.schema.json"
        ).read_text()
    )
    payload = cfg().model_dump(mode="json")
    jsonschema.validate(payload, schema)
    for bad in [
        {"snr_carrier_policy": True},
        {"morphology": None},
        {"complete_source_families_enabled": False},
        {"snr_carrier_policy": {"levels_db": [5, 5], "geometry": payload["morphology"]}},
    ]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**payload, **bad}, schema)
    different = copy.deepcopy(payload)
    different["snr_carrier_policy"]["geometry"]["maximum_width_deg"] = 60
    with pytest.raises(ValueError):
        XQCConfig.model_validate(different)


@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_sole_normal_writer_exports_candidate_without_confirming_source(mode):
    # Stub the completed parent evidence to isolate the new writer admission.
    # The actual original geometry/profile evaluator still runs on native data.
    c = cut()
    v, _ = fixture("empty")
    v.sweeps = [c]
    conf = cfg(mode=mode)
    from unittest.mock import patch

    import rainpulse_algo.multiband.xqc_v2.pipeline as pipeline

    with (
        patch.object(pipeline, "evaluate_cut", return_value=parent(c)),
        patch(
            "rainpulse_algo.multiband.xqc_v2.complete_family_integration.extend",
            side_effect=lambda c, cfg, e: e,
        ),
    ):
        old = x_qc(
            v, station(conf.model_copy(update={"snr_carrier_policy": None})), "b" * 64
        ).sweeps[0]
        out = x_qc(v, station(conf), "b" * 64).sweeps[0]
    new = out.fields["XQC_SNR_CARRIER_MASK"].astype(bool)
    assert new.sum() > 100
    assert (
        not out.fields["XQC_QUARANTINE_MASK"][new].any()
        and not out.fields["QPE_ELIGIBLE_MASK"].any()
    )
    if mode == "audit":
        np.testing.assert_array_equal(out.fields["DBZH_QC"], old.fields["DBZH_QC"])
    else:
        assert np.isnan(out.fields["DBZH_QC"][new]).all()
        assert np.isnan(out.fields["DBZH_QC_DISPLAY"][new]).all()
        assert not out.fields["REFLECTIVITY_ELIGIBLE_FOR_CR"][new].any()
        assert (out.fields["QC_ACTION"][new] == 3).all()
    assert out.xqc_diagnostics["implementation_revision"] == "xqc-original-snr-carrier-20261004-v1:censor-budget-review-20261005-v1"
    np.testing.assert_array_equal(c.fields["DBZH"], out.fields["DBZH_RAW"])
    from rainpulse_algo.multiband.codec import decode_arrays
    from rainpulse_algo.multiband.xqc_v2.export import export_sweep

    exported = {}
    native_ref = export_sweep(out, v.metadata, exported)
    native = decode_arrays(
        exported[native_ref["native"]["object_path"]], maximum_bytes=256 * 1024**2
    )
    for name in [
        "XQC_SNR_CARRIER_MASK",
        "XQC_SNR_CARRIER_EXCESS_MASK",
        "XQC_SNR_CARRIER_UNKNOWN_MASK",
        "XQC_SNR_CARRIER_STATE",
        "DBZH_RAW",
        "DBZH_QC",
    ]:
        np.testing.assert_array_equal(native[name], out.fields[name])


def test_final_native_warning_survives_total_evidence_budget_refusal(monkeypatch):
    def refused(*args, **kwargs):
        raise ResourceLimit("no evidence room")

    monkeypatch.setattr("rainpulse_algo.multiband.xqc_v2.snr_carrier_integration.bounded", refused)
    c = cut()
    v, _ = fixture("empty")
    v.sweeps = [c]
    from unittest.mock import patch

    import rainpulse_algo.multiband.xqc_v2.pipeline as pipeline

    with (
        patch.object(pipeline, "evaluate_cut", return_value=parent(c)),
        patch(
            "rainpulse_algo.multiband.xqc_v2.complete_family_integration.extend",
            side_effect=lambda c, cfg, e: e,
        ),
    ):
        out = x_qc(v, station(cfg(mode="cr_only")), "b" * 64).sweeps[0]
    assert not out.fields["XQC_SNR_CARRIER_MASK"].any()
    assert (out.fields["XQC_SNR_CARRIER_STATE"] == 5).all()
    assert out.xqc_diagnostics["status"].startswith("DEGRADED_SNR_CARRIER")
