from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest
import zarr
from pyproj import Geod
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.radar.ancillary import (
    AncillarySource,
    Bounds,
    CoastlineSource,
    DEMSource,
    iter_dem_tiles,
    sha256_file,
)
from rainpulse_algo.radar.blockage import (
    beam_centre_height_m,
    circular_partial_blockage,
    map_grid_to_polar,
)
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.dem import SharedDEMCache, VerifiedDEMTileStore
from rainpulse_algo.radar.grid_profile import load_radar_grid_profile
from rainpulse_algo.radar.grid_zarr import (
    build_radar_grid_zarr_store,
    validate_radar_grid_zarr_store,
)
from rainpulse_algo.radar.hybrid import (
    RadarGridInputError,
    build_hybrid_scan,
    create_hybrid_scan_caches,
)

from .test_fmt_decoder import make_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "gridding" / "rp016-hybrid-v1.yaml"
FLAG_PATH = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"


class RidgeTerrain:
    def __init__(self, radar_longitude: float, radar_latitude: float) -> None:
        self.radar_longitude = radar_longitude
        self.radar_latitude = radar_latitude
        self.geod = Geod(ellps="WGS84")

    def sample(self, longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
        _, _, distance = self.geod.inv(
            np.full(longitude.shape, self.radar_longitude),
            np.full(latitude.shape, self.radar_latitude),
            longitude,
            latitude,
        )
        return np.where(distance < 800, 1692.0, 1760.0).astype("float32")


class CountingTerrain(RidgeTerrain):
    cache_identity = "counting-ridge-v1"

    def __init__(self, radar_longitude: float, radar_latitude: float) -> None:
        super().__init__(radar_longitude, radar_latitude)
        self.sample_calls = 0

    def sample(self, longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
        self.sample_calls += 1
        return super().sample(longitude, latitude)


class FakeRasterDataset:
    def __init__(
        self,
        values: np.ndarray,
        bounds: tuple[float, float, float, float],
    ) -> None:
        self._values = values
        self.bounds = bounds
        self.crs = SimpleNamespace(to_epsg=lambda: 4326)

    def __enter__(self) -> FakeRasterDataset:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def read(self, index: int, masked: bool = True) -> np.ma.MaskedArray:
        assert index == 1
        assert masked is True
        return np.ma.array(self._values, mask=np.zeros(self._values.shape, dtype=bool))


def flag_masks() -> dict[str, np.uint32]:
    import yaml

    value = yaml.safe_load(FLAG_PATH.read_text())
    return {item["name"]: np.uint32(item["mask"]) for item in value["flags"]}


def small_grid(radar_longitude: float, radar_latitude: float) -> RegularLatLonGrid:
    return RegularLatLonGrid(
        grid_id="synthetic-radar-grid-v1",
        config_version="synthetic-radar-grid-config-v1",
        west=radar_longitude - 0.01,
        east=radar_longitude + 0.01,
        south=radar_latitude - 0.01,
        north=radar_latitude + 0.01,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=3,
        latitude_count=3,
        reference_latitude_deg=radar_latitude,
        ancillary_domain_id="synthetic-ancillary-v1",
    )


def qc_fixture(radar_config_version: str) -> dict[str, bytes]:
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.qc-radar-volume",
            "contract_version": "1.0",
            "asset_id": "50000000-0000-4000-8000-000000000001",
            "input_asset_ids": ["40000000-0000-4000-8000-000000000001"],
            "scan_id": "10000000-0000-4000-8000-000000000004",
            "radar_id": "z9598",
            "normalized_volume_uri": "s3://rainpulse/normalized/volume.zarr",
            "radar_config_version": radar_config_version,
            "qc_profile": "rp008-basic-v1",
            "qc_pipeline_version": "rp008-basic-1.0.4",
            "flag_definition_version": "qc-flags-v1",
            "module_provenance": [
                {"name": "static_ground_clutter", "status": "skipped"},
                {"name": "sea_ap", "status": "skipped"},
            ],
        }
    )
    root.create_dataset("sweep_number", data=np.array([0, 1], dtype="int16"))
    root.create_dataset("sweep_start_ray_index", data=np.array([0, 4], dtype="int32"))
    root.create_dataset("sweep_end_ray_index", data=np.array([3, 7], dtype="int32"))
    for sweep_index, (elevation, reflectivity) in enumerate(((0.5, 20.0), (1.5, 30.0))):
        group = root.create_group(f"sweep_{sweep_index:03d}")
        group.attrs.update(
            {
                "sweep_number": sweep_index,
                "source_sweep_number": sweep_index + 1,
                "nominal_elevation_deg": elevation,
                "source_moments": ["REF"],
            }
        )
        group.create_dataset(
            "azimuth", data=np.array([0.0, 90.0, 180.0, 270.0], dtype="float32")
        )
        group.create_dataset(
            "elevation", data=np.full(4, elevation, dtype="float32")
        )
        group.create_dataset(
            "range", data=np.array([500.0, 1500.0], dtype="float32")
        )
        group.create_dataset(
            "ray_time",
            data=np.array(
                [
                    "2026-08-24T03:00:00",
                    "2026-08-24T03:00:01",
                    "2026-08-24T03:00:02",
                    "2026-08-24T03:00:03",
                ],
                dtype="datetime64[ns]",
            ),
        )
        shape = (4, 2)
        group.create_dataset("DBZH_QC", data=np.full(shape, reflectivity, dtype="float32"))
        group.create_dataset("QUALITY_INDEX", data=np.full(shape, 0.9, dtype="float32"))
        group.create_dataset("QI_METEO", data=np.full(shape, 0.95, dtype="float32"))
        group.create_dataset("QI_ATTENUATION", data=np.full(shape, np.nan, dtype="float32"))
        group.create_dataset("QI_INTERFERENCE", data=np.ones(shape, dtype="float32"))
        group.create_dataset("QI_CALIBRATION", data=np.full(shape, np.nan, dtype="float32"))
        group.create_dataset("QI_RANGE", data=np.full(shape, 0.8, dtype="float32"))
        group.create_dataset("VALID_MASK", data=np.ones(shape, dtype="uint8"))
        group.create_dataset("QC_FLAGS", data=np.zeros(shape, dtype="uint32"))
    return {str(key): bytes(value) for key, value in store.items()}


