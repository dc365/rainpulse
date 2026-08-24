import bz2
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import yaml
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.fmt import (
    CUT_CONFIG,
    DECODER_VERSION,
    GENERIC_HEADER,
    MAGIC_NUMBER,
    MOMENT_HEADER,
    RADIAL_HEADER,
    SITE_CONFIG,
    TASK_CONFIG,
    DecodeError,
    decode_fmt_volume,
)
from rainpulse_algo.radar.health import assess_volume_health, load_radar_health_config
from rainpulse_algo.radar.worker import execute_fmt_decode
from rainpulse_algo.radar.zarr_volume import build_zarr_store, validate_zarr_store
from rainpulse_algo.worker.domain_contracts import RadarDecodeRequested

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
Z9598_CONFIG = REPOSITORY_ROOT / "configs" / "radars" / "z9598.yaml"
HEALTH_CONFIG = REPOSITORY_ROOT / "configs" / "health" / "rp007-integrity-v1.yaml"


def make_config(tmp_path: Path) -> Path:
    value = yaml.safe_load(Z9598_CONFIG.read_text())
    value["config_version"] = "z9598-test-v1"
    value["scan"].update(
        {
            "expected_elevations_deg": [0.5, 1.5],
            "expected_cut_elevations_deg": [0.5, 1.5],
            "range_gate_m": 1000.0,
            "max_range_m": 6000.0,
            "azimuth_resolution_deg": 180.0,
        }
    )
    value["fields"] = [value["fields"][0]]
    path = tmp_path / "z9598.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    return path


def make_fmt_fixture(tmp_path: Path, *, magic: int = MAGIC_NUMBER) -> Path:
    start = int(datetime(2026, 6, 15, 9, 4, 25, tzinfo=UTC).timestamp())
    payload = bytearray()
    payload.extend(GENERIC_HEADER.pack(magic, 2, 0, 1, -2147483648, bytes(16)))
    payload.extend(
        SITE_CONFIG.pack(
            b"Z9598",
            b"SanMing_Z9598",
            27.00861167907715,
            117.08055877685547,
            1740,
            1692,
            2730.0,
            0.96,
            0.86,
            789002,
            4,
            0,
            0,
            0,
            0,
            bytes(46),
        )
    )
    payload.extend(
        TASK_CONFIG.pack(
            b"VCP21D",
            b"Golden structural fixture",
            3,
            0,
            1570,
            start,
            2,
            *([0.0] * 9),
            bytes(40),
        )
    )
    for elevation in (0.5, 1.5):
        values = list(CUT_CONFIG.unpack(bytes(CUT_CONFIG.size)))
        values[0] = 1
        values[1] = 0
        values[2] = 322.0
        values[3] = -999999.0
        values[6] = elevation
        values[9] = 1.0
        values[10] = 12.0
        values[11] = 1000
        values[12] = 1000
        values[13] = 6000
        values[14] = -2147483648
        values[15] = 0
        values[20] = 8.8
        values[21] = 1 << 2
        payload.extend(CUT_CONFIG.pack(*values))

    raw_codes = (
        bytes([0, 4, 5, 66, 70, 100]),
        bytes([1, 2, 10, 68, 72, 110]),
        bytes([0, 3, 20, 70, 80, 120]),
        bytes([4, 4, 30, 72, 90, 130]),
    )
    radial_specs = (
        (3, 1, 0.2, 0.5),
        (2, 1, 180.2, 0.5),
        (0, 2, 0.4, 1.5),
        (4, 2, 180.4, 1.5),
    )
    for index, ((state, elevation_number, azimuth, elevation), body) in enumerate(
        zip(radial_specs, raw_codes, strict=True), 1
    ):
        data_length = MOMENT_HEADER.size + len(body)
        payload.extend(
            RADIAL_HEADER.pack(
                state,
                0,
                index,
                index,
                elevation_number,
                azimuth,
                elevation,
                start + index,
                index * 1000,
                data_length,
                1,
                0,
                -32768,
                -32768,
                b"\0",
                bytes(13),
            )
        )
        payload.extend(MOMENT_HEADER.pack(2, 2, 66, 1, -32768, len(body), bytes(12)))
        payload.extend(body)

    path = tmp_path / "Z_RADR_I_Z9598_20260824100425_O_DOR_SAD_CAP_FMT.bin.bz2"
    with bz2.open(path, "wb") as stream:
        stream.write(payload)
    return path


