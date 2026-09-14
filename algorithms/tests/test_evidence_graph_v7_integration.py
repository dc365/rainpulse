"""V7 must pass the actual Worker serialization path, including quarantine."""
import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from .test_residual_v61_integration import build_case61, frozen, FLAGS, ROOT

V7 = ROOT / "configs/qc/fujian-qc-evidence-graph-v7.yaml"


def test_v7_worker_serializes_stage_a_and_action_audit(tmp_path, monkeypatch):
    _, _, _, client, request = build_case61(tmp_path)
    profile = load_qc_profile(V7, FLAGS)
    task = request.model_dump(mode="json")
    task["payload"].update(qc_profile=profile.profile_version,
                           qc_pipeline_version=profile.pipeline_version,
                           qc_profile_sha256=frozen(V7)["sha256"])
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(V7))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    result = _execute_basic_qc(RadarQCRequested.model_validate(task), client)
    store = MemoryStore()
    store.update(result.objects)
    root = zarr.open_group(store, mode="r")
    assert root.attrs["qc_pipeline_version"] == "qc-opensource-7.0.0"
    sweep = root["sweep_000"]
    assert np.any(sweep["V7_FIRST_DECIDER"][:] > 0)
    assert "V7_STAGE_A_DONOR_USABLE_MASK" in sweep
    assert "V7_GRAPH_REVIEW_MASK" in sweep
    observed = np.isfinite(sweep["DBZH_RAW"][:])
    eligible = sweep["QPE_ELIGIBLE_MASK"][:] == 1
    assert not np.any(eligible & ~observed)
    assert np.all(np.isnan(sweep["DBZH_USABLE"][:][~eligible]))
