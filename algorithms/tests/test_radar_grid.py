from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import zarr
from pyproj import Geod
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.radar.blockage import (
    beam_centre_height_m,
    circular_partial_blockage,
    map_grid_to_polar,
)
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.grid_profile import load_radar_grid_profile
from rainpulse_algo.radar.grid_zarr import (
    build_radar_grid_zarr_store,
    validate_radar_grid_zarr_store,
)
from rainpulse_algo.radar.hybrid import RadarGridInputError, build_hybrid_scan

from .test_fmt_decoder import make_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "gridding" / "rp009-hybrid-v1.1.yaml"
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
