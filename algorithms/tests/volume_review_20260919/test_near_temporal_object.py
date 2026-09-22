import copy

import numpy as np
import pytest
from volume_review.data import array_digest
from volume_review.near_temporal_object import evaluate_near_temporal_object


def snapshot(rho_object=0.7, protected=False):
    rows, gates = 36, 120
    az = np.arange(rows, dtype=float) * 10
    r = 500.0 + np.arange(gates) * 500.0
    shape = (rows, gates)
    a = {
        "azimuth": az.copy(),
        "range": r.copy(),
        "DBZH_RAW": np.full(shape, 12.0, "float32"),
        "RHOHV_RAW": np.full(shape, 0.99, "float32"),
        "SNR_RAW": np.full(shape, 15.0, "float32"),
        "VALID_MASK": np.ones(shape, "uint8"),
        "REFLECTIVITY_ELIGIBLE_FOR_CR": np.ones(shape, "uint8"),
        "CF_HARD_WEATHER_MASK": np.zeros(shape, "uint8"),
        "CF_LOCAL_WEATHER_MASK": np.zeros(shape, "uint8"),
        "CF_LEGACY_PROTECTED_MASK": np.zeros(shape, "uint8"),
        "CF_WEATHER_PROXY_MASK": np.zeros(shape, "uint8"),
        "CF_MIXED_MASK": np.zeros(shape, "uint8"),
        "CF_BG_ENHANCEMENT_MASK": np.zeros(shape, "uint8"),
    }
    a["RHOHV_RAW"][8:14, 10:20] = rho_object
    if protected:
        a["CF_HARD_WEATHER_MASK"][8:14, 10:20] = 1
    return a


def test_two_snapshot_recurrence_creates_audit_object():
    current = snapshot()
    past = (copy.deepcopy(current), copy.deepcopy(current))
    evidence = evaluate_near_temporal_object(current, past, sweep="sweep_000")
    selected = evidence.arrays["NTO_OBJECT_MASK"] == 1
    assert selected.any()
    assert evidence.summary["action_gates"] == 0
    assert evidence.summary["object_count"] == 1
    assert np.array_equal(selected, evidence.arrays["NTO_SUPPORT_MASK"] == 1)


def test_weather_and_high_rho_are_excluded():
    protected = snapshot(protected=True)
    protected_past = (copy.deepcopy(protected), copy.deepcopy(protected))
    evidence = evaluate_near_temporal_object(
        protected, protected_past, sweep="sweep_000"
    )
    assert not (evidence.arrays["NTO_OBJECT_MASK"] == 1).any()
    high = snapshot(rho_object=0.99)
    high_past = (copy.deepcopy(high), copy.deepcopy(high))
    evidence = evaluate_near_temporal_object(high, high_past, sweep="sweep_000")
    assert not (evidence.arrays["NTO_CANDIDATE_MASK"] == 1).any()


def test_missing_prior_echo_recurrence_does_not_pass():
    current = snapshot()
    past1 = snapshot()
    past2 = snapshot()
    past1["DBZH_RAW"][:] = -20.0
    evidence = evaluate_near_temporal_object(current, (past1, past2), sweep="sweep_000")
    assert not (evidence.arrays["NTO_OBJECT_MASK"] == 1).any()
    prior1 = (evidence.arrays["NTO_PRIOR1_MASK"] == 1).sum()
    prior2 = (evidence.arrays["NTO_PRIOR2_MASK"] == 1).sum()
    assert prior1 < prior2


def test_input_is_not_mutated_and_snapshot_count_is_checked():
    current = snapshot()
    before = array_digest(current)
    past = (copy.deepcopy(current), copy.deepcopy(current))
    evaluate_near_temporal_object(current, past, sweep="sweep_000")
    assert array_digest(current) == before
    with pytest.raises(ValueError, match="exactly two"):
        evaluate_near_temporal_object(current, (copy.deepcopy(current),), sweep="sweep_000")