def dem_source_fixture() -> AncillarySource:
    return AncillarySource(
        domain_id="synthetic-dem-domain-v1",
        config_version="synthetic-ancillary-config-v1",
        bounds=Bounds(west=117, east=118, south=27, north=28),
        dem=DEMSource(
            asset_version="synthetic-dem-asset-v1",
            base_url="https://example.test/dem",
            planned_tile_count=1,
            storage_prefix="ancillary/dem/synthetic-dem-asset-v1",
            native_resolution_arc_seconds=1.0,
            max_uncovered_land_area_km2_per_tile=0.1,
        ),
        coastline=CoastlineSource(
            asset_version="synthetic-coastline-v1",
            source_url="https://example.test/coastline.zip",
            source_sha256="0" * 64,
            storage_prefix="ancillary/coastline/synthetic-coastline-v1",
        ),
    )


def write_dem_runtime_fixture(root: Path, source: AncillarySource) -> Path:
    tile = iter_dem_tiles(source)[0]
    tile_path = root / tile.relative_path
    tile_path.parent.mkdir(parents=True, exist_ok=True)
    tile_path.write_bytes(b"synthetic-dem-tile")
    manifest_dir = root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "domain_id": source.domain_id,
        "config_version": source.config_version,
        "dem_asset_version": source.dem.asset_version,
        "assets": [
            {
                "asset_type": "dem_tile",
                "tile_id": tile.tile_id,
                "relative_path": tile.relative_path.as_posix(),
                "size_bytes": tile_path.stat().st_size,
                "sha256": sha256_file(tile_path),
            }
        ],
    }
    verification = {
        "status": "accepted",
        "domain_id": source.domain_id,
        "config_version": source.config_version,
    }
    (manifest_dir / f"{source.config_version}.json").write_text(json.dumps(manifest))
    (manifest_dir / f"{source.config_version}.verification.json").write_text(
        json.dumps(verification)
    )
    return tile_path