def test_fmt_decoder_preserves_sweeps_geometry_and_missing_values(tmp_path: Path) -> None:
    config = load_radar_config(make_config(tmp_path))
    source = make_fmt_fixture(tmp_path)

    volume = decode_fmt_volume(source, config)

    assert volume.site.code == "Z9598"
    assert volume.site.name == "SanMing_Z9598"
    assert len(volume.sweeps) == 2
    assert volume.ray_count == 4
    assert volume.canonical_fields == ("DBZH",)
    assert volume.sweeps[0].fields["DBZH"].shape == (2, 6)
    assert np.isnan(volume.sweeps[0].fields["DBZH"][0, :2]).all()
    assert volume.sweeps[0].fields["DBZH"][0, 2] == pytest.approx(-30.5)
    assert volume.sweeps[0].fields["DBZH"][0, 3] == pytest.approx(0.0)
    assert volume.sweeps[0].range_m.tolist() == [500, 1500, 2500, 3500, 4500, 5500]
    assert volume.warnings and "header time is authoritative" in volume.warnings[0]


def test_fmt_decoder_rejects_invalid_magic(tmp_path: Path) -> None:
    config = load_radar_config(make_config(tmp_path))
    source = make_fmt_fixture(tmp_path, magic=0)

    with pytest.raises(DecodeError, match="magic"):
        decode_fmt_volume(source, config)


def test_normalized_zarr_round_trip(tmp_path: Path) -> None:
    config = load_radar_config(make_config(tmp_path))
    source = make_fmt_fixture(tmp_path)
    volume = decode_fmt_volume(source, config)
    health = assess_volume_health(volume, config, load_radar_health_config(HEALTH_CONFIG))

    objects = build_zarr_store(
        volume,
        config,
        asset_id=UUID("44444444-4444-4444-8444-444444444444"),
        source_uri=source.as_uri(),
        health=health,
    )
    summary = validate_zarr_store(objects)
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")

    assert summary["sweep_count"] == 2
    assert summary["ray_count"] == 4
    assert summary["fields"] == ["DBZH"]
    assert root.attrs["geometry_encoding"] == "sweep_groups_v1"
    assert root.attrs["operational_eligible"] is False
    assert root.attrs["radar_health"] == "DEGRADED"
    assert root["sweep_000/DBZH"].dtype == np.dtype("float32")
    assert np.isnan(root["sweep_000/DBZH"][0, 0])
    assert "health/summary.json" in objects


def test_health_summary_detects_missing_sweep_and_noise_telemetry(tmp_path: Path) -> None:
    config = load_radar_config(make_config(tmp_path))
    volume = decode_fmt_volume(make_fmt_fixture(tmp_path), config)
    incomplete = replace(volume, sweeps=volume.sweeps[:1])

    summary = assess_volume_health(
        incomplete, config, load_radar_health_config(HEALTH_CONFIG)
    ).value

    assert summary["health"] == "DEGRADED"
    assert summary["missing_sweep_numbers"] == [2]
    assert summary["missing_radial_count"] == 2
    assert summary["scan_completeness"] == pytest.approx(0.5)
    assert summary["channel_status"] == "UNKNOWN"
    assert "NOISE_TELEMETRY_MISSING" in summary["health_reasons"]


def test_health_summary_counts_out_of_range_gates(tmp_path: Path) -> None:
    config = load_radar_config(make_config(tmp_path))
    volume = decode_fmt_volume(make_fmt_fixture(tmp_path), config)
    first = volume.sweeps[0]
    fields = dict(first.fields)
    fields["DBZH"] = fields["DBZH"].copy()
    fields["DBZH"][0, 2] = 999.0
    changed = replace(first, fields=fields)
    volume = replace(volume, sweeps=(changed, *volume.sweeps[1:]))

    summary = assess_volume_health(
        volume, config, load_radar_health_config(HEALTH_CONFIG)
    ).value

    assert summary["out_of_range_gate_count"] == 1
    assert summary["anomaly_count"] >= 1
    assert "ANOMALOUS_VALUES" in summary["health_reasons"]


def test_real_worker_executor_enforces_allowed_root_and_builds_zarr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = make_config(tmp_path)
    source = make_fmt_fixture(tmp_path)
    value = json.loads(
        (REPOSITORY_ROOT / "contracts" / "examples" / "radar-decode-requested.json").read_text()
    )
    value["payload"].update(
        {
            "radar_id": "z9598",
            "input_uri": source.as_uri(),
            "source_format": "cma-rstm-level2",
            "radar_config_version": "z9598-test-v1",
            "decoder_version": DECODER_VERSION,
        }
    )
    request = RadarDecodeRequested.model_validate(value)
    monkeypatch.setenv("RAINPULSE_RADAR_INPUT_ROOTS", str(tmp_path))
    monkeypatch.setenv("RAINPULSE_RADAR_CONFIG_DIR", str(config_path.parent))
    monkeypatch.setenv("RAINPULSE_RADAR_HEALTH_CONFIG", str(HEALTH_CONFIG))

    result = execute_fmt_decode(request)

    assert result.data is None
    assert result.objects is not None
    assert ".zgroup" in result.objects
    assert result.diagnostics["radar_health"]["health"] == "DEGRADED"
    assert result.metrics["sweep_count"] == 2.0
    assert validate_zarr_store(result.objects)["fields"] == ["DBZH"]
