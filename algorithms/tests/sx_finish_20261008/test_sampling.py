# ruff: noqa: E501, I001
import numpy as np
import pytest
from pyproj import Transformer
from rainpulse_algo.multiband.composite_sampling import Plan, sample, batch, _CACHE, MAX_PLAN_BYTES

def metadata(w=25, h=17):
    return dict(grid_id='g', crs='EPSG:32651', west_m=190000.0, south_m=2870000.0, spacing_m=1000.0, width=w, height=h, row_order='south_to_north')

def parent(values, m):
    w, h = (m['width'], m['height'])
    spacing = m['spacing_m']
    west, south = (m['west_m'], m['south_m'])
    forward = Transformer.from_crs(m['crs'], 'EPSG:4326', always_xy=True)
    bounds = forward.transform_bounds(west, south, west + w * spacing, south + h * spacing, densify_pts=41)
    size = min(1024, max(w, h, 256))
    lon = bounds[0] + (np.arange(size) + 0.5) * (bounds[2] - bounds[0]) / size
    lat = bounds[1] + (np.arange(size) + 0.5) * (bounds[3] - bounds[1]) / size
    xx, yy = Transformer.from_crs('EPSG:4326', m['crs'], always_xy=True).transform(*np.meshgrid(lon, lat))
    cols = np.floor((xx - west) / spacing).astype(np.int64)
    rows = np.floor((yy - south) / spacing).astype(np.int64)
    valid = (cols >= 0) & (cols < w) & (rows >= 0) & (rows < h)
    out = np.full((size, size), np.nan, np.float32)
    out[valid] = values[rows[valid], cols[valid]]
    return out

@pytest.mark.parametrize('shape', [(1, 1), (3, 9), (37, 19), (270, 301)])
@pytest.mark.parametrize('dtype', ['float32', 'float64', 'int32'])
def test_exact_parent_nearest_samples(shape, dtype):
    h, w = shape
    m = metadata(w, h)
    a = np.arange(w * h, dtype=dtype).reshape(shape)
    old = a.copy()
    p = Plan(m)
    _, new = p.sample(a)
    np.testing.assert_array_equal(new, parent(a, m))
    np.testing.assert_array_equal(old, a)
    assert new.dtype == np.float32
    assert p.nbytes <= MAX_PLAN_BYTES

def test_one_plan_for_independent_field_validity_and_new_grid():

    @batch
    def run():
        m = metadata()
        a = np.ones((17, 25))
        b = a.copy()
        b[3:10] = np.nan
        _, x = sample(a, m)
        _, y = sample(b, m)
        assert _CACHE.get()['builds'] == 1 and _CACHE.get()['hits'] == 1
        assert np.count_nonzero(np.isfinite(x)) > np.count_nonzero(np.isfinite(y))
        m['west_m'] += 1000
        sample(b, m)
        assert _CACHE.get()['builds'] == 2
    run()
    assert _CACHE.get() is None

def test_error_resets_display_cache():

    @batch
    def run():
        sample(np.zeros((1, 1)), metadata())
    with pytest.raises(ValueError):
        run()
    assert _CACHE.get() is None
