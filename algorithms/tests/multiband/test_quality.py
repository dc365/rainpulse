# ruff: noqa: E501, E701, E702, I001, E402
from dataclasses import replace
import numpy as np
import pytest

from rainpulse_algo.multiband.quality import x_qc, accept_s_qc, phase_linear, Flag
from rainpulse_algo.multiband.model import XProfile
from rainpulse_algo.multiband.product import polar_quicklook, x_qc_objects
from conftest import volume


def test_upstream_correction_not_applied_twice(net):
    s = net.stations["x1"]; v = volume(s)
    original = v.sweeps[0].fields["DBZH"].copy()
    out = x_qc(v, s, net.sha256)
    np.testing.assert_array_equal(out.sweeps[0].fields["DBZH_QC"], original)
    np.testing.assert_array_equal(v.sweeps[0].fields["DBZH"], original)
    assert np.all(out.sweeps[0].fields["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1)
    assert not np.any(out.sweeps[0].fields["QPE_ELIGIBLE_MASK"])
    with pytest.raises(ValueError, match="double"):
        x_qc(v, replace(s, x_qc=XProfile(attenuation="phidp_linear", alpha_db_per_degree=.2)), net.sha256)


def test_expanded_sweep_limit_applies_only_to_streamed_x_qc(net):
    station = net.stations["x1"]
    v = volume(station)
    v.sweeps = [replace(v.sweeps[0], number=i) for i in range(33)]
    v.validate(station, require_geometry=False)
    with pytest.raises(ValueError, match="unsupported or incomplete"):
        v.validate(station)


def test_polar_preview_keeps_large_azimuth_gaps_transparent():
    import struct
    import zlib

    def alpha(image, x, y):
        offset = 8
        compressed = bytearray()
        while offset < len(image):
            size = struct.unpack_from('>I', image, offset)[0]
            name = image[offset + 4:offset + 8]
            payload = image[offset + 8:offset + 8 + size]
            offset += 12 + size
            if name == b'IDAT':
                compressed.extend(payload)
        stride = 1 + 101 * 4
        rows = zlib.decompress(compressed)
        return rows[y * stride + 1 + x * 4 + 3]

    ranges = np.array([100., 500.])
    one = polar_quicklook(np.array([0.]), ranges, np.full((1, 2), 30.), size=101)
    two = polar_quicklook(np.array([0., 180.]), ranges, np.full((2, 2), 30.), size=101)
    assert alpha(one, 50, 50) == 255
    assert alpha(one, 100, 50) == 0
    assert alpha(two, 100, 50) == 0


def test_unknown_attenuation_is_uncertain_not_missing_or_no_rain(net):
    s = replace(net.stations["x1"], x_qc=XProfile())
    v = volume(s); v.metadata["attenuation_status"] = "unknown"
    f = x_qc(v, s, net.sha256).sweeps[0].fields
    assert np.all(f["OBSERVED_MASK"] == 1) and not np.any(f["NO_ECHO_MASK"])
    assert np.all(f["DBZH_QC"] == 35) and not np.any(f["REFLECTIVITY_ELIGIBLE_FOR_CR"])
    assert np.all(f["CR_UNCERTAIN_MASK"] == 1)
    assert np.all(f["QC_ACTION"] == 3) and np.all(f["DBZH_QC_DISPLAY"] == 35)


@pytest.mark.parametrize("noecho,missing", [(False, True), (True, False)])
def test_three_states_preserved(net, noecho, missing):
    s = net.stations["x1"]; v = volume(s, noecho=noecho, missing=missing)
    f = x_qc(v, s, net.sha256).sweeps[0].fields
    assert np.isnan(f["DBZH_QC"]).all()
    assert np.all(f["OBSERVED_MASK"] == (not missing))
    assert np.all(f["NO_ECHO_MASK"] == noecho)
    assert np.all(f["REFLECTIVITY_ELIGIBLE_FOR_CR"] == noecho)


@pytest.mark.parametrize("kind", ["missing_snr", "uncalibrated", "confirmed", "blocked"])
def test_quality_gates(net, kind):
    s = net.stations["x1"]; v = volume(s)
    f = v.sweeps[0].fields
    if kind == "missing_snr": del f["SNRH"]
    elif kind == "uncalibrated": s = replace(s, calibration_verified=False)
    elif kind == "confirmed": f["CONFIRMED_NONMET_MASK"] = np.ones_like(f["OBSERVED_MASK"])
    else: f["BLOCKAGE_FRACTION"] = np.full_like(f["DBZH"], .9)
    assert not np.any(x_qc(v, s, net.sha256).sweeps[0].fields["REFLECTIVITY_ELIGIBLE_FOR_CR"])


def test_low_rho_alone_does_not_delete_hail_or_weak_weather(net):
    s = net.stations["x1"]; v = volume(s, values=(-5.,))
    v.sweeps[0].fields["RHOHV"][:] = .5
    f = x_qc(v, s, net.sha256).sweeps[0].fields
    assert np.all(f["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1)
    assert not np.any(f["MB_QC_FLAGS"] & int(Flag.NONMET_CONFIRMED))


def test_x_qc_preview_keeps_uncertain_echo_and_marks_confirmed_reject(net):
    s = net.stations["x1"]
    v = volume(s)
    v.sweeps[0].fields["SNRH"][0, 0] = -10
    v.sweeps[0].fields["CONFIRMED_NONMET_MASK"] = np.zeros_like(v.sweeps[0].fields["OBSERVED_MASK"])
    v.sweeps[0].fields["CONFIRMED_NONMET_MASK"][0, 1] = 1
    f = x_qc(v, s, net.sha256).sweeps[0].fields
    assert f["QC_ACTION"][0, 0] == 3
    assert f["QC_ACTION"][0, 1] == 2
    assert f["DBZH_QC_DISPLAY"][0, 0] == f["DBZH_RAW"][0, 0]
    assert np.isnan(f["DBZH_QC_DISPLAY"][0, 1])
    objects = x_qc_objects(x_qc(v, s, net.sha256))
    manifest = __import__("json").loads(objects["manifest.json"])
    assert manifest["comparison"]["sweeps"][0]["raw"] in objects
    assert manifest["comparison"]["sweeps"][0]["qc"] in objects
    assert manifest["comparison"]["sweeps"][0]["flags"] in objects
    assert all(objects[key].startswith(b"\x89PNG\r\n\x1a\n") for key in manifest["comparison"]["sweeps"][0].values() if isinstance(key, str) and key.endswith(".png"))


def test_phase_scaling_break_and_limit(net):
    s = net.stations["x1"]; v = volume(s); cut = v.sweeps[0]
    shape = cut.fields["DBZH"].shape
    cut.fields["PHIDP"] = np.broadcast_to(np.arange(shape[1], dtype=np.float32), shape).copy()
    cut.fields["PHASE_VALID_MASK"] = np.ones(shape, np.uint8)
    cut.fields["LIQUID_MASK"] = np.ones(shape, np.uint8)
    cfg = XProfile(attenuation="phidp_linear", alpha_db_per_degree=.2, max_pia_db=30)
    pia, kdp, limit = phase_linear(cut, cfg, anchor_verified=True, initial_pia_db=0)
    assert pia[0, 10] == pytest.approx(2.)  # not 4: PhiDP is already two-way
    assert kdp[0, 10] == pytest.approx(1.)
    assert not limit.any()
    cut.fields["PHASE_VALID_MASK"][0, 8] = 0
    pia, _, _ = phase_linear(cut, cfg, anchor_verified=True, initial_pia_db=0)
    assert np.isnan(pia[0, 8:]).all() and np.isfinite(pia[0, :8]).all()
    pia, _, limited = phase_linear(cut, replace(cfg, max_pia_db=1), anchor_verified=True, initial_pia_db=0)
    assert limited[1, 6:].all() and np.isnan(pia[1, 6:]).all()
    assert not np.isfinite(phase_linear(cut, cfg, anchor_verified=False, initial_pia_db=0)[0]).any()


def test_s_masks_and_values_unchanged(net):
    s = net.stations["s1"]; v = volume(s)
    v.sweeps[0].fields["REFLECTIVITY_ELIGIBLE_FOR_CR"][0, 0] = 0
    out = accept_s_qc(v, s, net.sha256)
    np.testing.assert_array_equal(out.sweeps[0].fields["DBZH_QC"], v.sweeps[0].fields["DBZH_QC"])
    assert out.sweeps[0].fields["REFLECTIVITY_ELIGIBLE_FOR_CR"][0, 0] == 0
    with pytest.raises(ValueError, match="not approved"):
        accept_s_qc(v, replace(s, allowed_s_qc_versions=()), net.sha256)


def test_x_reflectivity_palette_matches_s_renderer_and_preserves_qc_strength(net):
    from rainpulse_algo.diagnostics.renderer import REFLECTIVITY_STOPS, _scalar_rgba
    from rainpulse_algo.multiband.product import LEVELS, COLORS, quicklook
    from PIL import Image
    from io import BytesIO
    # Exercise exact boundaries, below/above-range clamping, and missing values.
    values = np.array([[-10., 4.9, 5., 9.9, 10., 34.9, 35., 70., 80., np.nan]])
    pixels = np.asarray(Image.open(BytesIO(quicklook(values))))
    expected = _scalar_rgba(values, np.isfinite(values), REFLECTIVITY_STOPS)
    np.testing.assert_array_equal(pixels[..., 3], expected[..., 3])
    np.testing.assert_array_equal(pixels[np.isfinite(values)], expected[np.isfinite(values)])
    assert LEVELS.tolist() == list(range(5, 71, 5))
    assert COLORS[6].tolist() == [231, 192, 0]
    station = net.stations["x1"]
    v = volume(station)
    v.sweeps[0].fields["SNRH"][:] = -10
    output = x_qc(v, station, net.sha256)
    assert np.any(output.sweeps[0].fields["QC_ACTION"] == 3)
    objects = x_qc_objects(output)
    # Uncertain strength keeps the common color; amber only occurs in flags.
    assert objects["sweeps/0/raw.png"] == objects["sweeps/0/qc.png"]
    assert objects["sweeps/0/flags.png"] != objects["sweeps/0/qc.png"]
