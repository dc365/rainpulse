from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.blockage import circular_partial_blockage
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.grid_profile import load_radar_grid_profile
from rainpulse_algo.radar.hybrid import build_hybrid_scan
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.clutter import asset_digest, build_clutter_prior
from rainpulse_algo.radar.qc_engine.geometry import wradlib_blockage
from rainpulse_algo.radar.qc_engine.review import run_manifest
from rainpulse_algo.radar.qc_engine.spike_reference import load_native_spike_reference
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_metrics import compare_measurement_actions
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.radar.qc_zarr import validate_qc_zarr_store
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_fmt_decoder import make_config
from .test_object_store import FakeMinio
from .test_qc_opensource import FLAGS, PROFILE, ROOT, field_case
from .test_radar_grid import RidgeTerrain, qc_fixture, small_grid
from .test_radar_qc import normalized_fixture, synthetic_normalized_fixture


def test_mature_geometry_matches_circle_and_never_sees_through_missing_dem():
    terrain = np.array([[-2.0, -0.5, 0, 0.5, 2, np.nan, 0]])
    beam, radius = np.zeros_like(terrain), np.ones_like(terrain)
    p, cumulative = wradlib_blockage(terrain, beam, radius, np.ones_like(terrain, bool))
    np.testing.assert_allclose(p, circular_partial_blockage(terrain, beam, radius), equal_nan=True)
    assert np.isnan(cumulative[0, 5:]).all()
    assert np.all(np.diff(cumulative[0, :5]) >= 0)


def test_new_engine_retries_have_identical_scientific_artifact_bytes(tmp_path, monkeypatch):
    objects = normalized_fixture(tmp_path)
    client = FakeMinio()
    prefix = "radar/normalized/z9598/test/volume.zarr"
    manifest = []
    for key, value in objects.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
        manifest.append(
            {"key": key, "sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)}
        )
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(objects),
            "size_bytes": sum(map(len, objects.values())),
            "objects": sorted(manifest, key=lambda x: x["key"]),
        }
    ).encode()
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(PROFILE))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_type": "radar.qc.requested.v1",
            "event_id": "30000000-0000-4000-8000-000000000111",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000112",
            "job_id": "30000000-0000-4000-8000-000000000113",
            "trace_id": "10000000-0000-4000-8000-000000000114",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{prefix}",
                "output_prefix": "s3://rainpulse/qc-test/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "fujian-qc-opensource-v1",
                "qc_pipeline_version": "qc-opensource-1.0.0",
                "flag_definition_version": "qc-flags-v2",
                "qc_profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
            },
        }
    )
    first, second = _execute_basic_qc(request, client), _execute_basic_qc(request, client)
    assert first.objects == second.objects
    assert validate_qc_zarr_store(first.objects)["sweep_count"] == 2
    wrong = request.model_copy(
        update={"payload": request.payload.model_copy(update={"qc_profile_sha256": "0" * 64})}
    )
    with pytest.raises(ValueError, match="SHA256"):
        _execute_basic_qc(wrong, client)


@pytest.mark.parametrize("with_flag", [False, True])
def test_v2_hybrid_excludes_untrusted_measurements_before_weight_normalization(tmp_path, with_flag):
    config = load_radar_config(make_config(tmp_path))
    longitude, latitude = config.site["longitude_deg"], config.site["latitude_deg"]
    grid = small_grid(longitude, latitude)
    profile = load_radar_grid_profile(ROOT / "configs/gridding/qc-opensource-hybrid-v1.yaml")
    profile = replace(profile, grid_id=grid.grid_id, grid_config_version=grid.config_version)
    objects = qc_fixture(config.config_version)
    store = MemoryStore()
    store.update(objects)
    source = zarr.open_group(store, mode="a")
    source.attrs.update(
        {
            "flag_definition_version": "qc-flags-v2",
            "qc_engine": "open_source",
            "operational_eligible": False,
        }
    )
    for i in (0, 1):
        sweep = source[f"sweep_{i:03d}"]
        shape = sweep["VALID_MASK"].shape
        sweep.create_dataset("REFLECTIVITY_TRUST_MASK", data=np.ones(shape, "uint8"))
        sweep.create_dataset(
            "QPE_ELIGIBLE_MASK",
            data=np.zeros(shape, "uint8") if not with_flag else np.ones(shape, "uint8"),
        )
        if with_flag:
            sweep["QC_FLAGS"][:] = np.full(shape, 32768, "uint32")
    objects = {str(k): bytes(v) for k, v in store.items()}
    flags = load_qc_profile(PROFILE, FLAGS).flag_masks
    result = build_hybrid_scan(
        objects,
        radar_config=config,
        grid=grid,
        profile=profile,
        terrain=RidgeTerrain(longitude, latitude),
        flag_masks=flags,
    )
    assert not result.fields["VALID_MASK"].any()
    assert result.operational_eligible is False
    assert "qc_candidate_not_operationally_accepted" in result.operational_reasons


