import numpy as np

from rainpulse_algo.radar.qc_engine.source_boundary import fit_boundary


def points(offset=0):
    r = np.arange(50_000.0, 450_000.0, 1000)
    return np.column_stack((r, np.full(len(r), offset))), r


def test_radial_boundary_and_off_origin_rainband():
    xy, r = points()
    assert fit_boundary(xy, r)["status"] == "radial_geometry"
    xy, r = points(20000)
    assert fit_boundary(xy, r)["status"] == "off_origin"


def test_holdout_corruption_does_not_change_fit():
    xy, r = points()
    a = fit_boundary(xy, r)
    xy[(r // 50000).astype(int) % 2 == 1, 1] += 12000
    b = fit_boundary(xy, r)
    assert a["line_origin_xy_m"] == b["line_origin_xy_m"]
    assert a["line_direction_xy"] == b["line_direction_xy"]
    assert b["status"] == "holdout_mismatch"


def test_insufficient_boundary_and_missing_are_not_geometry():
    xy, r = points()
    assert fit_boundary(xy[:5], r[:5])["status"] == "insufficient_support"
    xy[:] = np.nan
    assert fit_boundary(xy, r)["status"] == "insufficient_support"


def test_measured_sector_edges_and_missing_neighbour_abstention():
    from dataclasses import replace

    from rainpulse_algo.radar.qc_engine.source_boundary import source_boundary

    from .test_object_morphology import native

    n = native()
    r = np.arange(125.0, 450000.0, 250)
    z = np.full((12, len(r)), 10.0)
    n = replace(
        n,
        ranges=r,
        fields={"DBZH": z},
        field_available={"DBZH": np.ones(z.shape, bool)},
        attrs={"antenna_altitude_m": 640.0},
    )
    labels = np.zeros(z.shape, int)
    labels[4:8] = 1
    a = source_boundary(n, labels, 1)
    assert a["status"] == "paired_radial_geometry"
    n.field_available["DBZH"][[3, 8]] = False
    b = source_boundary(n, labels, 1)
    assert b["status"] == "unconfirmed_geometry"
    assert all(x["measured_edge_points"] == 0 for x in b["sides"].values())


def test_internal_holes_do_not_train_outer_boundary():
    from dataclasses import replace

    from rainpulse_algo.radar.qc_engine.source_boundary import source_boundary

    from .test_object_morphology import native

    n = native()
    ranges = np.arange(125.0, 450000.0, 250)
    z = np.full((12, len(ranges)), 10.0)
    n = replace(
        n,
        ranges=ranges,
        fields={"DBZH": z},
        field_available={"DBZH": np.ones(z.shape, bool)},
        attrs={"antenna_altitude_m": 640.0},
    )
    labels = np.zeros(z.shape, int)
    labels[2:10] = 1
    before = source_boundary(n, labels, 1)
    labels[4:6, ::2] = 0
    after = source_boundary(n, labels, 1)
    assert before["status"] == "paired_radial_geometry"
    assert before == after
