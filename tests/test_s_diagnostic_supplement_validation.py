"""A new image generation must preserve contributors and use causal current QC."""
import copy
import importlib.util
import io
from pathlib import Path
import struct
import zlib

import pytest


def module():
    path = Path(__file__).parents[1] / "scripts/audit_s_refresh_publication.py"
    spec = importlib.util.spec_from_file_location("supplement_audit", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def scene():
    job = "00000000-0000-0000-0000-000000000003"
    analysis = "00000000-0000-0000-0000-000000000001"
    scan = "00000000-0000-0000-0000-000000000002"
    uri = f"s3://rainpulse/radar/qc/s2/{scan}/inputs/{job}/volume.zarr"
    original = dict(analysis_id=analysis, analysis_time="2026-08-28T00:12:00Z",
                    grid_id="grid", input_uri="qpe/analysis.zarr", renderer_version="renderer",
                    diagnostic_config_version="config", flag_definition_version="flags",
                    output_prefix=f"s3://rainpulse/diagnostics/{analysis}/renderer/",
                    radar_inputs=[dict(radar_id="s1", scan_id="contributor", qc_uri="current")])
    replacement = copy.deepcopy(original)
    replacement["output_prefix"] += f"station-supplements/{job}/"
    replacement["radar_inputs"].append(dict(radar_id="s2", scan_id=scan, qc_uri=uri))
    row = dict(scan_id=scan, radar_id="s2", radar_band="S", qc_uri=uri,
               normalized_uri="native", volume_end_time="2026-08-28T00:10:00Z")
    qc = dict(job_id=job, status="SUCCEEDED", request_payload=dict(job_id=job, payload=dict(
        scan_id=scan, radar_id="s2", input_uri="native", qc_profile_sha256="profile",
        output_prefix=uri.removesuffix("volume.zarr"))))
    return original, replacement, {scan: row}, {job: qc}


def verify(values):
    original, replacement, rows, jobs = values
    return module().validate_station_supplement(
        original, replacement, {"s2"}, rows, jobs, "profile")


def test_accepts_same_contributors_and_causal_current_native_qc():
    values = scene()
    assert verify(values) == values[1]["radar_inputs"][1:]


@pytest.mark.parametrize("fault", [
    "missing_contributor", "old_contributor", "duplicate_station", "unapproved_station",
    "different_analysis", "different_renderer", "unrelated_output", "invalid_revision",
    "future", "stale", "not_s", "old_native", "old_profile", "wrong_raw",
    "failed_qc", "wrong_qc_envelope", "wrong_qc_output",
])
def test_rejects_lineage_or_eligibility_changes(fault):
    original, new, rows, jobs = values = scene()
    row = next(iter(rows.values()))
    qc = next(iter(jobs.values()))
    if fault == "missing_contributor":
        new["radar_inputs"].pop(0)
    elif fault == "old_contributor":
        new["radar_inputs"][0]["qc_uri"] = "old"
    elif fault == "duplicate_station":
        new["radar_inputs"].append(copy.deepcopy(new["radar_inputs"][-1]))
    elif fault == "unapproved_station":
        new["radar_inputs"][-1]["radar_id"] = "other"
    elif fault == "different_analysis":
        new["input_uri"] = "another-analysis"
    elif fault == "different_renderer":
        new["renderer_version"] = "unknown-renderer"
    elif fault == "unrelated_output":
        new["output_prefix"] = "s3://rainpulse/unrelated/"
    elif fault == "invalid_revision":
        new["output_prefix"] = new["output_prefix"].rsplit("/", 2)[0] + "/not-uuid/"
    elif fault == "future":
        row["volume_end_time"] = "2026-08-28T00:12:01Z"
    elif fault == "stale":
        row["volume_end_time"] = "2026-08-27T23:59:59Z"
    elif fault == "not_s":
        row["radar_band"] = "X"
    elif fault == "old_native":
        row["qc_uri"] = "newer-than-requested"
    elif fault == "old_profile":
        qc["request_payload"]["payload"]["qc_profile_sha256"] = "old"
    elif fault == "wrong_raw":
        qc["request_payload"]["payload"]["input_uri"] = "other-native"
    elif fault == "failed_qc":
        qc["status"] = "FAILED"
    elif fault == "wrong_qc_envelope":
        qc["request_payload"]["job_id"] = "another-job"
    elif fault == "wrong_qc_output":
        qc["request_payload"]["payload"]["output_prefix"] = "other/"
    with pytest.raises((ValueError, KeyError)):
        verify(values)


@pytest.mark.parametrize("fault", [None, "changed_pixels", "mixed_generation"])
def test_supplement_publication_preserves_original_images(monkeypatch, fault):
    path = Path(__file__).with_name("test_s_refresh_publication_audit.py")
    spec = importlib.util.spec_from_file_location("existing_audit_cases", path)
    cases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cases)
    audit = module()
    plan, state, _, cache = cases.runtime_fixture(audit, monkeypatch)
    detail = audit.fetch_json("unused")
    old_job = state["jobs"][-1]["job_id"]
    new_job = "00000000-0000-0000-0000-000000000099"
    extra_scan = "00000000-0000-0000-0000-000000000088"
    for panel in detail["panels"]:
        for frame in panel["frames"]:
            frame["image_url"] = frame["image_url"].replace(old_job, new_job)
    extra = dict(radar_id="s3", scan_id=extra_scan, qc_uri="current-extra")
    for kind in ("raw", "qc"):
        frame = dict(detail["panels"][0]["frames"][0], scan_id=extra_scan)
        frame["image_url"] = frame["image_url"].replace("example", "extra-"+kind)
        detail["panels"].append(dict(panel_id="dbzh_"+kind+":s3", frames=[frame]))
    monkeypatch.setattr(audit, "load_station_supplement", lambda *args: [extra])

    def image(url, **kwargs):
        data = cases.png()
        if fault == "changed_pixels" and "/extra-" not in url:
            def chunk(kind, body):
                return (struct.pack(">I", len(body))+kind+body+
                        struct.pack(">I", zlib.crc32(kind+body)))
            data = (b"\x89PNG\r\n\x1a\n"+
                    chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))+
                    chunk(b"IDAT", zlib.compress(b"\0\x11\x20\x30\xff"))+chunk(b"IEND", b""))
        return io.BytesIO(data)

    monkeypatch.setattr(audit, "urlopen", image)
    if fault == "mixed_generation":
        detail["panels"][0]["frames"][0]["image_url"] = (
            detail["panels"][0]["frames"][0]["image_url"].replace(new_job, old_job))
    if fault:
        with pytest.raises(ValueError, match="changed|generation"):
            audit.audit(plan, state, cache, "http://example.invalid", ["s3"])
    else:
        result = audit.audit(plan, state, cache, "http://example.invalid", ["s3"])
        proof = result["slots"][plan["groups"][0]["issue_time"]]
        assert result["status"] == "VERIFIED"
        assert proof["diagnostic_job_id"] == new_job
        assert proof["baseline_diagnostic_job_id"] == old_job
        assert proof["station_supplements"] == [extra]
        assert proof["public_frame_count"] == 8
