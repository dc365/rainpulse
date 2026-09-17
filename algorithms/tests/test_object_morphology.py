import numpy as np

from rainpulse_algo.radar.qc_engine.adapters import NativeSweep
from rainpulse_algo.radar.qc_engine.object_morphology import object_morphology


def native():
    shape = (12, 100)
    z = np.full(shape, np.nan)
    z[4:6, 10:90] = 50
    return NativeSweep(
        "test",
        np.arange(12.0),
        np.full(12, 0.5),
        np.arange(100) * 250.0 + 125,
        np.arange(12.0),
        {"DBZH": z},
        {"DBZH": np.isfinite(z)},
        np.arange(12),
        False,
        np.ones(12, bool),
        np.r_[np.zeros(11, bool), True],
        {},
        {},
    )


def test_raw_objects_and_missing_source_are_audit_only():
    n = native()
    raw = n.fields["DBZH"].copy()
    arrays, records = object_morphology(n, thresholds=(45,))
    assert len(records) == 1 and records[0]["gate_count"] == 160
    assert records[0]["radial_span_m"] == 19750
    assert records[0]["source_available_gates"] == 0
    assert records[0]["source_match_fraction"] is None
    assert not arrays["45"][~np.isfinite(raw)].any()
    assert np.array_equal(n.fields["DBZH"], raw, equal_nan=True)


def test_gap_splits_object_and_residual_counts_observed_only():
    n = native()
    n.gap_after[4] = True
    residual = np.full(n.shape, np.nan)
    residual[4, 10:50] = 0
    _, records = object_morphology(n, thresholds=(45,), source_residual=residual)
    assert len(records) == 2
    assert sum(r["source_available_gates"] for r in records) == 40
    assert sum(r["gate_count"] for r in records) == 160


def test_thresholds_are_separate_not_repeated_votes():
    n = native()
    arrays, records = object_morphology(n)
    assert set(arrays) == {"20", "35", "45", "55"}
    assert not arrays["55"].any()
    assert len(records) == 3


def test_full_ppi_wraps_but_sector_does_not():
    from dataclasses import replace

    n = native()
    n.fields["DBZH"][:] = np.nan
    n.fields["DBZH"][[0, -1], 10:20] = 50
    n.field_available["DBZH"][:] = np.isfinite(n.fields["DBZH"])
    n = replace(n, azimuth=np.arange(12) * 30.0, full_ppi=True, gap_after=np.zeros(12, bool))
    # Py-ART delta must represent actual geometry; 30° spacing is not within 2°.
    n = replace(n, azimuth=np.r_[0.0, np.arange(1, 11) * 30.0, 359.0])
    _, r = object_morphology(n, thresholds=(45,))
    assert len(r) == 1 and r[0]["angular_width_deg"] == 1
    n = replace(n, full_ppi=False)
    _, r = object_morphology(n, thresholds=(45,))
    assert len(r) == 2