def test_beam_geometry_and_partial_blockage_have_physical_limits() -> None:
    profile = load_radar_grid_profile(PROFILE_PATH)
    height = beam_centre_height_m(
        np.array([0.0, 100000.0]),
        np.array([0.5, 0.5]),
        100.0,
        profile.beam_geometry,
    )
    assert height[0] == pytest.approx(100.0)
    assert height[1] > height[0]

    fraction = circular_partial_blockage(
        np.array([80.0, 100.0, 120.0]),
        np.array([100.0, 100.0, 100.0]),
        np.array([10.0, 10.0, 10.0]),
    )
    assert fraction.tolist() == pytest.approx([0.0, 0.5, 1.0])


def test_direct_polar_mapping_rejects_cells_outside_ray_tolerance() -> None:
    profile = load_radar_grid_profile(PROFILE_PATH)
    mapping = map_grid_to_polar(
        np.array([[117.0805588, 117.0905588]], dtype="float32"),
        np.array([[27.0186117, 27.0186117]], dtype="float32"),
        radar_longitude_deg=117.0805588,
        radar_latitude_deg=27.0086117,
        sweep_azimuth_deg=np.array([0.0, 90.0, 180.0, 270.0]),
        sweep_range_m=np.array([500.0, 1500.0]),
        config=profile.polar_mapping,
    )
    assert mapping.supported[0, 0]
    assert not mapping.supported[0, 1]


def test_hybrid_scan_selects_higher_sweep_behind_low_beam_ridge(tmp_path: Path) -> None:
    radar_config = load_radar_config(make_config(tmp_path))
    grid = small_grid(
        float(radar_config.site["longitude_deg"]),
        float(radar_config.site["latitude_deg"]),
    )
    profile = load_radar_grid_profile(PROFILE_PATH)
    profile = replace(
        profile,
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )
    result = build_hybrid_scan(
        qc_fixture(radar_config.config_version),
        radar_config=radar_config,
        grid=grid,
        profile=profile,
        terrain=RidgeTerrain(
            float(radar_config.site["longitude_deg"]),
            float(radar_config.site["latitude_deg"]),
        ),
        flag_masks=flag_masks(),
        expected_scan_id="10000000-0000-4000-8000-000000000004",
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
    )

    north_cell = (2, 1)
    assert result.fields["VALID_MASK"][north_cell] == 1
    assert result.fields["SOURCE_SWEEP"][north_cell] == 1
    assert result.fields["DBZH_QC"][north_cell] == pytest.approx(30.0)
    assert result.fields["BLOCKAGE_RATE"][north_cell] < 0.7
    assert result.fields["QI_METEO"][north_cell] == pytest.approx(0.95)
    assert np.isnan(result.fields["QI_ATTENUATION"][north_cell])
    assert np.isnan(result.fields["QI_CALIBRATION"][north_cell])
    assert result.fields["QI_RANGE"][north_cell] == pytest.approx(0.8)
    assert not result.operational_eligible
    assert "vertical_datum_unverified" in result.operational_reasons
    assert result.summary["selection_counts"]["sweep_001"] > 0

    objects = build_radar_grid_zarr_store(
        result,
        asset_id=UUID("60000000-0000-4000-8000-000000000001"),
        qc_volume_uri="s3://rainpulse/qc/volume.zarr",
        provenance={"job_id": "60000000-0000-4000-8000-000000000002"},
    )
    validation = validate_radar_grid_zarr_store(objects)
    assert validation["shape"] == (3, 3)
    assert validation["valid_cell_count"] == 5
    assert validation["operational_eligible"] is False
    assert "grid/summary.json" in objects


