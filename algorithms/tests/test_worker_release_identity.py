from pathlib import Path

from rainpulse_algo.worker.release_identity import capture_release_identity, report_release_identity


def test_qc_identity_immutable_startup_and_drift(tmp_path, monkeypatch):
    config=tmp_path/"qc.yaml";config.write_text("profile_version: test\n")
    flags=tmp_path/"flags.yaml";flags.write_text("version: v1\n")
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(config))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(flags))
    startup=capture_release_identity("radar-qc-basic")
    # Overlay-only tests may not contain the original runtime.py. The hash is
    # mandatory in release verification, not in these drift-only unit tests.
    assert len(startup["qc_config_sha256"])==64
    first=report_release_identity(startup)
    assert first["qc_config_sha256"]==startup["qc_config_sha256"]
    flags.write_text("version: v2\n")
    changed=report_release_identity(startup)
    assert changed["config_unchanged"] is False
    assert changed["qc_flags_sha256"]==startup["qc_flags_sha256"]


def test_non_qc_workers_do_not_require_qc_config(monkeypatch):
    monkeypatch.delenv("RAINPULSE_RADAR_QC_CONFIG",raising=False)
    identity=capture_release_identity("pysteps-lk")
    assert identity["profile"]=="pysteps-lk"
    assert "qc_config_sha256" not in identity


def test_missing_identity_is_not_silently_verified(monkeypatch):
    monkeypatch.delenv("RAINPULSE_RADAR_QC_CONFIG",raising=False)
    monkeypatch.delenv("RAINPULSE_QC_FLAG_DEFINITIONS",raising=False)
    startup=capture_release_identity("radar-qc-basic")
    assert startup["qc_config_sha256"] is None
    assert report_release_identity(startup)["config_unchanged"] is False