def test_metrics_preserve_fixed_labels_and_separate_mixed_weather():
    labels = np.array([[1, 1, 2, 3, 0]])
    observed = np.ones(labels.shape, bool)
    reject = np.array([[False, True, True, True, False]])
    rate = np.array([[40.0, 10.0, 50.0, 40.0, 0.0]])
    metrics = compare_measurement_actions(labels, observed, reject, rate)
    assert metrics["trusted_weather_false_reject_rate"] == 0.5
    assert metrics["strong_weather_retention"] == 1
    assert metrics["mixed_measurement_withheld_rate"] == 1
    assert metrics["interference_recall"] == 1
    empty = compare_measurement_actions(np.zeros_like(labels), observed, reject, rate)
    assert empty["interference_recall"] is None
    with pytest.raises(ValueError):
        compare_measurement_actions(labels, observed, np.full(labels.shape, 3), rate)


def test_local_review_is_exact_input_bounded_and_never_promotes(tmp_path):
    dbzh, moments = field_case()
    objects = synthetic_normalized_fixture(dbzh, moments=moments)
    directory = tmp_path / "normalized.zarr"
    for key, value in objects.items():
        path = directory / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    manifest = {
        "schema_version": "1.0",
        "cases": [
            {
                "case_id": "synthetic-protection-only",
                "normalized_zarr": "normalized.zarr",
                "input_sha256": artifact_sha256(objects),
                "partition": "development",
                "process_id": "synthetic-only",
                "experimental_rfi": True,
            }
        ],
    }
    file = tmp_path / "cases.json"
    file.write_text(json.dumps(manifest))
    report = run_manifest(file, tmp_path / "review.json", inspect_rays=(50,))
    assert report["operational_eligible"] is False
    sweep = report["cases"][0]["sweeps"][0]
    assert sweep["label_status"].startswith("unlabeled")
    assert len(sweep["radials"][0]["range_m"]) == dbzh.shape[1]
    assert sweep["radials"][0]["fields"]["DBZH_RAW"][30] == 38
    assert "measurement_metrics" not in sweep["methods"]["open_source_fusion"]
    assert sweep["spike_native_reference"]["status"].startswith("not_executed")
    manifest["cases"][0]["input_sha256"] = "0" * 64
    file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash"):
        run_manifest(file, tmp_path / "invalid.json")


def test_native_spike_ray_reference_cannot_be_used_as_gate_truth(tmp_path):
    mask = np.array([0, 1, 0], "uint8")
    np.save(tmp_path / "ray.npy", mask)
    reference = {
        "algorithm": "RADVOL-QC-SPIKE",
        "input_sha256": "a" * 64,
        "source_revision": "pinned-upstream",
        "parameters_sha256": "b" * 64,
        "interpolated_values_used": False,
        "granularity": "ray",
        "mask_file": "ray.npy",
        "mask_sha256": hashlib.sha256((tmp_path / "ray.npy").read_bytes()).hexdigest(),
    }
    path = tmp_path / "native.json"
    path.write_text(json.dumps(reference))
    result = load_native_spike_reference(path, expected_input_sha256="a" * 64, shape=(3, 9))
    assert result["gate_metrics_permitted"] is False
    reference["interpolated_values_used"] = True
    path.write_text(json.dumps(reference))
    with pytest.raises(ValueError, match="interpolated"):
        load_native_spike_reference(path, expected_input_sha256="a" * 64, shape=(3, 9))


def test_static_prior_requires_independent_clear_air_and_exact_geometry():
    dbzh, _ = field_case()
    roots = []
    for index in range(3):
        objects = synthetic_normalized_fixture(dbzh)
        attrs = json.loads(objects[".zattrs"])
        attrs["scan_id"] = f"clear-{index}"
        objects[".zattrs"] = json.dumps(attrs).encode()
        roots.append(open_qc_input(objects).root)
    with pytest.raises(ValueError, match="clear-air"):
        build_clutter_prior(roots, reviewed_clear_air=False, minimum_samples=3)
    arrays, metadata = build_clutter_prior(roots, reviewed_clear_air=True, minimum_samples=3)
    assert asset_digest(arrays) == metadata["asset_content_sha256"]
    assert arrays["sweep_000__ground_clutter"][50, 30] == 1
    with pytest.raises(ValueError, match="unique"):
        build_clutter_prior(
            [roots[0], roots[0], roots[1]], reviewed_clear_air=True, minimum_samples=3
        )


def test_frozen_flag_layout_cannot_be_redefined(tmp_path):
    import yaml

    flags = yaml.safe_load(FLAGS.read_text())
    flags["flags"][12]["mask"] = 1
    bad = tmp_path / "bad-flags.yaml"
    bad.write_text(yaml.safe_dump(flags))
    with pytest.raises(ValueError, match="frozen layout"):
        load_qc_profile(PROFILE, bad)


