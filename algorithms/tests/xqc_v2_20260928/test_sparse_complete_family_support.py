"""Independent distance evidence must survive unrelated stronger native returns."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.source_family_geometry import source_view
from rainpulse_algo.multiband.xqc_v2.source_fans import detect_complete

from .test_complete_family_integration import policy
from .test_complete_source_families import wide


def mixed_corridor(bearing=180, elevation=0.47):
    cut = wide(bearing=bearing, elevation=elevation)
    f = {k: v.copy() for k, v in cut.fields.items()}
    f["DBZH"][:] = np.nan
    f["SNRH"][bearing] = 60 + 10 * np.sin(cut.range_m / 2000)
    references = cut.range_m < 30000
    target = (cut.range_m >= 60000) & (cut.range_m < 60500)
    source = references | target
    f["SNRH"][bearing, source] = 40
    f["DBZH"][bearing] = 40
    f["DBZH"][bearing, source] = 40 + 20 * np.log10(cut.range_m[source] / 1000) - 22
    f["OBSERVED_MASK"][:] = np.isfinite(f["DBZH"])
    return replace(cut, fields=f), target, source


def run(cut, protected=None):
    cfg = policy(complete_source_family_reference_mode="heldout_family")
    view = source_view(cut, cfg)
    protected = np.zeros(view.sweep.shape, bool) if protected is None else protected[view.order]
    out, record = detect_complete(view.sweep, cfg, protected=protected)
    return view.restore(out), record


@pytest.mark.parametrize("bearing,elevation", [(180, 0.47), (350, 3.36), (70, 9.88)])
def test_independent_sparse_family_does_not_require_dense_target_ray(bearing, elevation):
    cut, target, source = mixed_corridor(bearing, elevation)
    assert target.sum() >= 5 and source.mean() < 0.7
    raw = {k: v.copy() for k, v in cut.fields.items()}
    out, _ = run(cut)
    assert out[bearing, target].mean() > 0.9
    assert not out[bearing, ~source].any()
    for key, value in raw.items():
        np.testing.assert_array_equal(cut.fields[key], value)


@pytest.mark.parametrize("barrier", ["guard_only", "weather", "hard_weather"])
def test_sparse_family_retains_independent_reference_and_weather_gates(barrier):
    cut, target, source = mixed_corridor()
    f = {k: v.copy() for k, v in cut.fields.items()}
    protected = np.zeros(f["DBZH"].shape, bool)
    if barrier == "guard_only":
        guard = (cut.range_m >= 55000) & (cut.range_m < 65000)
        f["DBZH"][180, ~guard] = np.nan
    elif barrier == "weather":
        f["DBZH"][180] = 40
    else:
        protected[180, target] = True
    f["OBSERVED_MASK"][:] = np.isfinite(f["DBZH"])
    out, _ = run(replace(cut, fields=f), protected=protected)
    assert not out[180, target].any()