def test_hybrid_scan_rejects_confirmed_radial_interference(tmp_path: Path) -> None:
    radar_config = load_radar_config(make_config(tmp_path))
    grid = small_grid(
        float(radar_config.site["longitude_deg"]),
        float(radar_config.site["latitude_deg"]),
    )
    profile = replace(
        load_radar_grid_profile(PROFILE_PATH),
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )
    objects = qc_fixture(radar_config.config_version)
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="a")
    root["sweep_000/QC_FLAGS"][:] = flag_masks()["RADIAL_INTERFERENCE"]
    objects = {str(key): bytes(value) for key, value in store.items()}

    result = build_hybrid_scan(
        objects,
        radar_config=radar_config,
        grid=grid,
        profile=profile,
        terrain=RidgeTerrain(
            float(radar_config.site["longitude_deg"]),
            float(radar_config.site["latitude_deg"]),
        ),
        flag_masks=flag_masks(),
    )

    assert result.summary["selection_counts"]["sweep_000"] == 0
    selected = result.fields["VALID_MASK"] == 1
    assert np.any(selected)
    assert np.all(result.fields["SOURCE_SWEEP"][selected] == 1)
    assert np.all(result.fields["DBZH_QC"][selected] == pytest.approx(30.0))


def test_grid_rejects_qc_volume_from_another_scan(tmp_path: Path) -> None:
    radar_config = load_radar_config(make_config(tmp_path))
    grid = small_grid(
        float(radar_config.site["longitude_deg"]),
        float(radar_config.site["latitude_deg"]),
    )
    profile = replace(
        load_radar_grid_profile(PROFILE_PATH),
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )

    with pytest.raises(RadarGridInputError, match="scan identity"):
        build_hybrid_scan(
            qc_fixture(radar_config.config_version),
            radar_config=radar_config,
            grid=grid,
            profile=profile,
            terrain=RidgeTerrain(
                float(radar_config.site["longitude_deg"]),
                float(radar_config.site["latitude_deg"]),
            ),
            flag_masks=flag_masks(),
            expected_scan_id="20000000-0000-4000-8000-000000000004",
        )


def test_grid_rejects_flag_definition_missing_configured_hard_reject(
    tmp_path: Path,
) -> None:
    radar_config = load_radar_config(make_config(tmp_path))
    grid = small_grid(
        float(radar_config.site["longitude_deg"]),
        float(radar_config.site["latitude_deg"]),
    )
    profile = replace(
        load_radar_grid_profile(PROFILE_PATH),
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )
    incomplete_flags = flag_masks()
    incomplete_flags.pop("RADIAL_INTERFERENCE")

    with pytest.raises(RadarGridInputError, match="RADIAL_INTERFERENCE"):
        build_hybrid_scan(
            qc_fixture(radar_config.config_version),
            radar_config=radar_config,
            grid=grid,
            profile=profile,
            terrain=RidgeTerrain(
                float(radar_config.site["longitude_deg"]),
                float(radar_config.site["latitude_deg"]),
            ),
            flag_masks=incomplete_flags,
        )


def test_hybrid_scan_reuses_geometry_candidate_and_dem_blockage_caches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radar_config = load_radar_config(make_config(tmp_path))
    grid = small_grid(
        float(radar_config.site["longitude_deg"]),
        float(radar_config.site["latitude_deg"]),
    )
    profile = replace(
        load_radar_grid_profile(PROFILE_PATH),
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )
    terrain = CountingTerrain(
        float(radar_config.site["longitude_deg"]),
        float(radar_config.site["latitude_deg"]),
    )
    caches = create_hybrid_scan_caches(
        candidate_budget_bytes=1_000_000,
        geometry_budget_bytes=1_000_000,
        dem_budget_bytes=1_000_000,
    )

    import rainpulse_algo.radar.hybrid as hybrid_module

    counts = {"mapping": 0, "blockage": 0, "candidate": 0}
    original_mapping = hybrid_module.map_grid_to_polar
    original_blockage = hybrid_module.calculate_polar_blockage
    original_candidate = hybrid_module._candidate_fields

    def count_mapping(*args: object, **kwargs: object):
        counts["mapping"] += 1
        return original_mapping(*args, **kwargs)

    def count_blockage(*args: object, **kwargs: object):
        counts["blockage"] += 1
        return original_blockage(*args, **kwargs)

    def count_candidate(*args: object, **kwargs: object):
        counts["candidate"] += 1
        return original_candidate(*args, **kwargs)

    monkeypatch.setattr(hybrid_module, "map_grid_to_polar", count_mapping)
    monkeypatch.setattr(hybrid_module, "calculate_polar_blockage", count_blockage)
    monkeypatch.setattr(hybrid_module, "_candidate_fields", count_candidate)

    first = build_hybrid_scan(
        qc_fixture(radar_config.config_version),
        radar_config=radar_config,
        grid=grid,
        profile=profile,
        terrain=terrain,
        flag_masks=flag_masks(),
        caches=caches,
    )
    second = build_hybrid_scan(
        qc_fixture(radar_config.config_version),
        radar_config=radar_config,
        grid=grid,
        profile=profile,
        terrain=terrain,
        flag_masks=flag_masks(),
        caches=caches,
    )

    assert counts == {"mapping": 2, "blockage": 2, "candidate": 2}
    assert terrain.sample_calls == 2
    assert first.cache_stats["geometry_miss"] == 2
    assert first.cache_stats["candidate_miss"] == 2
    assert first.cache_stats["dem_miss"] == 2
    assert second.cache_stats["geometry_hit"] == 2
    assert second.cache_stats["candidate_hit"] == 2
    assert second.cache_stats["dem_hit"] == 2
    np.testing.assert_allclose(first.fields["DBZH_QC"], second.fields["DBZH_QC"])


