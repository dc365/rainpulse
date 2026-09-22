import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.diagnostics.composite import composite_reflectivity


def volume(lon=117.0):
    root = zarr.group(store=MemoryStore())
    root.attrs.update(site_longitude_deg=lon, site_latitude_deg=28.0,
                      flag_definition_version="qc-flags-v2")
    root.array("sweep_number", np.array([0, 1]))
    for number, value in enumerate([20., 40.]):
        sweep = root.create_group(f"sweep_{number:03d}")
        sweep.array("range", np.arange(1000., 101000., 1000.))
        sweep.array("azimuth", np.arange(360.))
        sweep.array("elevation", np.full(360, number))
        sweep.array("DBZH_QC", np.full((360, 100), value, dtype="float32"))
        sweep.array("VALID_MASK", np.ones((360, 100), dtype="uint8"))
        sweep.array("QPE_ELIGIBLE_MASK", np.ones((360, 100), dtype="uint8"))
        sweep.array("REFLECTIVITY_ELIGIBLE_FOR_CR", np.ones((360, 100), dtype="uint8"))
        sweep.array("QC_FLAGS", np.zeros((360, 100), dtype="uint32"))
    return root


def test_full_extent_vertical_maximum_and_quarantine():
    root = volume()
    values, bounds = composite_reflectivity([root], 1, maximum_size=128)
    assert bounds[0] < 117 < bounds[2] and bounds[1] < 28 < bounds[3]
    assert bounds[3] > 28.8  # Outside the old 25..27 forecast domain.
    assert np.nanmax(values) == 40
    root["sweep_001/REFLECTIVITY_ELIGIBLE_FOR_CR"][:] = 0
    values, _ = composite_reflectivity([root], 1, maximum_size=128)
    assert np.nanmax(values) == 20
    # QPE eligibility must not re-admit a gate withheld from CR display.
    root["sweep_001/QPE_ELIGIBLE_MASK"][:] = 1
    root["sweep_001/REFLECTIVITY_ELIGIBLE_FOR_CR"][:] = 0
    values, _ = composite_reflectivity([root], 1, maximum_size=128)
    assert np.nanmax(values) == 20
    root["sweep_000/QC_FLAGS"][:] = 1
    values, _ = composite_reflectivity([root], 1, maximum_size=128)
    assert np.isnan(values).all()


def test_union_extent_preserves_unobserved_gap():
    values, bounds = composite_reflectivity([volume(115), volume(120)], 1, maximum_size=128)
    assert bounds[0] < 115 and bounds[2] > 120
    assert np.isnan(values[:, values.shape[1] // 2]).all()


def test_winning_gate_provenance_follows_maximum_and_eligibility():
    root = volume()
    values, _, sources = composite_reflectivity([root], 1, maximum_size=64, return_sources=True)
    valid = np.isfinite(values)
    assert (sources['sweep'][values == 40] == 1).all()
    assert (sources['sweep'][values == 20] == 0).all()
    assert (sources['radar'][valid] == 0).all()
    assert (sources['gate'][~valid] == -1).all()
    for number in (0, 1):
        selected = valid & (sources['sweep'] == number)
        assert np.array_equal(root[f'sweep_{number:03d}/DBZH_QC'][:][sources['ray'][selected], sources['gate'][selected]], values[selected])
    root['sweep_001/REFLECTIVITY_ELIGIBLE_FOR_CR'][:] = 0
    values, _, sources = composite_reflectivity([root], 1, maximum_size=64, return_sources=True)
    assert (sources['sweep'][np.isfinite(values)] == 0).all()
