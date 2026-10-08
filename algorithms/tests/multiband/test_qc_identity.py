"""Actual producer identity must survive native adaptation and publication."""

import base64
import hashlib
import json
import zlib
from dataclasses import asdict, replace

import numpy as np
import pytest
import zarr
from conftest import TARGET, volume
from zarr.storage import MemoryStore

from rainpulse_algo.multiband.adapters import from_group
from rainpulse_algo.multiband.codec import decode_volume, encode_volume
from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.fusion import build_composite
from rainpulse_algo.multiband.product import composite_objects as product_objects
from rainpulse_algo.multiband.product import sx_comparison_objects
from rainpulse_algo.multiband.qc_identity import source_qc_identity
from rainpulse_algo.multiband.quality import accept_s_qc, x_qc
from rainpulse_algo.multiband.stream_fusion import build_composite_streaming

CUTOFF = "2026-09-23T00:10:10Z"


def test_native_s_asset_keeps_actual_qc_identity(net):
    station = replace(net.stations["s1"], source="s_qc_zarr")
    v = volume(station)
    root = zarr.group(store=MemoryStore())
    root.attrs.update(
        contract_name="rainpulse.qc-radar-volume",
        radar_id="s1",
        scan_id=v.metadata["scan_id"],
        flag_definition_version="frozen-flags",
        qc_pipeline_version="stored-s-pipeline",
        qc_parameters_sha256="a" * 64,
        qc_implementation_revision="stored-s-revision",
        qc_profile="stored-profile",
        qc_libraries={"wradlib": "stored-version"},
    )
    root.create_dataset("sweep_number", data=np.array([0], np.int16))
    cut = root.create_group("sweep_000")
    sweep = v.sweeps[0]
    for name, data in {
        "azimuth": sweep.azimuth_deg,
        "range": sweep.range_m,
        "elevation": sweep.elevation_deg,
        "ray_time": sweep.ray_time_epoch,
        "DBZH_RAW": sweep.fields["DBZH"],
        "DBZH_QC": sweep.fields["DBZH"],
        "QC_FLAGS": np.zeros(sweep.fields["DBZH"].shape, np.uint32),
        "REFLECTIVITY_ELIGIBLE_FOR_CR": np.ones(sweep.fields["DBZH"].shape, np.uint8),
        "QUALITY_INDEX": np.ones(sweep.fields["DBZH"].shape, np.float32),
    }.items():
        cut.create_dataset(name, data=data)
    source = {k: v.metadata[k] for k in ("scan_id", "volume_start", "volume_end", "available_at")}
    adapted = from_group(
        root,
        station,
        source,
        asset_sha256="b" * 64,
        maximum_bytes=10 * 1024**2,
        s_reject_mask=4,
        expected_flag_version="frozen-flags",
    )
    identity = source_qc_identity(adapted.metadata)
    assert identity["parameters_sha256"] == "a" * 64
    assert identity["implementation_revision"] == "stored-s-revision"
    assert identity["profile"] == "stored-profile"
    assert identity["libraries"] == {"wradlib": "stored-version"}
    assert identity["unreported_fields"] == []
    del root.attrs["qc_implementation_revision"]
    legacy = from_group(
        root,
        station,
        source,
        asset_sha256="c" * 64,
        maximum_bytes=10 * 1024**2,
        s_reject_mask=4,
        expected_flag_version="frozen-flags",
    )
    assert source_qc_identity(legacy.metadata)["unreported_fields"] == ["implementation_revision"]