@pytest.mark.parametrize(
    "qc_config", [PROFILE, ROOT / "configs/qc/fujian-qc-rfi-multivariate-v3.yaml"]
)
def test_new_qc_grid_mosaic_qpe_chain_preserves_generation_and_missing(tmp_path, qc_config):
    from datetime import UTC, datetime

    from rainpulse_algo.radar.grid_zarr import build_radar_grid_zarr_store
    from rainpulse_algo.radar.mosaic import (
        RadarMosaicInput,
        RadarMosaicInputError,
        build_radar_mosaic,
    )
    from rainpulse_algo.radar.mosaic_profile import load_radar_mosaic_profile
    from rainpulse_algo.radar.qc import apply_basic_qc
    from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store
    from rainpulse_algo.radar.qpe import convert_dbzh_to_rate
    from rainpulse_algo.radar.qpe_profile import load_qpe_profile

    config = load_radar_config(make_config(tmp_path))
    lon, lat = config.site["longitude_deg"], config.site["latitude_deg"]
    grid = small_grid(lon, lat)
    dbzh, moments = field_case()
    dbzh[:] = 20.0
    objects = synthetic_normalized_fixture(dbzh, moments=moments)
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store, mode="a")
    root.attrs.update(radar_id=config.radar_id, radar_config_version=config.config_version)
    store["health/summary.json"] = json.dumps(
        {"health": "HEALTHY", "radar_id": config.radar_id}
    ).encode()
    root["sweep_000"].attrs["nominal_elevation_deg"] = 0.5
    # The normalized contract uses datetime64[ns], not unitless epoch floats.
    del root["sweep_000/ray_time"]
    root["sweep_000"].create_dataset(
        "ray_time", data=np.full(360, np.datetime64("2026-08-25T12:00:00", "ns"))
    )
    objects = {str(k): bytes(v) for k, v in store.items()}
    qc_profile = load_qc_profile(qc_config, FLAGS)
    flags = qc_profile.flag_masks
    qc = apply_basic_qc(objects, qc_profile)
    qco = build_qc_zarr_store(
        objects,
        qc,
        asset_id="50000000-0000-4000-8000-000000000001",
        normalized_volume_uri="s3://rainpulse/test/native.zarr",
    )
    grid_profile = load_radar_grid_profile(ROOT / "configs/gridding/qc-opensource-hybrid-v1.yaml")
    grid_profile = replace(
        grid_profile, grid_id=grid.grid_id, grid_config_version=grid.config_version
    )

    class FlatTerrain:
        def sample(self, longitude, latitude):
            return np.full(longitude.shape, 100.0, dtype="float32")

    hybrid = build_hybrid_scan(
        qco,
        radar_config=config,
        grid=grid,
        profile=grid_profile,
        terrain=FlatTerrain(),
        flag_masks=flags,
    )
    assert hybrid.source_attributes["qc_parameters_sha256"] == qc_profile.parameters_hash
    grido = build_radar_grid_zarr_store(
        hybrid,
        asset_id="60000000-0000-4000-8000-000000000001",
        qc_volume_uri="s3://rainpulse/test/qc.zarr",
    )
    mosaic_profile = load_radar_mosaic_profile(ROOT / "configs/mosaic/qc-opensource-mosaic-v1.yaml")
    mosaic_profile = replace(
        mosaic_profile,
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
        alignment=replace(mosaic_profile.alignment, minimum_contributors=1),
    )
    inputs = (
        RadarMosaicInput(
            config.radar_id,
            root.attrs["scan_id"],
            "s3://rainpulse/test/grid.zarr",
            0,
            grid_profile.algorithm_version,
            grido,
        ),
    )
    mosaic = build_radar_mosaic(
        inputs,
        analysis_time=datetime(2026, 8, 25, 12, tzinfo=UTC),
        grid=grid,
        profile=mosaic_profile,
        flag_masks=flags,
    )
    qpe_profile = load_qpe_profile(ROOT / "configs/qpe/qc-opensource-zr-v1.yaml")
    rate, _ = convert_dbzh_to_rate(
        mosaic.fields["DBZH_QC"], mosaic.fields["VALID_MASK"], qpe_profile
    )
    assert np.any(np.isfinite(rate))
    assert np.all(np.isnan(rate[mosaic.fields["VALID_MASK"] == 0]))
    assert mosaic.operational_eligible is False
    # Another radar with a different candidate profile must not silently enter this analysis.
    other_store = MemoryStore()
    other_store.update(grido)
    other = zarr.open_group(other_store, mode="a")
    other.attrs.update(
        radar_id="different-radar",
        qc_parameters_sha256="f" * 64,
        scan_id="20000000-0000-4000-8000-000000000002",
    )
    other_input = replace(
        inputs[0],
        radar_id="different-radar",
        scan_id="20000000-0000-4000-8000-000000000002",
        objects={str(k): bytes(v) for k, v in other_store.items()},
    )
    with pytest.raises(RadarMosaicInputError, match="mix different"):
        build_radar_mosaic(
            (*inputs, other_input),
            analysis_time=mosaic.analysis_time,
            grid=grid,
            profile=mosaic_profile,
            flag_masks=flags,
        )
