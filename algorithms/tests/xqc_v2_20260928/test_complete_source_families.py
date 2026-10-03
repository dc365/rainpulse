"""A complete measured lobe must not disappear at an arbitrary angular cutoff."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.radial_source import detect

from .helpers import config
from .test_radial_source import narrow_source


def wide(bearing=180, half_width=60, elevation=6.0):
    v, _ = narrow_source()
    cut = v.sweeps[0]
    cut.elevation_deg[:] = elevation
    f = cut.fields
    for offset in range(-half_width, half_width + 1):
        row = (bearing + offset) % 360
        power = 40.0 - abs(offset) * 0.4
        f["SNRH"][row] = power
        f["DBZH"][row] = power + 20 * np.log10(cut.range_m / 1000) - 22
    f["OBSERVED_MASK"][:] = np.isfinite(f["DBZH"])
    return cut


def run(cut, *, enabled=True, protected=None):
    c = config(
        radial_source_enabled=True, radial_source_fan_model_enabled=True, noise_censor_snr_db=3.0
    )
    # Freeze the legacy child policy in memory for the actual RED run. The
    # public strict configuration contract is tested separately.
    c = c.model_copy(update={"complete_source_families_enabled": enabled})
    s = adapt(cut, c).sweep
    mask, record = detect(
        s, c, protected=np.zeros(s.shape, bool) if protected is None else protected
    )
    return s.original_indices, mask, record


@pytest.mark.parametrize("bearing,elevation", [(180, 0.47), (350, 3.36), (70, 9.88)])
def test_complete_wide_source_uses_measured_bilateral_family(bearing, elevation):
    cut = wide(bearing=bearing, elevation=elevation)
    order, old, _ = run(cut, enabled=False)
    row = int(np.flatnonzero(order == bearing)[0])
    target = (cut.range_m > 25000) & np.isfinite(cut.fields["DBZH"][bearing])
    assert not old[row, target].any()  # actual legacy failure, not a vacuous negative
    _, new, record = run(cut)
    assert new[row, target].mean() > 0.9
    assert record["complete"]


def test_wide_constant_ref_weather_and_added_stronger_core_stay():
    cut = wide()
    f = dict(cut.fields)
    f["DBZH"] = f["DBZH"].copy()
    region = np.arange(120, 241)
    f["DBZH"][region] = 40.0
    _, weather, _ = run(replace(cut, fields=f))
    assert not weather[region].any()
    f = dict(cut.fields)
    for name in ("DBZH", "SNRH"):
        f[name] = f[name].copy()
        f[name][180, (cut.range_m >= 35000) & (cut.range_m < 40000)] += 15
    _, mixed, _ = run(replace(cut, fields=f))
    assert not mixed[180, (cut.range_m >= 35000) & (cut.range_m < 40000)].any()


@pytest.mark.parametrize("barrier", ["missing", "gap", "full_ring", "one_shoulder", "invalid_snr"])
def test_incomplete_family_never_supplies_quiet_shoulders(barrier):
    cut = wide()
    f = {k: v.copy() for k, v in cut.fields.items()}
    if barrier == "missing":
        f["SNRH"][119] = np.nan
    elif barrier == "invalid_snr":
        f["SNRH"][119] = -999
    elif barrier in ("full_ring", "one_shoulder"):
        f["SNRH"][:] = 40
        if barrier == "one_shoulder":
            f["SNRH"][119] = -4
    elif barrier == "gap":
        keep = ~np.isin(np.arange(360), np.arange(116, 120))
        cut = replace(
            cut,
            azimuth_deg=cut.azimuth_deg[keep],
            elevation_deg=cut.elevation_deg[keep],
            ray_time_epoch=cut.ray_time_epoch[keep],
            fields={k: v[keep] for k, v in f.items()},
        )
        order, out, _ = run(cut)
        assert not out[int(np.flatnonzero(cut.azimuth_deg[order] == 180)[0])].any()
        return
    _, out, _ = run(replace(cut, fields=f))
    assert not out[180].any()


def test_complete_source_still_respects_hard_protection():
    cut = wide()
    _, out, _ = run(cut, protected=np.ones(cut.fields["DBZH"].shape, bool))
    assert not out.any()


@pytest.mark.parametrize("step,half_width", [(1, 130), (2, 60)])
def test_complete_native_wide_and_long_arc_are_measured(step, half_width):
    cut = wide(half_width=half_width)
    keep = np.arange(0, 360, step)
    cut = replace(
        cut,
        azimuth_deg=cut.azimuth_deg[keep],
        elevation_deg=cut.elevation_deg[keep],
        ray_time_epoch=cut.ray_time_epoch[keep],
        fields={k: v[keep] for k, v in cut.fields.items()},
    )
    order, out, _ = run(cut)
    row = int(np.flatnonzero(cut.azimuth_deg[order] == 180)[0])
    assert out[row, cut.range_m > 25000].mean() > 0.9


def test_additional_stage_resource_refusal_preserves_legacy_proofs(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import source_fans
    from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

    cut = wide(half_width=10)
    _, legacy, _ = run(cut, enabled=False)
    assert legacy.any()

    def refused(*args, **kwargs):
        raise ResourceLimit("frozen additional-family budget")

    monkeypatch.setattr(source_fans, "detect_complete", refused)
    _, actual, record = run(cut)
    np.testing.assert_array_equal(actual, legacy)
    assert not record["complete"] and record["failed_module"] == "complete_fan"


def test_strict_default_off_contract_and_digest():
    import hashlib
    import json

    from pydantic import ValidationError

    from rainpulse_algo.multiband.xqc_v2.config import XQCConfig

    c = XQCConfig()
    body = c.model_dump(mode="json")
    body.pop("complete_source_families_enabled")
    body.pop("complete_source_family_reference_mode")
    body.pop("polar_window_candidates_enabled")
    body.pop("fragmented_carrier_candidates_enabled")
    assert (
        c.digest
        == hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    for value in ("yes", 1):
        with pytest.raises(ValidationError):
            XQCConfig(complete_source_families_enabled=value)
    with pytest.raises(ValidationError):
        XQCConfig(complete_source_families_enabled=True)
