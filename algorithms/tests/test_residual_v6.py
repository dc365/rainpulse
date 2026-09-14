"""V6 engineering mechanisms and explicit negative controls, not real skill scores."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_engine.crossradar import fuse_crossradar
from rainpulse_algo.radar.qc_engine.narrow_spike import narrow_candidates
from rainpulse_algo.radar.qc_engine.polar_objects import gate_area_km2, label_polar
from rainpulse_algo.radar.qc_engine.range_signature import range_signatures
from rainpulse_algo.radar.qc_engine.residual import residual_decision
from rainpulse_algo.radar.qc_engine.residual_association import peripheral_review
from rainpulse_algo.radar.qc_engine.residual_profile import ResidualConfig
from rainpulse_algo.radar.qc_engine.speckle_review import speckle_candidates
from rainpulse_algo.radar.qc_zarr import build_validated_qc_zarr_store, validate_qc_zarr_store

from .test_crossradar_v5 import FLAGS, ROOT, V5, long_scene
from .test_qc_paper_algorithms import evaluate
from .test_radar_qc import synthetic_normalized_fixture

V6 = ROOT / "configs/qc/fujian-qc-residual-v6.yaml"


def config(**updates):
    p = load_qc_profile(V6, FLAGS)
    return p.model_copy(update={"residual": p.residual.model_copy(update=updates)})


def line_scene(*, dr=250, noise=0, rho=0.99, missing=False, short=False, remote=False):
    n = long_scene(dr=dr)
    f = {k: v.copy() for k, v in n.fields.items()}
    z = np.full(n.shape, -20.0, "float32")
    chosen = (n.ranges >= (220000 if remote else 15000)) & (
        n.ranges <= (40000 if short else 440000)
    )
    rng = np.random.default_rng(319)
    z[5, chosen] = -15 + 20 * np.log10(n.ranges[chosen] / 1000) + rng.normal(0, noise, chosen.sum())
    if missing:
        z[5, 150::30] = np.nan
    f["DBZH"] = z
    f["RHOHV"][:] = rho
    if rho < 0.8:
        f["ZDR"][5, chosen] = 9
        f["PHIDP"][5, chosen] = np.where(np.arange(chosen.sum()) % 2, 140, 0)
    return replace(
        n, fields=f, field_available={k: np.isfinite(v) & np.isfinite(z) for k, v in f.items()}
    )


def v5_decision(n):
    p = load_qc_profile(V5, FLAGS)
    return fuse_crossradar(n, evaluate(n), range_signatures(n, p.cross_radar), p)


def test_v6_profile_isolated_and_all_frozen_identity_preserved():
    assert load_qc_profile(V6, FLAGS).residual is not None
    assert load_qc_profile(V5, FLAGS).residual is None
    expected = json.loads(
        (Path(__file__).parent / "fixtures/qc_v1_v4_frozen_hashes.json").read_text()
    )
    for name, digest in expected.items():
        assert load_qc_profile(ROOT / f"configs/qc/{name}.yaml", FLAGS).parameters_hash == digest
    from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile

    p = config().model_dump()
    p["pipeline_version"] = "qc-opensource-5.0.0"
    with pytest.raises(ValueError):
        OpenSourceQCProfile.model_validate(p)


@pytest.mark.parametrize("dr", [250, 500, 1000])
def test_inlier_identity_is_not_destroyed_by_short_measured_outliers(dr):
    n = long_scene(dr=dr)
    z = n.fields["DBZH"].copy()
    z[:, int(25000 / dr) :: int(25000 / dr)] += 5
    n = replace(n, fields={**n.fields, "DBZH": z})
    p = config()
    old = range_signatures(n, p.cross_radar)
    fixed = range_signatures(n, p.cross_radar, association=p.residual)
    assert not old.arrays["V5_RANGE_CANDIDATE_MASK"].any()
    assert fixed.arrays["V5_RANGE_CANDIDATE_MASK"].mean() > 0.85
    link = fixed.arrays["V6_RANGE_LINKED_REVIEW_MASK"] == 1
    assert link.any()
    assert not fixed.arrays["V5_RANGE_CANDIDATE_MASK"][link].any()
    assert (fixed.arrays["V6_RANGE_LINK_PARENT_ID"][link] > 0).all()


def test_healthy_measured_gaps_block_range_identity():
    n = long_scene()
    z = n.fields["DBZH"].copy()
    z[:, 100::100] += 5
    n = replace(n, fields={**n.fields, "DBZH": z})
    protected = np.zeros(n.shape, bool)
    protected[:, 100::100] = True
    p = config()
    out = range_signatures(n, p.cross_radar, association=p.residual, protected=protected)
    assert not out.arrays["V5_RANGE_CANDIDATE_MASK"].any()


@pytest.mark.parametrize("dr", [250, 500, 1000])
@pytest.mark.parametrize("missing", [False, True])
def test_weak_narrow_interrupted_line_has_native_candidate(dr, missing):
    n = line_scene(dr=dr, missing=missing)
    p = config()
    e = narrow_candidates(
        n, p.residual, polarimetric_risk=np.zeros(n.shape, bool), protected=np.zeros(n.shape, bool)
    )
    domain = n.field_available["DBZH"] & (n.fields["DBZH"] > 0)
    assert e.arrays["V6_NARROW_CANDIDATE_MASK"][domain].mean() > 0.9
    assert not e.arrays["V6_NARROW_CANDIDATE_MASK"][~n.field_available["DBZH"]].any()
    assert e.arrays["V6_NARROW_MODEL_MASK"].any()


@pytest.mark.parametrize("kind", ["short", "remote"])
def test_short_and_remote_line_are_not_forced_through_long_strong_model(kind):
    n = line_scene(rho=0.3, **{kind: True})
    risk = n.fields["RHOHV"] < 0.8
    e = narrow_candidates(
        n, ResidualConfig(), polarimetric_risk=risk, protected=np.zeros(n.shape, bool)
    )
    assert e.arrays["V6_NARROW_CANDIDATE_MASK"].any()
    assert (e.arrays["V6_NARROW_TYPE"] == (3 if kind == "short" else 4)).any()


def test_rough_narrow_line_not_subject_to_smooth_axial_entry():
    n = line_scene(noise=5, rho=0.3)
    e = narrow_candidates(
        n,
        ResidualConfig(),
        polarimetric_risk=n.fields["RHOHV"] < 0.8,
        protected=np.zeros(n.shape, bool),
    )
    assert e.arrays["V6_NARROW_CANDIDATE_MASK"].sum() > 1000


def test_shape_hypothesis_is_quarantine_not_confirmation():
    n = line_scene()
    old = v5_decision(n)
    new, summary = residual_decision(n, old, config())
    target = n.fields["DBZH"] > 0
    assert new.arrays["V6_QUARANTINED_ADDITION_MASK"][target].any()
    assert not new.arrays["V6_CONFIRMED_ADDITION_MASK"].any()
    assert not new.arrays["QPE_ELIGIBLE_MASK"][target].any()
    assert summary["modules"]["narrow"]["object_count"] > 0
    np.testing.assert_array_equal(n.fields["DBZH"], line_scene().fields["DBZH"])


def test_coherent_high_correlation_weather_support_protects_additional_line_actions():
    n = line_scene()
    old = v5_decision(n)
    new, _ = residual_decision(n, old, config(), weather_support=np.ones(n.shape))
    assert not new.arrays["V6_QUARANTINED_ADDITION_MASK"].any()
    assert not new.arrays["V6_CONFIRMED_ADDITION_MASK"].any()


def test_flat_narrow_rain_band_without_measurement_anomaly_is_not_automatically_deleted():
    n = line_scene()
    z = n.fields["DBZH"].copy()
    z[5, :] = 25
    n = replace(n, fields={**n.fields, "DBZH": z})
    old = v5_decision(n)
    new, _ = residual_decision(n, old, config())
    assert not new.arrays["V6_CONFIRMED_ADDITION_MASK"].any()
    assert not new.arrays["V6_QUARANTINED_ADDITION_MASK"].any()


def test_large_clean_weather_area_and_small_strong_cell_survive():
    n = line_scene()
    z = np.full(n.shape, -20.0, "float32")
    z[:, 200:350] = 25
    z[5, 700] = 50
    n = replace(n, fields={**n.fields, "DBZH": z})
    old = v5_decision(n)
    new, _ = residual_decision(n, old, config())
    assert not new.arrays["V6_CONFIRMED_ADDITION_MASK"].any()
    assert not new.arrays["V6_QUARANTINED_ADDITION_MASK"].any()


def test_unknown_platform_not_promoted_to_confirmation():
    n = long_scene(ceiling=70, high_rho=False)
    f = {k: v.copy() for k, v in n.fields.items()}
    f["ZDR"][:] = 9
    f["PHIDP"][:, ::2] = 140
    n = replace(n, fields=f)
    out, _ = residual_decision(n, v5_decision(n), config())
    unknown = out.arrays["V6_RANGE_MODEL_CODE"] == 3
    assert unknown.any()
    assert not out.arrays["V6_CONFIRMED_ADDITION_MASK"][unknown].any()


def test_range_link_outliers_do_not_become_confirmed_by_object_membership():
    n = long_scene()
    z = n.fields["DBZH"].copy()
    z[:, 100::100] += 5
    n = replace(n, fields={**n.fields, "DBZH": z})
    out, _ = residual_decision(n, v5_decision(n), config())
    linked = out.arrays["V6_RANGE_LINKED_REVIEW_MASK"] == 1
    assert linked.any()
    assert not out.arrays["V6_CONFIRMED_ADDITION_MASK"][linked].any()


def test_peripheral_is_one_frozen_pass_with_trace_and_missing_barrier():
    n = line_scene()
    source = np.zeros(n.shape, bool)
    source[5, 400] = True
    cfg = ResidualConfig(peripheral_range_m=1000, peripheral_angle_deg=0)
    out = peripheral_review(
        n,
        cfg,
        confirmed=source,
        model_anchor=np.zeros(n.shape, bool),
        protected=np.zeros(n.shape, bool),
    )
    chosen = out["V6_PERIPHERAL_COMPATIBLE_MASK"] == 1
    assert chosen.sum() == 8
    assert not chosen[5, 405]
    assert (out["V6_PARENT_GATE"][chosen] == 400).all()
    z = n.fields["DBZH"].copy()
    z[5, 402] = np.nan
    n = replace(
        n,
        fields={**n.fields, "DBZH": z},
        field_available={**n.field_available, "DBZH": np.isfinite(z)},
    )
    out = peripheral_review(
        n,
        cfg,
        confirmed=source,
        model_anchor=np.zeros(n.shape, bool),
        protected=np.zeros(n.shape, bool),
    )
    assert not out["V6_PERIPHERAL_COMPATIBLE_MASK"][5, 402:405].any()


def test_eligible_loss_is_not_a_source_anchor():
    n = line_scene()
    old = v5_decision(n)
    old.arrays["QPE_ELIGIBLE_MASK"][:] = 0
    new, _ = residual_decision(
        n, old, config(repair_range_links=False, narrow_enabled=False, speckle_enabled=False)
    )
    assert not new.arrays["V6_PERIPHERAL_REVIEW_MASK"].any()


@pytest.mark.parametrize("full", [False, True])
def test_object_labelling_seam_has_true_topology(full):
    n = line_scene()
    n = replace(
        n,
        azimuth=np.arange(12) * 30.0,
        full_ppi=full,
        gap_after=np.r_[np.zeros(11, bool), not full],
    )
    mask = np.zeros(n.shape, bool)
    mask[0, 10:20] = True
    mask[-1, 10:20] = True
    out, count = label_polar(mask, n)
    assert count == (1 if full else 2)
    assert (out[0, 15] == out[-1, 15]) == full


def test_physical_area_increases_with_range():
    n = line_scene()
    area = gate_area_km2(n)
    assert area[5, 800] > area[5, 80] * 9
    assert np.isfinite(area).all()


def test_small_weak_object_without_raw_noise_evidence_is_not_deleted():
    n = line_scene()
    z = np.full(n.shape, -20.0, "float32")
    z[5, 400] = 10
    n = replace(n, fields={**n.fields, "DBZH": z})
    out, _ = speckle_candidates(
        n,
        ResidualConfig(),
        baseline_eligible=np.ones(n.shape, bool),
        protected=np.zeros(n.shape, bool),
        pol_bad=np.zeros(n.shape, bool),
        low_snr=np.zeros(n.shape, bool),
    )
    assert not out["V6_SPECKLE_CANDIDATE_MASK"].any()


def test_speckle_requires_raw_neighbour_observability_not_new_blank_space():
    n = line_scene()
    z = np.full(n.shape, -20.0, "float32")
    z[5, 400] = 10
    n = replace(n, fields={**n.fields, "DBZH": z})
    noise = np.zeros(n.shape, bool)
    noise[5, 400] = True
    out, _ = speckle_candidates(
        n,
        ResidualConfig(),
        baseline_eligible=np.ones(n.shape, bool),
        protected=np.zeros(n.shape, bool),
        pol_bad=np.zeros(n.shape, bool),
        low_snr=noise,
    )
    assert out["V6_SPECKLE_CANDIDATE_MASK"][5, 400]
    # Surrounding ORIGINAL observations become missing, not clear air.
    good = np.ones(n.shape, bool)
    good[4:7, 397:404] = False
    good[5, 400] = True
    n = replace(n, field_available={**n.field_available, "DBZH": good})
    out, _ = speckle_candidates(
        n,
        ResidualConfig(),
        baseline_eligible=np.ones(n.shape, bool),
        protected=np.zeros(n.shape, bool),
        pol_bad=np.zeros(n.shape, bool),
        low_snr=noise,
    )
    assert not out["V6_SPECKLE_CANDIDATE_MASK"].any()


def test_all_disabled_is_exact_v5_decision_not_a_new_algorithm():
    n = line_scene()
    old = v5_decision(n)
    new, _ = residual_decision(
        n,
        old,
        config(
            repair_range_links=False,
            narrow_enabled=False,
            association_enabled=False,
            speckle_enabled=False,
        ),
    )
    for name in old.arrays:
        np.testing.assert_array_equal(new.arrays[name], old.arrays[name])
    np.testing.assert_array_equal(old.flags, new.flags)


@pytest.mark.parametrize("degraded", [False, True])
def test_actual_library_worker_core_and_zarr_contract(degraded):
    n = line_scene(dr=1000)
    obj = synthetic_normalized_fixture(
        n.fields["DBZH"],
        azimuth_deg=n.azimuth,
        range_m=n.ranges,
        moments={k: v for k, v in n.fields.items() if k != "DBZH"},
    )
    if degraded:
        health = json.loads(obj["health/summary.json"])
        health["health"] = "DEGRADED"
        obj["health/summary.json"] = json.dumps(health).encode()
    before = dict(obj)
    when = datetime(2026, 8, 28, tzinfo=UTC)
    old = apply_basic_qc(obj, load_qc_profile(V5, FLAGS), created_at=when)
    new = apply_basic_qc(obj, config(), created_at=when)
    a, b = old.sweeps[0].optional_qc_fields, new.sweeps[0].optional_qc_fields
    np.testing.assert_array_equal(a["QPE_ELIGIBLE_MASK"], b["V6_BASELINE_ELIGIBLE_MASK"])
    np.testing.assert_array_equal(a["QC_ACTION"] == 2, b["V6_BASELINE_REJECT_MASK"] == 1)
    assert new.summary["sweeps"]["sweep_000"]["residual_v6"]["modules"]
    bundle, proof = build_validated_qc_zarr_store(
        obj, new, asset_id="v6-test", normalized_volume_uri="s3://test/native"
    )
    assert proof == validate_qc_zarr_store(bundle)
    assert before == obj


def test_validation_catches_unaccounted_restoration():
    import zarr
    from zarr.storage import MemoryStore

    n = line_scene(dr=1000)
    obj = synthetic_normalized_fixture(
        n.fields["DBZH"],
        azimuth_deg=n.azimuth,
        range_m=n.ranges,
        moments={k: v for k, v in n.fields.items() if k != "DBZH"},
    )
    out = apply_basic_qc(obj, config())
    bundle, _ = build_validated_qc_zarr_store(
        obj, out, asset_id="test", normalized_volume_uri="s3://test"
    )
    store = MemoryStore()
    store.update(bundle)
    root = zarr.open_group(store=store, mode="a")
    root["sweep_000/V6_BASELINE_REJECT_MASK"][:] = 1
    with pytest.raises(ValueError):
        validate_qc_zarr_store(dict(store))


def test_disabling_v6_preserves_quality_and_flags_not_only_actions():
    n = line_scene()
    old = v5_decision(n)
    new, _ = residual_decision(
        n,
        old,
        config(
            repair_range_links=False,
            narrow_enabled=False,
            association_enabled=False,
            speckle_enabled=False,
        ),
    )
    np.testing.assert_array_equal(old.quality, new.quality)
    np.testing.assert_array_equal(old.flags, new.flags)


def test_native_reference_does_not_invent_results_when_not_executed():
    from rainpulse_algo.radar.qc_engine.native_bropo import native_emitters

    n = line_scene()
    result = native_emitters(replace(n, full_ppi=False))
    assert result["status"] == "not_executed_unsupported_native_geometry"
    assert result["masks"] is None and result["raw_scores"] is None
    # No mocked native backend: this environment actually lacks the extensions.
    import importlib.util

    if importlib.util.find_spec("_ropogenerator") is None:
        z = np.tile(n.fields["DBZH"][0], (360, 1))
        m = replace(
            n,
            fields={k: np.tile(v[0], (360, 1)) for k, v in n.fields.items()},
            field_available={k: np.ones(z.shape, bool) for k in n.fields},
            azimuth=np.arange(360, dtype=float),
            elevation=np.full(360, 0.5),
            original_indices=np.arange(360),
            full_ppi=True,
            geometry_good=np.ones(360, bool),
            gap_after=np.zeros(360, bool),
            audit={**n.audit, "azimuth_spacing_deg": 1.0},
        )
        result = native_emitters(m)
        assert result["status"] == "not_executed_native_dependencies"
        assert result["masks"] is None
    with pytest.raises(ValueError):
        native_emitters(n, emitter_args=(-10, 0))
