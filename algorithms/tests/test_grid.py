from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.grid import GridConfigError, load_grid_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GRID_CONFIG = REPOSITORY_ROOT / "configs" / "grids" / "fuzhou-0p01deg-v1.yaml"


def test_fuzhou_grid_coordinates_are_immutable_and_sample_compatible() -> None:
    grid = load_grid_config(GRID_CONFIG)

    assert grid.grid_id == "fuzhou_118_123_25_27_0p01deg_v1"
    assert grid.shape == (201, 501)
    assert grid.latitude.dtype == np.dtype("float32")
    assert grid.longitude.dtype == np.dtype("float32")
    assert grid.latitude[[0, -1]].tolist() == [25.0, 27.0]
    assert grid.longitude[[0, -1]].tolist() == [118.0, 123.0]
    assert np.all(np.diff(grid.latitude) > 0)
    assert np.all(np.diff(grid.longitude) > 0)
    assert grid.pixel_edge_bounds == pytest.approx((117.995, 24.995, 123.005, 27.005))
    assert grid.coordinate_sha256 == (
        "111a8653e5f227153216d100b81e4214bd2dbf3d134ee07641969be884a3d658"
    )


def test_latitude_aware_grid_metric_is_not_a_square_one_kilometre_assumption() -> None:
    grid = load_grid_config(GRID_CONFIG)
    metric = grid.metric()
    reference_row = int(np.argmin(np.abs(grid.latitude - grid.reference_latitude_deg)))

    assert metric.version == "wgs84-geod-grid-metric-v1"
    assert metric.x_spacing_m_by_latitude.shape == (201,)
    assert metric.y_spacing_m_by_latitude.shape == (201,)
    assert metric.x_spacing_m_by_latitude[reference_row] == pytest.approx(1000, abs=5)
    assert metric.y_spacing_m_by_latitude[reference_row] == pytest.approx(1108, abs=5)
    assert metric.x_spacing_m_by_latitude[0] > metric.x_spacing_m_by_latitude[-1]


def test_grid_loader_rejects_shape_that_does_not_match_inclusive_bounds(tmp_path: Path) -> None:
    invalid = GRID_CONFIG.read_text().replace("longitude: 501", "longitude: 500")
    path = tmp_path / "invalid-grid.yaml"
    path.write_text(invalid)

    with pytest.raises(GridConfigError, match="differs from bounds/interval"):
        load_grid_config(path)
