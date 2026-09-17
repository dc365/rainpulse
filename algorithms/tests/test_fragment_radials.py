"""Interrupted raw-radial and polarimetric controls, not weather truth scores."""

from dataclasses import replace

import numpy as np

from rainpulse_algo.radar.qc_engine.fragment_radials import FragmentConfig, fragment_evidence
from rainpulse_algo.radar.qc_engine.narrow_spike import narrow_candidates

from .test_residual_v6 import config, line_scene


def scene():
    n = line_scene(dr=250, rho=0.7)
    f = {k: v.copy() for k, v in n.fields.items()}
    # 5 km measured fragments / 2 km missing: exceeds old 1.5 km link budget.
    f["DBZH"][5, np.arange(n.shape[1]) % 28 >= 20] = np.nan
    # Phase ramps: most adjacent steps <25 degrees, but appreciable dispersion.
    f["PHIDP"][5] = (np.arange(n.shape[1]) % 5) * 24
    f["ZDR"][5] = 0
    return replace(
        n,
        fields=f,
        field_available={k: np.isfinite(v) & np.isfinite(f["DBZH"]) for k, v in f.items()},
    )


def test_fragmented_low_amplitude_phase_route_missed_by_legacy():
    n = scene()
    zero = np.zeros(n.shape, bool)
    old = narrow_candidates(n, config().residual, polarimetric_risk=zero, protected=zero)
    assert not old.arrays["V6_NARROW_CANDIDATE_MASK"].any()
    e = fragment_evidence(n, FragmentConfig())
    domain = n.field_available["DBZH"] & (n.fields["DBZH"] > 0) & (n.ranges[None, :] > 30000)
    assert e["FRAGMENT_CANDIDATE_MASK"][domain].mean() > 0.8
    assert e["FRAGMENT_POL_CONFIRMED_MASK"][domain].mean() > 0.6
    assert not e["FRAGMENT_CANDIDATE_MASK"][~n.field_available["DBZH"]].any()


def test_uniform_weather_and_missing_shoulders_do_not_become_spikes():
    n = scene()
    f = {k: v.copy() for k, v in n.fields.items()}
    f["DBZH"][:] = 30
    n = replace(n, fields=f, field_available={k: np.isfinite(v) for k, v in f.items()})
    assert not fragment_evidence(n, FragmentConfig())["FRAGMENT_CANDIDATE_MASK"].any()
    f["DBZH"][:] = np.nan
    f["DBZH"][5] = 30
    n = replace(
        n,
        fields=f,
        field_available={k: np.isfinite(v) & np.isfinite(f["DBZH"]) for k, v in f.items()},
    )
    assert not fragment_evidence(n, FragmentConfig())["FRAGMENT_CANDIDATE_MASK"].any()


def test_smooth_phase_high_rho_and_low_snr_never_confirm():
    for field, value in [("PHIDP", 359), ("RHOHV", 0.99), ("SNR", 0)]:
        n = scene()
        n.fields[field][5] = value
        assert not fragment_evidence(n, FragmentConfig())["FRAGMENT_POL_CONFIRMED_MASK"].any()


def test_circular_phase_wrap_is_not_dispersion():
    n = scene()
    n.fields["PHIDP"][5] = np.where(np.arange(n.shape[1]) % 2, 359, 1)
    e = fragment_evidence(n, FragmentConfig())
    assert np.nanmax(e["FRAGMENT_PHASE_CIRCULAR_VARIANCE"]) < 0.001
    assert not e["FRAGMENT_POL_CONFIRMED_MASK"].any()


def test_action_is_monotone_and_raw_is_immutable():
    from rainpulse_algo.radar.qc_engine.fragment_radials import apply_fragment_decision

    from .test_residual_v6 import v5_decision

    n = scene()
    before = v5_decision(n)
    saved = n.fields["DBZH"].copy()
    after, stats = apply_fragment_decision(n, before, FragmentConfig(), config())
    confirmed = after.arrays["FRAGMENT_POL_CONFIRMED_MASK"] == 1
    assert confirmed.any()
    assert (after.arrays["QC_ACTION"][confirmed] == 2).all()
    assert not after.arrays["QPE_ELIGIBLE_MASK"][confirmed].any()
    assert not after.arrays["RFI_QUARANTINE_MASK"][confirmed].any()
    assert not np.isfinite(after.arrays["DBZH_USABLE"][confirmed]).any()
    np.testing.assert_equal(
        after.arrays["QC_ACTION"][~confirmed], before.arrays["QC_ACTION"][~confirmed]
    )
    np.testing.assert_equal(n.fields["DBZH"], saved)


