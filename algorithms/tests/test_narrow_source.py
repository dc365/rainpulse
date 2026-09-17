from types import SimpleNamespace

import numpy as np

from rainpulse_algo.radar.qc_engine.narrow_source import narrow_source


def test_long_spike_and_missing_side_weather_are_distinct():
    z = np.full((5, 800), 20.0)
    z[2] = 60
    n = SimpleNamespace(
        shape=z.shape,
        fields={"DBZH": z},
        field_available={"DBZH": np.isfinite(z)},
        geometry_good=np.ones(5, bool),
        gap_after=np.array([0, 0, 0, 0, 1], bool),
        full_ppi=False,
        gate_spacing_m=250.0,
    )
    ref = np.zeros(z.shape, bool)
    ref[2] = True
    res = np.full(z.shape, np.nan)
    res[2] = 0
    weather = np.zeros(z.shape, bool)
    weather[2, 400:420] = True
    mask, reasons = narrow_source(n, ref, res, weather=weather)
    assert mask[2, 100] and not mask[2, 410]
    n.field_available["DBZH"][1] = False
    mask, reasons = narrow_source(n, ref, res)
    assert not mask.any() and (reasons[2] & 2).all()
