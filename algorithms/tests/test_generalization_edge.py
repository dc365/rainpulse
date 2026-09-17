import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.generalization import bounded_edge_reference


def extend(q=None, seed=None, ids=None, ranges=None):
    q = np.ones((1, 21), bool) if q is None else q
    seed = np.zeros_like(q) if seed is None else seed
    if not seed.any():
        seed[:, 8:12] = True
    return bounded_edge_reference(
        q,
        seed,
        np.ones(q.shape, int) if ids is None else ids,
        np.arange(21) * 10000.0 if ranges is None else ranges,
        maximum_extension_m=50000,
        minimum_anchor_span_m=25000,
    )


def test_physical_bound_and_no_recursive_growth():
    got = extend()
    assert np.flatnonzero(got).tolist() == [3, 4, 5, 6, 7, 12, 13, 14, 15, 16]


@pytest.mark.parametrize("barrier", ["missing_or_weather_or_conflict", "object"])
def test_evidence_and_object_boundaries_stop_extension(barrier):
    q = np.ones((1, 21), bool)
    ids = np.ones(q.shape, int)
    if barrier == "object":
        ids[:, 14:] = 2
    else:
        q[:, 14] = False
    got = extend(q=q, ids=ids)
    assert not got[:, 14:].any()
    assert got[0, 13]


def test_scattered_anchors_are_insufficient():
    seed = np.zeros((1, 21), bool)
    seed[:, [8, 11]] = True
    assert not extend(seed=seed).any()


def test_no_anchor_no_extension():
    q = np.ones((1, 21), bool)
    assert not bounded_edge_reference(
        q,
        np.zeros_like(q),
        np.ones(q.shape, int),
        np.arange(21) * 10000.0,
        maximum_extension_m=50000,
        minimum_anchor_span_m=25000,
    ).any()


def test_invalid_range_geometry_rejected():
    with pytest.raises(ValueError):
        extend(ranges=np.zeros(21))


def test_stage_edge_is_quarantine_only_and_weather_is_barrier():
    from rainpulse_algo.radar.qc import load_qc_profile
    from rainpulse_algo.radar.qc_engine.generalization import broad_source_review

    from .test_generalization_p0p2 import FLAGS, P, broad_scene, keep, oc_fixture

    p = load_qc_profile(P.with_name("fujian-qc-generalization-edge.yaml"), FLAGS)
    n = broad_scene()
    e, o = oc_fixture(n)
    e.arrays["bracketed_reference_mask"][:] = 0
    e.arrays["bracketed_reference_mask"][:, 180:220] = 1
    weather = np.zeros(n.shape)
    weather[:, 240:] = 1
    d, s = broad_source_review(n, keep(n), p, e, o, weather_support=weather)
    edge = d.arrays["P2_BOUNDED_EDGE_REFERENCE_MASK"] == 1
    assert edge[:, 220:240].any()
    assert not edge[:, 240:].any()
    assert not d.arrays["QPE_ELIGIBLE_MASK"][edge].any()
    assert not np.isfinite(d.arrays["DBZH_USABLE"][edge]).any()
    assert s["new_confirmed_gates"] == 0
    assert not np.any(d.arrays["QC_ACTION"][edge] == 3)
