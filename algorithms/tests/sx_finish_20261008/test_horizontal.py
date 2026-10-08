# ruff: noqa: E501, I001
import copy
import numpy as np
import pytest
from types import SimpleNamespace as NS
from pyproj import Transformer
from rainpulse_algo.multiband.fusion import allocate_output, EARTH_EFFECTIVE_M, GEOD, _nearest_ray
from rainpulse_algo.multiband.experimental import update_samples
from rainpulse_algo.multiband.horizontal_plan import HorizontalPlan, fuse_sweep

def parent(s, station, values, admitted, quality, native_ray, lon, lat, target, index, outputs, g):
    for y in range(0, g.height, g.tile_rows):
        sl = slice(y, min(y + g.tile_rows, g.height))
        xx, yy = np.meshgrid(g.west_m + (np.arange(g.width) + 0.5) * g.spacing_m, g.south_m + (np.arange(sl.start, sl.stop) + 0.5) * g.spacing_m)
        glon, glat = Transformer.from_crs(g.crs, 'EPSG:4326', always_xy=True).transform(xx, yy)
        bearing, _, distance = GEOD.inv(np.full(xx.shape, lon), np.full(xx.shape, lat), glon, glat)
        ray, offset = _nearest_ray(s.azimuth_deg, bearing % 360)
        arc = distance / EARTH_EFFECTIVE_M
        denominator = np.cos(np.deg2rad(s.elevation_deg[ray]) + arc)
        slant = EARTH_EFFECTIVE_M * np.sin(arc) / np.maximum(denominator, 1e-09)
        ranges = s.range_m
        pos = np.clip(np.searchsorted(ranges, slant), 1, len(ranges) - 1)
        gate = np.where(abs(slant - ranges[pos - 1]) <= abs(slant - ranges[pos]), pos - 1, pos)
        widths = np.r_[np.diff(ranges), np.diff(ranges)[-1]]
        age = target - s.ray_time_epoch[ray]
        support = (offset <= 1.0) & (denominator > 0) & (abs(slant - ranges[gate]) <= widths[gate] / 2) & (age >= 0) & (age <= station.maximum_age_seconds)
        for band in (station.band, 'S+X'):
            update_samples({k: v[sl] for k, v in outputs[band].items()}, values[ray, gate], support & admitted[ray, gate], native_ray[ray], gate, age, quality[ray, gate], index, s.number)

@pytest.mark.parametrize('irregular', [False, True])
@pytest.mark.parametrize('elevation', [-2.0, 0.5, 25.0])
@pytest.mark.parametrize('capacity', [0, 10000000])
def test_equivalent_native_samples_and_far_skips(irregular, elevation, capacity):
    rng = np.random.default_rng(12)
    az = np.arange(180) * 2.0
    ranges = np.arange(80) * 250.0 + 125
    if irregular:
        az = az[np.arange(180) % 7 != 0]
        ranges = np.cumsum(rng.uniform(125, 650, 80))
    shape = (len(az), len(ranges))
    z = rng.uniform(-5, 60, shape)
    obs = rng.random(shape) > 0.1
    no = (rng.random(shape) < 0.08) & obs
    z[~obs | no] = np.nan
    admitted = obs & (rng.random(shape) > 0.1)
    q = rng.random(shape)
    s = NS(azimuth_deg=az, range_m=ranges, elevation_deg=np.full(len(az), elevation), ray_time_epoch=np.full(len(az), 1000.0), number=7, fields={'DBZH': z, 'OBSERVED_MASK': obs, 'NO_ECHO_MASK': no})
    tx = Transformer.from_crs(4326, 32651, always_xy=True)
    x, y = tx.transform(120.0, 26.0)
    g = NS(grid_id='g', crs='EPSG:32651', west_m=x - 90000, south_m=y - 90000, spacing_m=3000, width=60, height=60, tile_rows=4, levels_m_msl=(0.0,), method='experimental_horizontal_max')

    def output():
        out = {b: allocate_output(g) for b in ('S', 'X', 'S+X')}
        for v in out.values():
            v['WINNER_SWEEP_NUMBER'] = np.full((g.height, g.width), -1, np.int32)
        return out
    before = copy.deepcopy(s)
    a, b = (output(), output())
    st = NS(band='X', radar_id='x', maximum_age_seconds=600)
    native = np.arange(len(az))[::-1]
    parent(s, st, z, admitted, q, native, 120.0, 26.0, 1060.0, 0, a, g)
    plan = HorizontalPlan(g, capacity)
    fuse_sweep(plan, s, st, z, admitted, q, native, 120.0, 26.0, 1060.0, 0, b)
    assert plan.tiles_skipped > 0
    for band in a:
        for k in a[band]:
            np.testing.assert_array_equal(a[band][k], b[band][k], err_msg=k)
    for k in s.fields:
        np.testing.assert_array_equal(s.fields[k], before.fields[k])
    old = plan.metrics()
    fuse_sweep(plan, s, st, z, admitted, q, native, 120.0, 26.0, 1060.0, 0, b)
    if capacity:
        assert plan.cache.hits > old['geometry_hits']
    assert plan.cache.peak <= capacity