def test_disabled_profile_hash_and_invalid_configuration():
    import hashlib
    import json

    import pytest

    from rainpulse_algo.radar.qc import load_qc_profile
    from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile

    from .test_crossradar_v5 import FLAGS, ROOT

    p = load_qc_profile(ROOT / "configs/qc/fujian-qc-evidence-graph-v7.yaml", FLAGS)
    assert "fragment_radials" not in p.model_dump()["evidence_graph"]
    raw = p.model_dump(mode="json")
    raw.pop("generalization", None)  # Absent 7.2 extension is not a frozen parameter.
    raw["context"].pop("split_radial_weather_support", None)
    assert (
        hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        == p.parameters_hash
    )
    enabled = p.model_dump(mode="json")
    enabled["evidence_graph"]["fragment_radials"] = FragmentConfig().model_dump(mode="json")
    assert OpenSourceQCProfile.model_validate(enabled).parameters_hash != p.parameters_hash
    with pytest.raises(ValueError):
        FragmentConfig(phase_window_gates=4)
    with pytest.raises(ValueError):
        FragmentConfig(shoulder_offsets_deg=(float("nan"),))


def test_geometry_gap_blocks_shoulders_and_source_order_restores():
    n = scene()
    n.gap_after[4] = True
    n.gap_after[5] = True
    assert not fragment_evidence(n, FragmentConfig())["FRAGMENT_CANDIDATE_MASK"].any()
    n = scene()
    permutation = np.arange(n.shape[0])[::-1]
    n = replace(n, original_indices=permutation)
    mask = fragment_evidence(n, FragmentConfig())["FRAGMENT_CANDIDATE_MASK"]
    np.testing.assert_equal(n.restore(mask)[permutation], mask)


def test_enabled_worker_serializes_experiment_and_keeps_raw(tmp_path, monkeypatch):
    import yaml
    import zarr
    from zarr.storage import MemoryStore

    from rainpulse_algo.radar.qc import load_qc_profile
    from rainpulse_algo.radar.qc_worker import _execute_basic_qc
    from rainpulse_algo.worker.domain_contracts import RadarQCRequested

    from .test_residual_v61_integration import FLAGS, ROOT, build_case61, frozen

    _, _, _, client, request = build_case61(tmp_path)
    base = load_qc_profile(ROOT / "configs/qc/fujian-qc-evidence-graph-v7.yaml", FLAGS)
    data = base.model_dump(mode="json")
    data["evidence_graph"]["fragment_radials"] = FragmentConfig(
        association_maximum_distance_m=4000
    ).model_dump(mode="json")
    path = tmp_path / "fragment.yaml"
    path.write_text(yaml.safe_dump(data))
    task = request.model_dump(mode="json")
    task["payload"].update(
        qc_profile=base.profile_version,
        qc_pipeline_version=base.pipeline_version,
        qc_profile_sha256=frozen(path)["sha256"],
    )
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(path))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    result = _execute_basic_qc(RadarQCRequested.model_validate(task), client)
    store = MemoryStore()
    store.update(result.objects)
    group = zarr.open_group(store, mode="r")["sweep_000"]
    assert "FRAGMENT_PHASE_CIRCULAR_VARIANCE" in group
    assert "FRAGMENT_ASSOCIATION_ID" in group
    confirmed = group["FRAGMENT_POL_CONFIRMED_MASK"][:] == 1
    assert not group["QPE_ELIGIBLE_MASK"][:][confirmed].any()
    assert np.all(group["QC_ACTION"][:][confirmed] == 2)
    # DBZH_QC stores retained measurements; renderer uses the separate action mask.
    np.testing.assert_equal(group["DBZH_RAW"][:], group["DBZH_QC"][:])