def test_verified_dem_tile_store_reuses_shared_byte_budget_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = dem_source_fixture()
    tile_path = write_dem_runtime_fixture(tmp_path, source)
    cache = SharedDEMCache(4 * 1024)
    values = np.arange(16, dtype="float32").reshape(4, 4)
    open_calls = 0

    def fake_open(path: Path) -> FakeRasterDataset:
        nonlocal open_calls
        open_calls += 1
        assert Path(path) == tile_path
        return FakeRasterDataset(values, (117.0, 27.0, 118.0, 28.0))

    monkeypatch.setattr("rainpulse_algo.radar.dem.rasterio.open", fake_open)

    first = VerifiedDEMTileStore(
        source,
        tmp_path,
        expected_asset_version=source.dem.asset_version,
        expected_config_version=source.config_version,
        cache=cache,
    )
    second = VerifiedDEMTileStore(
        source,
        tmp_path,
        expected_asset_version=source.dem.asset_version,
        expected_config_version=source.config_version,
        cache=cache,
    )

    sampled_first = first.sample(np.array([117.25]), np.array([27.75]))
    sampled_second = second.sample(np.array([117.25]), np.array([27.75]))

    assert sampled_first[0] == pytest.approx(5.0)
    assert sampled_second[0] == pytest.approx(5.0)
    assert open_calls == 1
    assert first.cache_stats() == {"hits": 0, "misses": 1}
    assert second.cache_stats() == {"hits": 1, "misses": 0}


def test_verified_dem_tile_store_skips_cache_when_tile_exceeds_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = dem_source_fixture()
    tile_path = write_dem_runtime_fixture(tmp_path, source)
    cache = SharedDEMCache(32)
    values = np.arange(16, dtype="float32").reshape(4, 4)
    open_calls = 0

    def fake_open(path: Path) -> FakeRasterDataset:
        nonlocal open_calls
        open_calls += 1
        assert Path(path) == tile_path
        return FakeRasterDataset(values, (117.0, 27.0, 118.0, 28.0))

    monkeypatch.setattr("rainpulse_algo.radar.dem.rasterio.open", fake_open)

    first = VerifiedDEMTileStore(
        source,
        tmp_path,
        expected_asset_version=source.dem.asset_version,
        expected_config_version=source.config_version,
        cache=cache,
    )
    second = VerifiedDEMTileStore(
        source,
        tmp_path,
        expected_asset_version=source.dem.asset_version,
        expected_config_version=source.config_version,
        cache=cache,
    )

    first.sample(np.array([117.25]), np.array([27.75]))
    second.sample(np.array([117.25]), np.array([27.75]))

    assert open_calls == 2
    assert first.cache_stats() == {"hits": 0, "misses": 1}
    assert second.cache_stats() == {"hits": 0, "misses": 1}
