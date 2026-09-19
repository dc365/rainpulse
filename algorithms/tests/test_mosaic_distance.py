from dataclasses import replace

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.mosaic import RadarMosaicInput, RadarMosaicInputError, build_radar_mosaic

from .test_radar_mosaic import ANALYSIS_TIME, flag_masks, profile, radar_grid_fixture, small_grid


def inputs(distances, values):
    out = []
    for i, (r, z) in enumerate(zip(distances, values)):
        radar = f"z{i + 1}"
        scan = f"90000000-0000-4000-8000-{i + 1:012d}"
        store = MemoryStore()
        store.update(radar_grid_fixture(radar, scan, np.full((2, 2), z), np.full((2, 2), 0.8)))
        root = zarr.open_group(store, mode="a")
        root.create_dataset("GROUND_RANGE", data=np.full((2, 2), r, dtype="float32"))
        root["GROUND_RANGE"].attrs["units"] = "m"
        out.append(
            RadarMosaicInput(radar, scan, f"s3://test/{radar}", 0, "hybrid-scan-1.1.0", dict(store))
        )
    return out


def run(data):
    p = profile()
    p = replace(
        p,
        alignment=replace(p.alignment, minimum_contributors=1),
        fusion=replace(p.fusion, method="qi_distance_linear_z_blend", distance_scale_km=100.0),
    )
    return build_radar_mosaic(
        data, analysis_time=ANALYSIS_TIME, grid=small_grid(), profile=p, flag_masks=flag_masks()
    )


def test_gaussian_linear_z_weighting():
    result = run(inputs([0, 100000], [20, 40]))
    expected = 10 * np.log10((100 + np.exp(-1) * 10000) / (1 + np.exp(-1)))
    np.testing.assert_allclose(result.fields["DBZH_QC"], expected, rtol=1e-6)


def test_single_far_source_is_not_artificially_dimmer():
    result = run(inputs([10000000], [40]))
    np.testing.assert_allclose(result.fields["DBZH_QC"], 40.0)


def test_missing_remains_missing_and_never_counts_as_zero_rain():
    result = run(inputs([100000, 200000], [np.nan, 30]))
    np.testing.assert_allclose(result.fields["DBZH_QC"], 30.0)
    assert (result.fields["CONTRIBUTOR_COUNT"] == 1).all()


@pytest.mark.parametrize("distance", [np.nan, -1, np.inf])
def test_invalid_distance_on_valid_echo_fails(distance):
    with pytest.raises(RadarMosaicInputError, match="GROUND_RANGE"):
        run(inputs([distance], [30]))


def test_hard_rejected_near_echo_cannot_outvote_clean_far_echo():
    data = inputs([0, 150000], [70, 20])
    store = MemoryStore()
    store.update(data[0].objects)
    root = zarr.open_group(store, mode="a")
    root["QC_FLAGS"][:] = flag_masks()["RADIAL_INTERFERENCE"]
    data[0] = replace(data[0], objects=dict(store))
    result = run(data)
    np.testing.assert_allclose(result.fields["DBZH_QC"], 20.0)
    assert (result.fields["CONTRIBUTOR_COUNT"] == 1).all()


def test_distance_units_must_be_explicit_metres():
    data = inputs([100], [30])
    store = MemoryStore()
    store.update(data[0].objects)
    zarr.open_group(store, mode="a")["GROUND_RANGE"].attrs["units"] = "km"
    with pytest.raises(RadarMosaicInputError, match="GROUND_RANGE"):
        run([replace(data[0], objects=dict(store))])


def test_all_missing_preserves_no_coverage():
    result = run(inputs([np.nan, np.nan], [np.nan, np.nan]))
    assert not result.fields["VALID_MASK"].any()
    assert np.isnan(result.fields["DBZH_QC"]).all()