def test_frozen_anchor_links_fragment_but_not_gap_or_recursive_tail():
    from rainpulse_algo.radar.qc_engine.fragment_radials import associate_fragments

    n = line_scene(dr=250)
    anchor = np.zeros(n.shape, bool)
    anchor[5, 100:120] = True
    n.fields["DBZH"][5, 120:128] = np.nan
    n.field_available["DBZH"][5, 120:128] = False
    cfg = FragmentConfig(association_maximum_distance_m=4000)
    e = associate_fragments(n, anchor, np.zeros(n.shape, bool), cfg)
    linked = e["FRAGMENT_LINKED_MASK"] == 1
    assert linked[5, 128:136].all()
    assert not linked[5, 120:128].any()
    assert not linked[5, 136:].any()
    assert (e["FRAGMENT_PARENT_GATE"][5, 128:136] == 119).all()
    assert (e["FRAGMENT_ASSOCIATION_ID"][5, 128:136] > 0).all()


def test_association_preserves_weather_and_incompatible_measurements_as_review_only():
    from rainpulse_algo.radar.qc_engine.fragment_radials import associate_fragments

    n = line_scene(dr=250)
    anchor = np.zeros(n.shape, bool)
    anchor[5, 100:120] = True
    n.fields["DBZH"][5, 128:130] += 30
    e = associate_fragments(
        n, anchor, np.zeros(n.shape, bool), FragmentConfig(association_maximum_distance_m=4000)
    )
    assert not e["FRAGMENT_LINKED_MASK"][5, 128:130].any()
    assert e["FRAGMENT_LINKED_MASK"][5, 130]
    # Membership has no action array: each observed target needs independent evidence.
    assert "QC_ACTION" not in e


def test_linked_gate_needs_own_evidence_and_cross_conflict_withholds():
    from rainpulse_algo.radar.qc_engine.decision import Decision
    from rainpulse_algo.radar.qc_engine.fragment_radials import apply_fragment_decision

    from .test_residual_v6 import v5_decision

    n = line_scene(dr=250, rho=0.7)
    n.fields["PHIDP"][5] = (np.arange(n.shape[1]) % 5) * 24
    n.fields["DBZH"][5, 120:128] = np.nan
    n.field_available["DBZH"][5, 120:128] = False
    p = config()
    d = v5_decision(n)
    arrays = {k: v.copy() for k, v in d.arrays.items()}
    arrays["QC_ACTION"][:] = 0
    arrays["QC_ACTION"][5, 100:120] = 2
    arrays["QPE_ELIGIBLE_MASK"][:] = 1
    arrays["RFI_QUARANTINE_MASK"][:] = 0
    arrays["V5_RANGE_CANDIDATE_MASK"][:] = 0
    flags = np.zeros(n.shape, dtype=d.flags.dtype)
    flags[5, 100:120] = p.flag_masks["RADIAL_INTERFERENCE"]
    base = Decision(arrays, flags, np.ones(n.shape, dtype="float32"))
    cfg = FragmentConfig(association_maximum_distance_m=4000, minimum_observed_fraction=1)
    assert not fragment_evidence(n, cfg)["FRAGMENT_CANDIDATE_MASK"][5, 131]
    a, _ = apply_fragment_decision(n, base, cfg, p)
    assert a.arrays["QC_ACTION"][5, 131] == 2
    unknown, _ = apply_fragment_decision(n, base, cfg, p, cross_support=np.full(n.shape, np.nan))
    np.testing.assert_equal(unknown.arrays["QC_ACTION"], a.arrays["QC_ACTION"])
    assert not unknown.arrays["FRAGMENT_CROSS_SUPPORT_AVAILABLE_MASK"].any()
    b, _ = apply_fragment_decision(n, base, cfg, p, cross_support=np.ones(n.shape))
    assert b.arrays["QC_ACTION"][5, 131] == 1
    assert b.arrays["RFI_QUARANTINE_MASK"][5, 131] == 1
    assert b.arrays["QPE_ELIGIBLE_MASK"][5, 131] == 0
    n.fields["SNR"][5, 128:136] = 0
    c, _ = apply_fragment_decision(n, base, cfg, p)
    assert c.arrays["FRAGMENT_LINKED_MASK"][5, 131] == 1
    assert c.arrays["QC_ACTION"][5, 131] == 0


