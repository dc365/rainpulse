"""Real library/Worker tests on synthetic raw data, not acceptance on weather truth."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_worker import _completion_qc_summary, _execute_basic_qc
from rainpulse_algo.radar.qc_zarr import validate_qc_zarr_store
from rainpulse_algo.worker.domain_contracts import RadarQCRequested

from .test_generalization_p0p2 import P
from .test_residual_v61_integration import FLAGS, ROOT, build_case61, frozen


@pytest.mark.parametrize(
    "config,version",
    [
        (P, "qc-opensource-7.2.0"),
        (P.with_name("fujian-qc-generalization-edge.yaml"), "qc-opensource-7.2.1"),
        (P.with_name("fujian-qc-broad-source-v1.yaml"), "qc-opensource-7.3.0"),
        (P.with_name("fujian-qc-broad-source-range-v1.yaml"), "qc-opensource-7.3.1"),
        (P.with_name("fujian-qc-distance-polar-v1.yaml"), "qc-opensource-7.3.2"),
        (P.with_name("fujian-qc-radial-opening-v1.yaml"), "qc-opensource-7.3.3"),
        (P.with_name("fujian-qc-source-edge-v1.yaml"), "qc-opensource-7.3.4"),
        (P.with_name("fujian-qc-near-source-v1.yaml"), "qc-opensource-7.3.5"),
        (P.with_name("fujian-qc-near-sector-v1.yaml"), "qc-opensource-7.3.6"),
    ],
)
def test_actual_worker_p0p2_artifact_and_idempotency(tmp_path, monkeypatch, config, version):
    _, _, _, client, request = build_case61(tmp_path)
    profile = load_qc_profile(config, FLAGS)
    task = request.model_dump(mode="json")
    task["payload"].update(
        qc_profile=profile.profile_version,
        qc_pipeline_version=profile.pipeline_version,
        qc_profile_sha256=frozen(config)["sha256"],
    )
    request = RadarQCRequested.model_validate(task)
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(config))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    first = _execute_basic_qc(request, client)
    second = _execute_basic_qc(request, client)
    assert first.objects == second.objects
    assert validate_qc_zarr_store(first.objects)["sweep_count"] >= 1
    store = MemoryStore()
    store.update(first.objects)
    root = zarr.open_group(store, mode="r")
    g = root["sweep_000"]
    observed = np.isfinite(g["DBZH_RAW"][:])
    assert root.attrs["qc_pipeline_version"] == version
    for k in (
        "P2_RANGE_MEASUREMENT_MASK",
        "P2_PROPOSAL_MASK",
        "P2_ADDED_QUARANTINE_MASK",
        "P2_ADMIN_PENALTY_REMOVED_MASK",
    ):
        assert not np.any((g[k][:] == 1) & ~observed)
    summary = json.loads(first.objects["qc/summary.json"])
    assert not summary["health_facets"]["operational_eligible"]
    assert "generalization_summary" in summary
    assert len(json.dumps(first.diagnostics).encode()) < 65536


def test_completion_summary_excludes_objects_and_keeps_review_gate():
    summary = {
        "qc_pipeline_version": "qc-opensource-7.2.0",
        "operational_eligible": False,
        "health_facets": {"admission_ready": False},
        "generalization_summary": {"review_required": True},
        "sweeps": {
            "sweep_000": {"v7_graph": {"generalization": {"measurement_routes": [{}] * 200000}}}
        },
    }
    s = _completion_qc_summary(summary)
    assert len(json.dumps(s).encode()) < 4096
    assert s["generalization_summary"]["review_required"]
    assert "sweeps" not in s and s["summary_object_path"] == "qc/summary.json"


def test_old_yaml_and_semantic_parameters_unchanged():
    # Frozen reviewed set, not a self-updating expectation or git dependency.
    expected = json.loads(
        (Path(__file__).parent / "fixtures/generalization_baseline_hashes.json").read_text()
    )
    for name, row in expected.items():
        p = ROOT / name
        assert hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"]
        if row["parameters_hash"] is not None:
            assert load_qc_profile(p, FLAGS).parameters_hash == row["parameters_hash"]


def test_action_validator_rejects_unproven_or_unmeasured_additions():
    import pytest

    from rainpulse_algo.radar.qc_engine.evidence_validation import validate_evidence_fields

    shape = (2, 5)
    observed = np.ones(shape, bool)
    observed[0, 4] = False
    fields = {
        k: np.zeros(shape, "uint8")
        for k in (
            "V7_BASELINE_REJECT_MASK",
            "V7_BASELINE_QUARANTINE_MASK",
            "V7_BASELINE_ELIGIBLE_MASK",
            "QPE_ELIGIBLE_MASK",
            "RFI_QUARANTINE_MASK",
            "P2_ADDED_QUARANTINE_MASK",
            "P2_BASELINE_QUARANTINE_MASK",
            "P2_BASELINE_ELIGIBLE_MASK",
            "P2_PROPOSAL_MASK",
            "OC1_ADDED_QUARANTINE_MASK",
            "OC1_BASELINE_QUARANTINE_MASK",
        )
    }
    for key in ("V7_BASELINE_ELIGIBLE_MASK", "QPE_ELIGIBLE_MASK", "P2_BASELINE_ELIGIBLE_MASK"):
        fields[key][:] = observed
    reject = np.zeros(shape, bool)
    assert not validate_evidence_fields(fields, observed, reject).any()
    # An extra quarantine without a recorded measured proposal must fail.
    fields["P2_ADDED_QUARANTINE_MASK"][0, 1] = 1
    fields["RFI_QUARANTINE_MASK"][0, 1] = 1
    fields["QPE_ELIGIBLE_MASK"][0, 1] = 0
    with pytest.raises(ValueError, match="measured candidate"):
        validate_evidence_fields(fields, observed, reject)
    fields["P2_PROPOSAL_MASK"][0, 1] = 1
    assert validate_evidence_fields(fields, observed, reject)[0, 1]
    fields["P2_ADDED_QUARANTINE_MASK"][0, 4] = 1
    fields["RFI_QUARANTINE_MASK"][0, 4] = 1
    fields["P2_PROPOSAL_MASK"][0, 4] = 1
    with pytest.raises(ValueError, match="measured candidate"):
        validate_evidence_fields(fields, observed, reject)


def test_original_broad_source_parameters_frozen():
    assert (
        load_qc_profile(P.with_name("fujian-qc-broad-source-v1.yaml"), FLAGS).parameters_hash
        == "909697df6bb65665e067c4eb9ebbc9c8cff279c93ee665eef9426ca1959a9ca2"
    )
