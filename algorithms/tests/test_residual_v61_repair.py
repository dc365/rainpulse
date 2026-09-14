"""Regression of proven V6 mechanisms, with explicit synthetic negative controls."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from rainpulse_algo.radar import qc_worker
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.fingerprints import array_digest
from rainpulse_algo.radar.qc_engine.narrow_local import (
    NarrowStage,
    local_widths,
    repaired_narrow_candidates,
)
from rainpulse_algo.radar.qc_engine.narrow_spike import narrow_candidates
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
from rainpulse_algo.radar.qc_engine.residual import residual_decision
from rainpulse_algo.radar.qc_engine.residual_association import peripheral_review

from .test_crossradar_v5 import FLAGS, ROOT
from .test_residual_v6 import V6, line_scene, v5_decision

V61 = ROOT / "configs/qc/fujian-qc-residual-v61.yaml"


def profile():
    return load_qc_profile(V61, FLAGS)


def crossing_scene(dr=250):
    n = line_scene(dr=dr)
    f = {k: v.copy() for k, v in n.fields.items()}
    # One original line is connected to an 8-degree, measured broad parent.
    parents = np.zeros(n.shape, bool)
    parents[5, f["DBZH"][5] > 0] = True
    crossed = (n.ranges >= 100000) & (n.ranges <= 135000)
    f["DBZH"][2:10, crossed] = 30
    parents[2:10, crossed] = True
    return replace(n, fields=f), parents, crossed


@pytest.mark.parametrize(
    "version",
    [
        "fujian-qc-opensource-v1",
        "fujian-qc-crossradar-v5",
        "fujian-qc-residual-v6",
        "fujian-qc-residual-v61",
    ],
)
def test_geometry_capability_loads_real_radar_config_for_every_open_source_version(
    tmp_path, monkeypatch, version
):
    cfg = ROOT / "configs/radars/fujian-20260828/z9591.yaml"
    (tmp_path / "z9591.yaml").write_bytes(cfg.read_bytes())
    monkeypatch.setenv("RAINPULSE_RADAR_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("RAINPULSE_ANCILLARY_CONFIG", raising=False)
    monkeypatch.delenv("RAINPULSE_ANCILLARY_ROOT", raising=False)
    audit = {}
    req = SimpleNamespace(payload=SimpleNamespace(radar_id="z9591"))
    beam, terrain, directory, _ = qc_worker._load_qc_geometry_resources(
        req,
        load_qc_profile(ROOT / f"configs/qc/{version}.yaml", FLAGS),
        audit=audit,
    )
    assert beam is not None and directory == tmp_path and terrain is None
    assert audit["beam_status"] == "loaded"
    assert audit["altitude_datum_status"] != "verified_egm2008"
    assert (
        audit["resources"]["current_radar_config"]["sha256"]
        == hashlib.sha256(cfg.read_bytes()).hexdigest()
    )


def test_geometry_unconfigured_invalid_and_mismatched_are_explicit(tmp_path, monkeypatch):
    req = SimpleNamespace(payload=SimpleNamespace(radar_id="z9591"))
    monkeypatch.delenv("RAINPULSE_RADAR_CONFIG_DIR", raising=False)
    audit = {}
    assert qc_worker._load_qc_geometry_resources(req, profile(), audit=audit)[0] is None
    assert audit["status"] == "radar_config_directory_unavailable"
    monkeypatch.setenv("RAINPULSE_RADAR_CONFIG_DIR", str(tmp_path))
    (tmp_path / "z9591.yaml").write_text("{}")
    assert qc_worker._load_qc_geometry_resources(req, profile(), audit=audit)[0] is None
    assert audit["status"] == "radar_config_invalid"


def test_frozen_v6_parameters_and_all_older_configs_unchanged():
    frozen = json.loads(
        (ROOT / "algorithms/tests/fixtures/qc_pre_v61_file_hashes.json").read_text()
    )
    for name, digest in frozen.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    p = profile().model_dump()
    p["pipeline_version"] = "qc-opensource-6.0.0"
    p["decision_version"] = "residual-v6"
    with pytest.raises(ValueError, match="6.1"):
        OpenSourceQCProfile.model_validate(p)


@pytest.mark.parametrize("dr", [250, 500, 1000])
def test_local_width_rescues_branch_not_whole_broad_object(dr):
    n, parents, crossed = crossing_scene(dr)
    empty = np.zeros(n.shape, bool)
    cfg = profile().residual
    old = narrow_candidates(n, cfg, polarimetric_risk=empty, protected=empty, parent_mask=parents)
    assert not old.arrays["V6_NARROW_CANDIDATE_MASK"].any()
    fixed = repaired_narrow_candidates(
        n, cfg, polarimetric_risk=empty, protected=empty, parent_mask=parents
    )
    tails = (n.ranges > 30000) & (n.ranges < 400000) & ~crossed
    assert fixed.arrays["V6_NARROW_CANDIDATE_MASK"][5, tails].mean() > 0.95
    assert not fixed.arrays["V6_NARROW_CANDIDATE_MASK"][2:10, crossed].any()
    assert (
        fixed.arrays["V61_NARROW_STAGE_REASON"][2:10, crossed] & int(NarrowStage.LOCAL_WIDTH)
    ).any()


@pytest.mark.parametrize("gap", [False, True])
def test_original_candidates_and_models_are_preserved(gap):
    n = line_scene(missing=gap)
    empty = np.zeros(n.shape, bool)
    cfg = profile().residual
    old = narrow_candidates(n, cfg, polarimetric_risk=empty, protected=empty)
    fixed = repaired_narrow_candidates(n, cfg, polarimetric_risk=empty, protected=empty)
    for key in ("V6_NARROW_CANDIDATE_MASK", "V6_NARROW_MODEL_MASK"):
        assert not np.any((old.arrays[key] == 1) & (fixed.arrays[key] == 0))
    assert not fixed.arrays["V6_NARROW_CANDIDATE_MASK"][~n.field_available["DBZH"]].any()


def test_local_width_seam_and_missing_edge_are_not_conflated():
    n = line_scene(dr=1000)
    nr = n.shape[0]
    n = replace(
        n,
        azimuth=np.arange(nr) * 30.0,
        full_ppi=True,
        gap_after=np.zeros(nr, bool),
        audit={**n.audit, "azimuth_spacing_deg": 30.0},
    )
    mask = np.zeros(n.shape, bool)
    mask[[-1, 0], :] = True
    w = local_widths(mask, n)
    assert (w[[-1, 0]] == 60).all()
    gaps = n.gap_after.copy()
    gaps[-1] = True
    assert (local_widths(mask, replace(n, gap_after=gaps))[[-1, 0]] == 30).all()
    assert np.isnan(w[1]).all()


@pytest.mark.parametrize("distance", [50000, 100000, 250000, 440000])
def test_footprint_adjacency_works_at_distance_but_keeps_original_parent(distance):
    n = line_scene()
    cfg = profile().residual
    gate = int(np.argmin(abs(n.ranges - distance)))
    n.fields["DBZH"][5:7, gate] = 25
    parent = np.zeros(n.shape, bool)
    parent[5, gate] = True
    empty = np.zeros(n.shape, bool)
    out = peripheral_review(
        n, cfg, confirmed=empty, model_anchor=parent, protected=empty, footprint=True
    )
    assert out["V6_PERIPHERAL_COMPATIBLE_MASK"][6, gate]
    assert out["V6_PARENT_RAY"][6, gate] == n.original_indices[5]
    assert out["V61_PERIPHERAL_FOOTPRINT_GAP_M"][6, gate] == 0
    assert out["V61_PERIPHERAL_CENTER_DISTANCE_M"][6, gate] == pytest.approx(
        distance * np.deg2rad(1)
    )


@pytest.mark.parametrize("barrier", ["missing_ray", "protected", "unobserved", "angle"])
def test_footprint_review_does_not_cross_barriers(barrier):
    n = line_scene()
    cfg = profile().residual
    gate = 999
    n.fields["DBZH"][5:7, gate] = 25
    anchor = np.zeros(n.shape, bool)
    anchor[5, gate] = True
    protect = np.zeros(n.shape, bool)
    if barrier == "missing_ray":
        n.gap_after[5] = True
    elif barrier == "protected":
        protect[6, gate] = True
    elif barrier == "unobserved":
        n.field_available["DBZH"][6, gate] = False
    else:
        n = replace(n, azimuth=n.azimuth * 2)
    out = peripheral_review(
        n,
        cfg,
        confirmed=anchor,
        model_anchor=np.zeros(n.shape, bool),
        protected=protect,
        footprint=True,
    )
    assert not out["V6_PERIPHERAL_COMPATIBLE_MASK"][6, gate]


@pytest.mark.parametrize("weather", [False, True])
def test_repaired_decisions_keep_monotonicity_and_weather_protection(weather):
    n, parents, _ = crossing_scene()
    old = v5_decision(n)
    old.arrays["RFI_OBJECT_ID"] = parents.astype("uint32")
    support = np.ones(n.shape) if weather else None
    six, _ = residual_decision(n, old, load_qc_profile(V6, FLAGS), weather_support=support)
    fixed, _ = residual_decision(n, old, profile(), weather_support=support)
    assert not np.any((six.arrays["QC_ACTION"] == 2) & (fixed.arrays["QC_ACTION"] != 2))
    assert not np.any(
        (six.arrays["QPE_ELIGIBLE_MASK"] == 0) & (fixed.arrays["QPE_ELIGIBLE_MASK"] == 1)
    )
    if weather:
        assert not fixed.arrays["V6_QUARANTINED_ADDITION_MASK"].any()
    else:
        assert fixed.arrays["V6_QUARANTINED_ADDITION_MASK"].any()
        assert not fixed.arrays["V6_CONFIRMED_ADDITION_MASK"].any()


@pytest.mark.parametrize("scene", ["flat_line", "small_strong", "weak_broad"])
def test_no_new_single_condition_weather_deletion(scene):
    n = line_scene()
    n.fields["DBZH"][:] = -20
    if scene == "flat_line":
        n.fields["DBZH"][5] = 30
    if scene == "small_strong":
        n.fields["DBZH"][5, 200:203] = 60
    if scene == "weak_broad":
        n.fields["DBZH"][:, 100:700] = 5
    fixed, _ = residual_decision(n, v5_decision(n), profile())
    assert not fixed.arrays["V6_CONFIRMED_ADDITION_MASK"].any()
    assert not fixed.arrays["V6_QUARANTINED_ADDITION_MASK"].any()


def test_semantic_array_digest_ignores_byteorder_but_not_missing_support():
    a = np.array([1.0, np.nan, -0.0], dtype="<f4")
    assert array_digest(a) == array_digest(a.astype(">f4"))
    assert array_digest(a) != array_digest(np.nan_to_num(a))


def test_precision_does_not_count_quarantine_as_confirmed_or_unlabeled_as_truth():
    from rainpulse_algo.radar.qc_engine.network_gate import counts, rates

    labels = np.array([[2, 2, 1, 0, 3]], "uint8")
    domain = np.ones(labels.shape, bool)
    rejected = np.array([[1, 0, 1, 1, 1]], bool)
    eligible = np.zeros(labels.shape, bool)
    result = rates(counts(labels, domain, rejected, eligible, 250))
    assert result["confirmed_precision_on_trusted_binary_labels"] == 0.5
    assert result["interference_recall"] == 0.5
    assert result["interference_withheld_rate"] == 1.0
    assert (
        rates(counts(labels, domain, np.zeros_like(rejected), eligible, 250))[
            "confirmed_precision_on_trusted_binary_labels"
        ]
        is None
    )


def test_directory_fingerprint_ignores_read_access_timestamp(tmp_path):
    import os

    from rainpulse_algo.radar.qc_engine.forensic_io import directory_digest

    p = tmp_path / "chunk"
    p.write_bytes(b"frozen chunk")
    first = directory_digest(tmp_path)
    st = p.stat()
    os.utime(p, ns=(1, st.st_mtime_ns))
    assert directory_digest(tmp_path) == first


def test_disabled_local_repairs_keep_exact_v6_actions_flags_and_values():
    n, parents, _ = crossing_scene()
    base = v5_decision(n)
    base.arrays["RFI_OBJECT_ID"] = parents.astype("uint32")
    p = profile()
    p = p.model_copy(
        update={
            "residual_repair": p.residual_repair.model_copy(
                update={"local_narrow_width": False, "footprint_peripheral": False}
            )
        }
    )
    original, _ = residual_decision(n, base, load_qc_profile(V6, FLAGS))
    repaired, _ = residual_decision(n, base, p)
    for key, value in original.arrays.items():
        np.testing.assert_array_equal(value, repaired.arrays[key])
    np.testing.assert_array_equal(original.flags, repaired.flags)
    np.testing.assert_array_equal(original.quality, repaired.quality)


def test_forensic_schema_matches_runtime_contract():
    from rainpulse_algo.radar.qc_engine.forensic_export import Manifest

    assert (
        json.loads((ROOT / "configs/schemas/qc-forensic-v1.schema.json").read_text())
        == Manifest.model_json_schema()
    )
