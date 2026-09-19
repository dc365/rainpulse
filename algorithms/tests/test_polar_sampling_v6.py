import numpy as np
import pytest

from rainpulse_algo.diagnostics.polar_sampling import polar_pixels, project_rgba


def test_sector_is_not_extrapolated_into_unmeasured_quadrants():
    az = np.arange(20.0, 31.0)
    r = np.arange(1000.0, 101000.0, 1000.0)
    rgba = np.full((len(az), len(r), 4), 255, "uint8")
    out = project_rgba(rgba, az, r, 201)
    assert not out[100:, :, 3].any()  # southern half unobserved
    assert not out[:, :100, 3].any()  # west unobserved
    assert out[:, :, 3].any()


def test_missing_range_does_not_extrapolate_to_radar_centre():
    p = polar_pixels(np.arange(360.0), np.arange(10000, 201000, 1000), 201)
    assert not p.available[100, 100]
    assert p.available[0, 100]


def test_periodic_seam_and_duplicate_azimuth():
    p = polar_pixels(np.r_[359.0, 0.0, 1.0], np.arange(1000.0, 101000.0, 1000.0), 201)
    assert p.available[10, 100]
    assert p.ray[10, 100] == 1
    dup = polar_pixels(np.r_[359.0, 0.0, 0.0, 1.0], np.arange(1000.0, 101000.0, 1000.0), 201)
    assert not dup.available[10, 100]


def test_transparent_source_stays_transparent_and_trace_matches_colour():
    az = np.arange(360.0)
    r = np.arange(1000.0, 101000.0, 1000.0)
    p = polar_pixels(az, r, 201)
    rgba = np.zeros((360, 100, 4), "uint8")
    assert not project_rgba(rgba, az, r, 201).any()
    rgba[0, 49] = [10, 20, 30, 255]
    out = project_rgba(rgba, az, r, 201)
    found = np.argwhere(out[:, :, 3] > 0)
    assert len(found)
    for row, col in found:
        assert (p.ray[row, col], p.gate[row, col]) == (0, 49)


@pytest.mark.parametrize(
    "az,r,size",
    [([], [1, 2], 100), ([0, 1], [2, 1], 100), ([0, np.nan], [1, 2], 100), ([0, 1], [1, 2], 10000)],
)
def test_invalid_geometry_refused(az, r, size):
    with pytest.raises(ValueError):
        polar_pixels(az, r, size)


def test_polar_layer_declares_sampler_version():
    from rainpulse_algo.diagnostics.renderer import _store_layer

    store = {}
    layer = _store_layer(
        store,
        layer_id="test",
        title="test",
        scope="polar",
        field="DBZH_RAW",
        rendering="scalar",
        unit="dBZ",
        rgba=np.zeros((10, 10, 4), "uint8"),
        palette_version="test",
        legend=[],
        sampling_version="native-footprint-v2",
    )
    assert layer["sampling_version"] == "native-footprint-v2"
    assert layer["object_path"] in store


def test_versioned_renderer_preserves_legacy_and_selects_new_footprint(tmp_path):
    from rainpulse_algo.diagnostics.profile import load_diagnostic_profile
    from rainpulse_algo.diagnostics.renderer import (
        build_diagnostic_bundle,
        validate_diagnostic_bundle,
    )

    from .test_diagnostics import (
        ANALYSIS_ID,
        DIAGNOSTIC_CONFIG,
        JOB_ID,
        SCAN_ID,
        analysis_fixture,
        flag_definitions,
        qc_fixture,
    )

    old = load_diagnostic_profile(DIAGNOSTIC_CONFIG)
    raw = qc_fixture(tmp_path)
    for version, expected in [
        ("radar-diagnostic-renderer-1.1.0", "native-footprint-v2"),
        ("radar-diagnostic-renderer-1.2.0", "native-footprint-v2"),
        ("radar-diagnostic-renderer-1.4.0", "native-footprint-v2"),
        ("radar-diagnostic-renderer-9.0.0", "native-footprint-v2"),
    ]:
        objects = build_diagnostic_bundle(
            analysis_fixture(),
            [("z9598", SCAN_ID, raw)],
            analysis_uri="s3://test/analysis",
            analysis_id=ANALYSIS_ID,
            job_id=JOB_ID,
            profile=old.model_copy(update={"renderer_version": version}),
            flag_definitions=flag_definitions(),
        )
        manifest = validate_diagnostic_bundle(objects)["manifest"]
        for layer in manifest["layers"]:
            assert layer.get("sampling_version") == (
                expected if layer["scope"] == "polar" else None
            )
