"""V5 analytic/mechanism tests; not measurements of real meteorological skill."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.crossradar_profile import CrossRadarConfig
from rainpulse_algo.radar.qc_engine.range_signature import range_signatures

from .test_qc_paper_algorithms import native

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ROOT / "configs/qc/flag-definitions-v2.yaml"
V5 = ROOT / "configs/qc/fujian-qc-crossradar-v5.yaml"


def long_scene(*, dr=250.0, ceiling=None, gaps=False, high_rho=True):
    ranges = np.arange(dr, 460001, dr)
    z = 20 + 20 * np.log10(ranges / 1000.0)
    if ceiling is not None:
        z = np.minimum(z, ceiling)
    z = np.broadcast_to(z, (12, len(ranges))).copy().astype("float32")
    if gaps:
        z[:, 100::50] = np.nan
    n = native()
    fields = {
        "DBZH": z,
        "RHOHV": np.full_like(z, 0.99 if high_rho else 0.3),
        "ZDR": np.ones_like(z),
        "PHIDP": np.full_like(z, 20),
        "SNR": np.full_like(z, 25),
    }
    return replace(
        n,
        ranges=ranges,
        azimuth=np.arange(12.0) + 20,
        elevation=np.full(12, 0.5),
        ray_time=np.zeros(12),
        original_indices=np.arange(12),
        geometry_good=np.ones(12, bool),
        gap_after=np.r_[np.zeros(11, bool), True],
        fields=fields,
        field_available={k: np.isfinite(v) for k, v in fields.items()},
        attrs={"radar_config_version": "synthetic-v1", "radar_id": "test"},
    )


@pytest.mark.parametrize("dr", [250.0, 500.0, 1000.0])
@pytest.mark.parametrize("ceiling", [None, 70.0])
def test_physical_distance_signature_works_across_resolutions(dr, ceiling):
    n = long_scene(dr=dr, ceiling=ceiling, gaps=True)
    before = {k: v.copy() for k, v in n.fields.items()}
    result = range_signatures(n, CrossRadarConfig())
    target = n.field_available["DBZH"] & (n.ranges[None, :] >= 30000)
    assert result.arrays["V5_RANGE_CANDIDATE_MASK"][target].mean() > 0.98
    assert not result.arrays["V5_RANGE_CANDIDATE_MASK"][~n.field_available["DBZH"]].any()
    for k in before:
        np.testing.assert_array_equal(before[k], n.fields[k])


@pytest.mark.parametrize("dbz", [0.0, 25.0, 50.0, 65.0, 70.0])
def test_flat_weather_and_high_dbz_alone_do_not_qualify(dbz):
    n = long_scene()
    f = dict(n.fields)
    f["DBZH"] = np.full(n.shape, dbz, "float32")
    n = replace(n, fields=f)
    result = range_signatures(n, CrossRadarConfig())
    assert not result.arrays["V5_RANGE_CANDIDATE_MASK"].any()


def test_v5_is_isolated_and_old_hashes_do_not_change():
    p = load_qc_profile(V5, FLAGS)
    assert p.pipeline_version == "qc-opensource-5.0.0"
    assert p.operational_eligible is False
    assert p.cross_radar.single_field_action == "quarantine"
    for name in (
        "fujian-qc-opensource-v1",
        "fujian-qc-rfi-objects-v2",
        "fujian-qc-rfi-objects-v3",
        "fujian-qc-paper-fusion-v4",
    ):
        old = load_qc_profile(ROOT / f"configs/qc/{name}.yaml", FLAGS)
        assert old.cross_radar is None


def _decision(n, cfg=None, weather=None):
    from rainpulse_algo.radar.qc_engine.crossradar import fuse_crossradar

    from .test_qc_paper_algorithms import evaluate

    p = load_qc_profile(V5, FLAGS)
    if cfg is not None:
        p = p.model_copy(update={"cross_radar": cfg})
    baseline = evaluate(n, weather=weather)
    return baseline, fuse_crossradar(
        n, baseline, range_signatures(n, p.cross_radar), p, weather_support=weather
    )


@pytest.mark.parametrize("ceiling", [None, 70.0])
def test_high_rho_strong_signature_withheld_not_claimed_confirmed(ceiling):
    n = long_scene(ceiling=ceiling)
    old, new = _decision(n)
    target = (n.ranges[None, :] >= 30000) * np.ones(n.shape, bool)
    assert old.arrays["QPE_ELIGIBLE_MASK"][target].all()
    assert new.arrays["RFI_QUARANTINE_MASK"][target].all()
    assert not new.arrays["V5_CONFIRMED_ADDITION_MASK"].any()
    assert not new.arrays["QPE_ELIGIBLE_MASK"][target].any()
    assert not (new.flags[target] & (1 << 15)).any()
    assert np.isnan(new.arrays["DBZH_USABLE"][target]).all()


def test_missing_snr_is_not_fabricated_and_not_a_disabled_range_detector():
    from rainpulse_algo.radar.qc_engine.crossradar import measurement_capability

    n = long_scene()
    for missing in [("SNR",), ("SNR", "PHIDP", "RHOHV", "ZDR")]:
        fields = {k: v for k, v in n.fields.items() if k not in missing}
        nn = replace(n, fields=fields, field_available={k: n.field_available[k] for k in fields})
        code, count, snr, reliable = measurement_capability(nn, CrossRadarConfig())
        assert not snr.any() and not reliable.any()
        assert np.max(code) == (2 if len(fields) > 1 else 1)
        # Range hypothesis never consumes SNR/RHOHV values.
        assert range_signatures(nn, CrossRadarConfig()).arrays["V5_RANGE_CANDIDATE_MASK"].any()


def test_verified_ceiling_is_bound_to_config_and_sweep_and_never_guessed():
    from rainpulse_algo.radar.qc_engine.crossradar_profile import VerifiedCeiling

    meta = VerifiedCeiling(
        radar_config_version="synthetic-v1",
        sweep="sweep_000",
        value_dbz=70.0,
        source_sha256="1" * 64,
        evidence_uri="synthetic:test-fixture",
        semantics="verified_reflectivity_output_ceiling",
    )
    cfg = CrossRadarConfig(verified_ceilings=(meta,))
    n = long_scene(ceiling=70)
    result = range_signatures(n, cfg)
    assert 2 in np.unique(result.arrays["V5_RANGE_MODEL_CODE"])
    wrong = replace(n, attrs={"radar_config_version": "different"})
    assert 2 not in np.unique(range_signatures(wrong, cfg).arrays["V5_RANGE_MODEL_CODE"])
    assert 3 in np.unique(range_signatures(wrong, cfg).arrays["V5_RANGE_MODEL_CODE"])
    with pytest.raises(ValueError):
        CrossRadarConfig(single_field_action="research_reject")
    with pytest.raises(ValueError):
        CrossRadarConfig(verified_ceilings=(meta, meta))


def test_receipt_bound_single_field_confirmation_never_confirms_unknown_plateau():
    cfg = CrossRadarConfig(
        single_field_action="research_reject", single_field_validation_sha256="2" * 64
    )
    n = long_scene()
    _, res = _decision(n, cfg)
    assert res.arrays["V5_CONFIRMED_ADDITION_MASK"].any()
    _, censored = _decision(long_scene(ceiling=70), cfg)
    assert not censored.arrays["V5_CONFIRMED_ADDITION_MASK"].any()
    _, mixed = _decision(n, cfg, weather=np.ones(n.shape))
    assert not mixed.arrays["V5_CONFIRMED_ADDITION_MASK"].any()
    assert mixed.arrays["V5_WEATHER_CONFLICT_MASK"].any()
    assert mixed.arrays["RFI_QUARANTINE_MASK"].any()


def test_long_hole_and_observed_incompatible_patch_are_not_swallowed():
    n = long_scene()
    fields = {k: v.copy() for k, v in n.fields.items()}
    fields["DBZH"][:, 400:700] = np.nan
    fields["DBZH"][:, 900:920] = 70.0
    n = replace(n, fields=fields, field_available={k: np.isfinite(v) for k, v in fields.items()})
    res = range_signatures(n, CrossRadarConfig()).arrays
    assert not res["V5_RANGE_CANDIDATE_MASK"][:, 400:700].any()
    assert not res["V5_RANGE_CANDIDATE_MASK"][:, 900:920].any()


def test_whole_azimuth_ring_and_linear_range_weather_are_not_radial_objects():
    n = long_scene()
    n = replace(
        n,
        azimuth=np.linspace(0, 360, 12, endpoint=False),
        full_ppi=True,
        gap_after=np.zeros(12, bool),
        audit={"azimuth_spacing_deg": 30.0},
    )
    assert not range_signatures(n, CrossRadarConfig()).arrays["V5_RANGE_CANDIDATE_MASK"].any()
    n = long_scene()
    f = dict(n.fields)
    f["DBZH"] = np.broadcast_to(25 + n.ranges / 10000, n.shape).astype("float32")
    assert (
        not range_signatures(replace(n, fields=f), CrossRadarConfig())
        .arrays["V5_RANGE_CANDIDATE_MASK"]
        .any()
    )


def test_gap_association_uses_measured_length_and_does_not_create_observations():
    from rainpulse_algo.radar.qc_engine.segments import associated_support

    seed = np.ones((1, 200), bool)
    observed = seed.copy()
    seed[:, 25::30] = False
    observed[:, 25::30] = False
    support, linked = associated_support(seed, observed, ~observed, 250, 12000, 1000, 0.15)
    assert np.array_equal(support, seed)
    assert not linked.any()
    # Long missing gap and an observed healthy gap both split rather than close.
    seed = np.zeros((1, 60), bool)
    seed[:, :20] = True
    seed[:, 40:] = True
    support, _ = associated_support(seed, np.ones_like(seed), ~seed, 250, 12000, 1000, 0.15)
    assert not support.any()


def test_v4_to_v5_runtime_and_zarr_validation_and_health(tmp_path):
    from datetime import UTC, datetime

    from rainpulse_algo.radar.qc import apply_basic_qc
    from rainpulse_algo.radar.qc_zarr import build_validated_qc_zarr_store, validate_qc_zarr_store

    from .test_radar_qc import synthetic_normalized_fixture

    n = long_scene(ceiling=70.0)
    objects = synthetic_normalized_fixture(
        n.fields["DBZH"],
        azimuth_deg=n.azimuth,
        range_m=n.ranges,
        moments={k: v for k, v in n.fields.items() if k != "DBZH"},
    )
    before = dict(objects)
    p4 = load_qc_profile(ROOT / "configs/qc/fujian-qc-paper-fusion-v4.yaml", FLAGS)
    p5 = load_qc_profile(V5, FLAGS)
    old = apply_basic_qc(objects, p4, created_at=datetime(2026, 8, 28, tzinfo=UTC))
    new = apply_basic_qc(objects, p5, created_at=datetime(2026, 8, 28, tzinfo=UTC))
    a, b = old.sweeps[0].optional_qc_fields, new.sweeps[0].optional_qc_fields
    assert np.array_equal(b["V5_BASELINE_REJECT_MASK"], a["QC_ACTION"] == 2)
    assert np.array_equal(b["V5_BASELINE_ELIGIBLE_MASK"], a["QPE_ELIGIBLE_MASK"])
    assert b["V5_RANGE_CANDIDATE_MASK"].any()
    out, check = build_validated_qc_zarr_store(
        objects, new, asset_id="v5-test", normalized_volume_uri="s3://test/input"
    )
    assert check["valid_gate_count"] > 0
    assert validate_qc_zarr_store(out) == check
    assert new.summary["sweeps"]["sweep_000"]["decision_funnel"]["rows"]
    assert objects == before
    # Mutation must be caught by downstream contract validation.
    import zarr
    from zarr.storage import MemoryStore

    store = MemoryStore()
    store.update(out)
    g = zarr.open_group(store=store, mode="a")
    g["sweep_000/V5_CONFIRMED_ADDITION_MASK"][:] = 1
    with pytest.raises(ValueError):
        validate_qc_zarr_store(dict(store))


def test_parameter_hashes_match_real_frozen_v4_baseline_implementation():
    import json
    from pathlib import Path

    expected = json.loads(
        (Path(__file__).parent / "fixtures/qc_v1_v4_frozen_hashes.json").read_text()
    )
    for name, digest in expected.items():
        actual = load_qc_profile(ROOT / f"configs/qc/{name}.yaml", FLAGS)
        assert actual.parameters_hash == digest


def test_nonuniform_range_is_refused_before_expert_and_numeric_plateau_is_not_slow_log_curve():
    n = long_scene()
    mode = range_signatures(n, CrossRadarConfig()).arrays["V5_RANGE_MODEL_CODE"]
    assert 1 in np.unique(mode) and 3 not in np.unique(mode)
    from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
    from rainpulse_algo.radar.qc_input import open_qc_input

    from .test_radar_qc import synthetic_normalized_fixture

    ranges = n.ranges.copy()
    ranges[30] += 10
    obj = synthetic_normalized_fixture(n.fields["DBZH"], azimuth_deg=n.azimuth, range_m=ranges)
    with pytest.raises(ValueError, match="nonuniform"):
        adapt_sweep(open_qc_input(obj).root, "sweep_000", load_qc_profile(V5, FLAGS))