def test_range_term_repairs_model_without_relaxing_residual_threshold():
    from rainpulse_algo.radar.qc_engine.crossradar_profile import CrossRadarConfig
    from rainpulse_algo.radar.qc_engine.range_signature import _fit

    r = np.arange(20125, 460000, 250, dtype=float)
    z = 5 + 20 * np.log10(r / 1000) + 0.014 * r / 1000
    cfg = CrossRadarConfig()
    assert _fit(r, z, cfg, None)[1] == "shape_residual"
    fit, why = _fit(r, z, cfg, None, range_term_db_per_km=0.014)
    assert why is None and fit["p90"] < 0.001
    assert cfg.residual_p90_db == 2.5


def test_range_term_estimate_uses_two_ray_groups_and_rejects_disagreement():
    from rainpulse_algo.radar.qc_engine.fragment_radials import estimate_range_term

    n = line_scene(dr=250)
    snr = np.full(n.shape, 30.0, dtype="float32")
    z = (
        np.broadcast_to(5 + 20 * np.log10(n.ranges / 1000) + 0.014 * n.ranges / 1000, n.shape)
        .astype("float32")
        .copy()
    )
    n = replace(
        n,
        fields={**n.fields, "SNR": snr, "DBZH": z},
        field_available={
            **n.field_available,
            "SNR": np.ones(n.shape, bool),
            "DBZH": np.ones(n.shape, bool),
        },
    )
    term, record = estimate_range_term(n)
    assert abs(term - 0.014) < 0.0001
    n.fields["DBZH"][::2] += 0.01 * n.ranges[None, :] / 1000
    term, record = estimate_range_term(n)
    assert term is None and record["status"] == "inconsistent_ray_groups"


def test_range_candidate_only_quarantines_and_cross_weather_protects(monkeypatch):
    from types import SimpleNamespace

    from rainpulse_algo.radar.qc_engine import fragment_radials as module
    from rainpulse_algo.radar.qc_engine import range_signature

    from .test_residual_v6 import v5_decision

    n = line_scene()
    before = v5_decision(n)
    # Isolate arbitration: detector and paired-moment estimator tested separately.
    target = np.zeros(n.shape, dtype=bool)
    target[0, 150] = True
    before.arrays["QC_ACTION"][target] = 0
    before.arrays["QPE_ELIGIBLE_MASK"][target] = 1
    before.arrays["RFI_QUARANTINE_MASK"][target] = 0
    monkeypatch.setattr(
        module, "estimate_range_term", lambda _: (0.011, {"status": "measured_consistent"})
    )
    monkeypatch.setattr(
        range_signature,
        "range_signatures",
        lambda *a, **k: SimpleNamespace(
            arrays={
                "V5_RANGE_CANDIDATE_MASK": target.astype("uint8"),
                "V5_RANGE_MODEL_CODE": target.astype("uint8"),
                "V5_RANGE_RESIDUAL_P90_DB": np.zeros(n.shape),
            },
            summary={},
        ),
    )
    raw = n.fields["DBZH"].copy()
    for support, expected in [(np.nan, 0), (0.9, 1)]:
        after, _ = module.apply_fragment_decision(
            n,
            before,
            FragmentConfig(range_term_enabled=True),
            config(),
            cross_support=np.full(n.shape, support),
        )
        assert after.arrays["QPE_ELIGIBLE_MASK"][target].item() == expected
        assert after.arrays["QC_ACTION"][target].item() != 2
        assert after.arrays["RFI_QUARANTINE_MASK"][target].item() == 1 - expected
        np.testing.assert_equal(n.fields["DBZH"], raw)


def test_range_term_missing_snr_disables_estimation():
    from rainpulse_algo.radar.qc_engine.fragment_radials import estimate_range_term

    n = line_scene()
    n = replace(n, fields={k: v for k, v in n.fields.items() if k != "SNR"})
    term, record = estimate_range_term(n)
    assert term is None
    assert record["status"] == "snr_unavailable"
