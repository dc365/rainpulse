"""Run with RAINPULSE_EMITTER_CORE pointing at the source-verified native core."""

import os
from types import SimpleNamespace
import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.measurement_v8.sparse_emitter import (
    run_sparse_emitter,
    file_hash,
)


@pytest.fixture
def core():
    path = os.environ.get("RAINPULSE_EMITTER_CORE")
    if not path:
        pytest.skip("original native Emitter core required")
    return path, file_hash(path)


def scan(z):
    return SimpleNamespace(
        fields={"DBZH": z},
        field_available={"DBZH": np.isfinite(z)},
        gate_spacing_m=1000.0,
        full_ppi=False,
        geometry_good=np.ones(z.shape[0], bool),
        gap_after=np.zeros(z.shape[0], bool),
    )


def test_observed_line_survives_unrelated_missing_but_not_missing_shoulders(core):
    z = np.zeros((7, 40), dtype="float32")
    z[3] = 30
    z[0] = np.nan
    n = scan(z)
    before = z.copy()
    assert (run_sparse_emitter(n, *core).scores["1"][3] > 0).all()
    np.testing.assert_equal(z, before)
    z[2, 10:15] = np.nan
    score = run_sparse_emitter(n, *core).scores["1"]
    assert np.isnan(score[3, 10:15]).all()
    assert (score[3, :10] > 0).all()


def test_missing_does_not_create_contrast_and_geometry_gap_is_unknown(core):
    z = np.full((7, 40), 20.0, dtype="float32")
    z[2, 10:15] = np.nan
    n = scan(z)
    assert not (run_sparse_emitter(n, *core).scores["1"] > 0).any()
    n.gap_after[2] = True
    assert np.isnan(run_sparse_emitter(n, *core).scores["1"][3]).all()


def test_budget_leaves_uncomputed_unknown(core):
    result = run_sparse_emitter(scan(np.zeros((7, 40))), *core, maximum_calls=1)
    assert result.summary["budget_exhausted"]
    assert np.isfinite(result.scores["1"]).sum() == 40
    assert np.isnan(result.scores["2"]).all()
