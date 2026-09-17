"""Held-out source boundary geometry; no QC disposition."""

import numpy as np


def fit_boundary(xy, ranges):
    from skimage.measure import LineModelND, ransac

    xy, ranges = np.asarray(xy, float), np.asarray(ranges, float)
    if xy.ndim != 2 or xy.shape != (len(ranges), 2):
        raise ValueError("boundary geometry mismatch")
    valid = np.isfinite(xy).all(axis=1) & np.isfinite(ranges)
    xy, ranges = xy[valid], ranges[valid]
    train = (ranges // 50000).astype(int) % 2 == 0
    # Cap point count deterministically, in range order, independently per split.
    groups = []
    for mask in (train, ~train):
        ix = np.flatnonzero(mask)
        ix = ix[np.argsort(ranges[ix], kind="stable")]
        if len(ix) > 2000:
            ix = ix[np.linspace(0, len(ix) - 1, 2000).astype(int)]
        groups.append(ix)
    a, b = groups
    if min(len(a), len(b)) < 20 or min(np.ptp(ranges[a]), np.ptp(ranges[b])) < 100000:
        return {"status": "insufficient_support", "train_points": len(a), "holdout_points": len(b)}
    model, inliers = ransac(
        xy[a], LineModelND, min_samples=2, residual_threshold=1500, max_trials=200, rng=42
    )
    if model is None:
        return {"status": "fit_failed"}
    hold = model.residuals(xy[b])
    origin = float(model.residuals(np.zeros((1, 2)))[0])
    fraction = float(np.mean(hold <= 1500))
    good_train = a[inliers]
    result = {
        "status": "radial_geometry",
        "train_points": len(a),
        "holdout_points": len(b),
        "line_origin_xy_m": model.origin.tolist(),
        "line_direction_xy": model.direction.tolist(),
        "origin_distance_m": origin,
        "holdout_p90_m": float(np.percentile(hold, 90)),
        "holdout_inlier_fraction": fraction,
        "train_inlier_fraction": float(np.mean(inliers)),
    }
    if len(good_train) < 20 or np.ptp(ranges[good_train]) < 100000 or np.mean(inliers) < 0.8:
        result["status"] = "inconsistent_boundary"
    elif fraction < 0.8 or np.percentile(hold, 90) > 1500:
        result["status"] = "holdout_mismatch"
    elif origin > 3000:
        result["status"] = "off_origin"
    return result


def source_boundary(native, object_labels, object_id, *, site_altitude_m=None):
    import wradlib

    if object_labels.shape != native.shape:
        raise ValueError("object geometry mismatch")
    valid = (
        native.field_available["DBZH"]
        & np.isfinite(native.fields["DBZH"])
        & native.geometry_good[:, None]
    )
    inside = object_labels == object_id
    nxt = np.roll(inside, -1, axis=0)
    safe = valid & np.roll(valid, -1, axis=0) & ~native.gap_after[:, None]
    if not native.full_ppi:
        safe[-1] = False
    altitude = (
        native.attrs.get("antenna_altitude_m") if site_altitude_m is None else site_altitude_m
    )
    if altitude is None or not np.isfinite(altitude):
        return {"status": "missing_site_altitude", "audit_only": True}
    # Use the external azimuth envelope at each range, not internal holes.
    angles = np.unique(native.azimuth[np.any(inside, axis=1)] % 360)
    if not len(angles):
        return {"status": "empty_object", "audit_only": True}
    gap = int(np.argmax(np.diff(np.r_[angles, angles[0] + 360])))
    start = angles[(gap + 1) % len(angles)]
    unwrapped = (native.azimuth - start) % 360
    lower = np.min(np.where(inside, unwrapped[:, None], np.inf), axis=0)
    upper = np.max(np.where(inside, unwrapped[:, None], -np.inf), axis=0)
    outer_leave = np.isclose(unwrapped[:, None], upper[None, :])
    outer_enter = np.isclose(np.roll(unwrapped, -1)[:, None], lower[None, :])
    sides = {}
    for name, edges in [
        ("leaving", inside & ~nxt & outer_leave),
        ("entering", ~inside & nxt & outer_enter),
    ]:
        yy, xx = np.nonzero(edges & safe)
        jj = (yy + 1) % native.shape[0]
        theta = (native.elevation[yy] + native.elevation[jj]) / 2
        az = native.azimuth[yy] + ((native.azimuth[jj] - native.azimuth[yy] + 180) % 360 - 180) / 2
        distance = wradlib.georef.bin_distance(native.ranges[xx], theta, float(altitude))
        xy = np.column_stack((distance * np.sin(np.deg2rad(az)), distance * np.cos(np.deg2rad(az))))
        sides[name] = fit_boundary(xy, native.ranges[xx])
        sides[name]["measured_edge_points"] = len(xx)
    return {
        "status": "paired_radial_geometry"
        if all(x["status"] == "radial_geometry" for x in sides.values())
        else "unconfirmed_geometry",
        "audit_only": True,
        "sides": sides,
        "coordinate_model": "wradlib.bin_distance_4_3_earth",
    }
