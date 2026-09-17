import numpy as np

from rainpulse_algo.radar.qc_engine.object_gate_review import GateReason, review_object_gates


def test_geometry_does_not_override_polar_or_weather_or_missing():
    labels = np.ones((1, 5), int)
    bws = {
        "BWS_REASON": np.array([[7, 19, 15, 7, 7]]),
        "BWS_CANDIDATE_MASK": np.array([[1, 0, 1, 1, 1]]),
        "BWS_RANGE_RESIDUAL_DB": np.array([[0.0, 0.0, 0.0, np.nan, 0.0]]),
    }
    raw = np.array([[50.0, 50.0, 50.0, 50.0, np.nan]])
    reasons, proposal = review_object_gates(labels, {1}, bws, raw)
    assert proposal.tolist() == [[True, False, False, False, False]]
    assert reasons[0, 1] & GateReason.TARGET_CONFLICT
    assert reasons[0, 2] & GateReason.PROTECTED
    assert reasons[0, 3] & GateReason.SOURCE_UNAVAILABLE
    assert reasons[0, 4] & GateReason.MISSING
    _, proposal = review_object_gates(labels, set(), bws, raw)
    assert not proposal.any()
