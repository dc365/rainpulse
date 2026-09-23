"""pytest 测试：转换符号、格网内插、GNSS 路线、已知点回归。"""

import os

import pytest

from heightdatum import (GeoidGrid, egm2008_from_1985, egm2008_from_gnss, h1985_from_egm2008,
                         h1985_from_gnss, uncertainty_budget)
from heightdatum.convert import local_offset_from_anchor

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "egm2008_fujian_2p5.npy")


@pytest.fixture(scope="module")
def grid():
    return GeoidGrid.load(DATA)


def test_geoid_grid_known_point(grid):
    # GeographicLib 官方计算器（1′ 格网）：福州 (119.3E, 26.07N) N = +11.2391 m
    n = grid.sample(119.3, 26.07)
    assert abs(n - 11.2391) < 0.02  # 2.5′ 格网与 1′ 官方值差异应 < 2 cm


def test_geoid_grid_bounds(grid):
    with pytest.raises(ValueError):
        grid.sample(130.0, 26.0)  # 出界不外推


def test_conversion_roundtrip():
    h85 = 641.0
    h_egm = egm2008_from_1985(h85)
    assert abs(h_egm - (h85 + 0.32)) < 1e-9
    assert abs(h1985_from_egm2008(h_egm) - h85) < 1e-9


def test_gnss_route(grid):
    # 若实测椭球高 h = H_egm + N，则 H_egm = h − N 必须自洽
    lon, lat = 119.54056, 25.99139
    n = grid.sample(lon, lat)
    h_egm = 641.32
    h = h_egm + n
    assert abs(egm2008_from_gnss(h, lon, lat, geoid_grid=grid) - h_egm) < 1e-9
    # H_1985 = h − N − δ
    assert abs(h1985_from_gnss(h, lon, lat, geoid_grid=grid) - (641.32 - 0.32)) < 1e-9


def test_local_offset_recovery(grid):
    # 构造：某站 h = H_1985 + δ_true + N；反算 δ 应还原 δ_true
    lon, lat = 117.08056, 27.00861
    delta_true = 0.31
    h = 1740.0 + delta_true + grid.sample(lon, lat)
    d = local_offset_from_anchor(h, 1740.0, lon, lat, geoid_grid=grid)
    assert abs(d - delta_true) < 1e-9


def test_uncertainty_budget():
    lit = uncertainty_budget(method="literature", mountainous=True)
    assert 0.05 < lit["total_rss"] < 0.2
    gnss = uncertainty_budget(method="gnss")
    assert gnss["total_rss"] < lit["total_rss"]