def test_x_baseline_replaces_inherited_identity_and_hashes_effective_parameters(net):
    station = net.stations["x1"]
    raw = volume(station)
    raw.metadata.update(
        xqc_parameter_sha256="inherited",
        xqc_mode="inherited",
        xqc_implementation_revision="inherited",
    )
    result = x_qc(raw, station, net.sha256)
    expected = hashlib.sha256(
        json.dumps(
            asdict(station.x_qc), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    identity = source_qc_identity(result.metadata)
    assert identity["parameters_sha256"] == expected
    assert identity["implementation_revision"] == "x-moment-qc-v1"
    assert identity["mode"] == "baseline"
    changed = replace(station, x_qc=replace(station.x_qc, snr_min_db=4.0))
    assert (
        source_qc_identity(x_qc(raw, changed, net.sha256).metadata)["parameters_sha256"] != expected
    )
    assert raw.metadata["xqc_parameter_sha256"] == "inherited"


@pytest.mark.parametrize("enhanced", [False, True])
def test_identity_survives_codec_streaming_and_manifest(net, tmp_path, enhanced):
    s = accept_s_qc(volume(net.stations["s1"]), net.stations["s1"], net.sha256)
    s.metadata.update(qc_parameters_sha256="a" * 64, qc_implementation_revision="s-producer")
    x = x_qc(volume(net.stations["x1"]), net.stations["x1"], net.sha256)
    if enhanced:
        x.metadata.update(
            processing="enhanced-producer",
            xqc_parameter_sha256="e" * 64,
            xqc_implementation_revision="enhanced-revision",
            xqc_mode="quarantine",
        )
    volumes = [
        decode_volume(
            encode_volume(v), maximum_bytes=10 * 1024**2, asset_sha256=v.metadata["asset_sha256"]
        )
        for v in (s, x)
    ]
    eager = build_composite(volumes, net, "local", TARGET, CUTOFF)
    cuts = [replace(v, sweeps=[cut]) for v in volumes for cut in v.sweeps]
    streamed = build_composite_streaming(
        iter(cuts),
        net,
        "local",
        TARGET,
        CUTOFF,
        options=ExecutionOptions(streaming=True),
        directory=tmp_path,
    )
    for result in (eager, streamed):
        records = result.metadata["sources"]
        assert records[0]["qc_identity"]["parameters_sha256"] == "a" * 64
        x_identity = records[-1]["qc_identity"]
        assert x_identity == source_qc_identity(x.metadata)
        if enhanced:
            assert x_identity["base_parameters_sha256"] != "e" * 64
        manifest = json.loads(product_objects(result)["manifest.json"])
        assert manifest["sources"] == records
    for name in eager.arrays:
        np.testing.assert_array_equal(eager.arrays[name], streamed.arrays[name])


def test_unknown_x_identity_does_not_borrow_s_attributes():
    identity = source_qc_identity(
        {
            "band": "X",
            "qc_parameters_sha256": "a" * 64,
            "qc_implementation_revision": "wrong-producer",
        }
    )
    assert identity["parameters_sha256"] is None
    assert identity["implementation_revision"] is None
    assert identity["unreported_fields"] == [
        "pipeline_version",
        "parameters_sha256",
        "implementation_revision",
    ]


@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_legacy_enhanced_identity_reports_missing_baseline_parameters(mode):
    metadata = {
        "band": "X", "processing": "enhanced-producer", "xqc_mode": mode,
        "xqc_parameter_sha256": "e" * 64,
        "xqc_implementation_revision": "enhanced-revision",
    }
    identity = source_qc_identity(metadata)
    assert identity["base_parameters_sha256"] is None
    assert identity["unreported_fields"] == ["base_parameters_sha256"]
    metadata["xqc_base_parameter_sha256"] = "b" * 64
    assert source_qc_identity(metadata)["unreported_fields"] == []


def test_real_x_enhancement_retains_baseline_and_enhancement_parameters(net):
    from rainpulse_algo.multiband.xqc_v2.config import XQCConfig

    cfg = XQCConfig.model_validate(
        {
            "mode": "audit",
            "receiver_enabled": False,
            "clutter_enabled": False,
            "isolation_enabled": False,
            "radial_objects_enabled": False,
        }
    )
    station = net.stations["x1"]
    enhanced = replace(station, x_qc=replace(station.x_qc, enhancement=cfg.model_dump(mode="json")))
    raw = volume(station)
    out = x_qc(raw, enhanced, net.sha256)
    identity = source_qc_identity(out.metadata)
    baseline = source_qc_identity(x_qc(raw, station, net.sha256).metadata)
    assert identity["parameters_sha256"] == cfg.digest
    assert identity["base_parameters_sha256"] == baseline["parameters_sha256"]
    assert identity["implementation_revision"] == out.metadata["xqc_implementation_revision"]
    assert identity["mode"] == "audit"
    changed = replace(enhanced, x_qc=replace(enhanced.x_qc, snr_min_db=4.0))
    changed_identity = source_qc_identity(x_qc(raw, changed, net.sha256).metadata)
    assert changed_identity["parameters_sha256"] == identity["parameters_sha256"]
    assert changed_identity["base_parameters_sha256"] != identity["base_parameters_sha256"]


def test_single_band_probe_uses_its_own_winner_indices(net):
    s = accept_s_qc(volume(net.stations["s1"]), net.stations["s1"], net.sha256)
    x = x_qc(volume(net.stations["x1"]), net.stations["x1"], net.sha256)
    combined = build_composite([s, x], net, "local", TARGET, CUTOFF)
    bands = {band: build_composite([v], net, "local", TARGET, CUTOFF)
             for band, v in (("S", s), ("X", x))}
    objects = sx_comparison_objects(combined, bands)
    manifest = json.loads(objects["manifest.json"])
    products = {p["product_id"]: p for p in manifest["comparison"]["products"]}
    for band in ("S", "X"):
        product = products[f"{band.lower()}_only"]
        probe = product["map"]["probe"]
        assert {"WINNER_SOURCE", "WINNER_RAY", "WINNER_GATE"} <= set(probe["fields"])
        winners = set()
        for tile in probe["tiles"].values():
            payload = json.loads(objects[tile["path"]])
            data = np.frombuffer(zlib.decompress(base64.b64decode(payload["data"])), "<f8")
            data = data.reshape(payload["height"], payload["width"], len(payload["fields"]))
            indices = data[..., payload["fields"].index("WINNER_SOURCE")]
            winners.update(int(i) for i in np.unique(indices) if np.isfinite(i) and i >= 0)
        assert winners
        for index in winners:
            source = next(source for source in product["sources"] if source["index"] == index)
            assert source["band"] == band
            assert source["qc_identity"] == source_qc_identity((s if band == "S" else x).metadata)
