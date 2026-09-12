"""Native-frame spatial verification using bounded, checksum-validated sources."""

import hashlib
import json
import time
from collections import OrderedDict

import numpy as np
from scipy.ndimage import uniform_filter


def spatial_metrics(observed, predicted, bounds):
    observed, predicted = np.asarray(observed), np.asarray(predicted)
    if observed.ndim != 2 or observed.shape != predicted.shape:
        raise ValueError("verification grid shapes differ")
    valid = np.isfinite(observed) & np.isfinite(predicted) & (observed >= 0) & (predicted >= 0)
    count = int(valid.sum())
    difference = predicted[valid].astype(float) - observed[valid]
    height, width = observed.shape
    west, south, east, north = bounds
    dx = (east - west) / width * 111.32 * np.cos(np.deg2rad((south + north) / 2))
    dy = (north - south) / height * 111.32
    if dx <= 0 or dy <= 0:
        raise ValueError("invalid geographic grid")
    rows = []
    for threshold in [1, 5, 10, 20, 50]:
        obs, pred = (observed > threshold) & valid, (predicted > threshold) & valid
        hits = int((obs & pred).sum())
        misses = int((obs & ~pred & valid).sum())
        false_alarms = int((~obs & pred & valid).sum())
        denominator = hits + misses + false_alarms
        csi = hits / denominator if denominator else None
        for km in [1, 5, 10, 20, 40]:
            # Nearest odd window, with actual dimensions returned to callers.
            nx, ny = [max(1, 2 * int(round((km / pitch - 1) / 2)) + 1) for pitch in (dx, dy)]
            size = (ny, nx)
            support = (
                uniform_filter(valid.astype(float), size=size, mode="constant", cval=0) > 1 - 1e-10
            )
            of = uniform_filter(obs.astype(float), size=size, mode="constant", cval=0)[support]
            pf = uniform_filter(pred.astype(float), size=size, mode="constant", cval=0)[support]
            denom = float(np.sum(of**2 + pf**2))
            # Suppress floating-point filter residue before event-any thresholding.
            oe, pe = of > 0.5 / (nx * ny), pf > 0.5 / (nx * ny)
            union = int((oe | pe).sum())
            rows.append(
                dict(
                    threshold=threshold,
                    window_km=km,
                    window_shape=[ny, nx],
                    actual_km=[nx * dx, ny * dy],
                    neighborhood_cells=int(support.sum()),
                    hits=hits,
                    misses=misses,
                    false_alarms=false_alarms,
                    csi=csi,
                    fss=max(0.0, min(1.0, 1 - float(np.sum((of - pf) ** 2)) / denom))
                    if denom > 1e-20
                    else None,
                    neighborhood_csi=int((oe & pe).sum()) / union if union else None,
                )
            )
    return dict(
        valid_cells=count,
        total_cells=valid.size,
        coverage=count / valid.size,
        mae=float(np.mean(np.abs(difference))) if count else None,
        rmse=float(np.sqrt(np.mean(difference**2))) if count else None,
        rows=rows,
    )


def point_field(service, source, bounds, algorithm):
    values, valid, grid = service._point(source)
    indices = source["indices"]
    if len(indices) != 1:
        raise ValueError("expected one native frame")
    west, south, east, north = bounds
    dx, dy = (east - west) / grid["width"], (north - south) / grid["height"]
    x, y = west + dx / 2, south + dy / 2
    nominal = [x, y, dx, dy]
    actual = [grid["west"], grid["south"], grid["longitude_interval"], grid["latitude_interval"]]
    legacy = [
        x,
        y,
        float(np.float32(x + dx)) - float(np.float32(x)),
        float(np.float32(y + dy)) - float(np.float32(y)),
    ]
    if not np.allclose(actual, nominal, rtol=0, atol=1e-8) and not (
        algorithm == "qpe" and np.allclose(actual, legacy, rtol=0, atol=1e-10)
    ):
        raise ValueError("verification source grid differs")
    return np.where(valid[indices[0]], values[indices[0]], np.nan)


def forecast_field(service, request):
    from rainpulse_algo.products.ensemble_builder import _open_group
    from rainpulse_algo.worker.object_store import (
        ArtifactObjectReader,
        artifact_sha256,
        minio_client_from_environment,
    )

    bounds = request["bounds"]
    source = request["forecast"][0]
    if request["algorithm"] != "steps":
        return point_field(service, source, bounds, request["algorithm"])
    if service.client is None:
        service.client = minio_client_from_environment()
    objects = ArtifactObjectReader(service.client, max_size_bytes=512 * 1024**2).load(source["uri"])
    if artifact_sha256(objects) != source["sha256"]:
        raise ValueError("ensemble checksum differs")
    group = _open_group(objects)
    shape = group["rain_rate"].shape
    if len(shape) != 4 or shape[0] > 32 or np.prod(shape) > 64_000_000:
        raise ValueError("ensemble shape exceeds limit")
    lat, lon = group["lat"][:], group["lon"][:]
    dx, dy = (bounds[2] - bounds[0]) / len(lon), (bounds[3] - bounds[1]) / len(lat)
    if not np.allclose(
        lon, bounds[0] + (np.arange(len(lon)) + 0.5) * dx, rtol=0, atol=1e-5
    ) or not np.allclose(lat, bounds[1] + (np.arange(len(lat)) + 0.5) * dy, rtol=0, atol=1e-5):
        raise ValueError("ensemble grid differs")
    index = list(group["lead_time"][:]).index(request["lead_minutes"])
    rates = group["rain_rate"][:, index]
    valid = (group["member_valid_mask"][:, index] == 1) & np.isfinite(rates) & (rates >= 0)
    return np.where(valid.all(axis=0), np.median(rates, axis=0), np.nan)


def calculate(service, request):
    key = hashlib.sha256(json.dumps(["spatial-v1", request], sort_keys=True).encode()).hexdigest()
    with service.compute_lock:
        cache = getattr(service, "verification_cache", None)
        if cache is None:
            service.verification_cache = cache = OrderedDict()
        now = time.monotonic()
        if key in cache and cache[key][0] > now:
            return cache[key][1]
        bounds = request["bounds"]
        obs = point_field(service, request["truth"][0], bounds, "qpe")
        pred = forecast_field(service, request)
        metrics = spatial_metrics(obs, pred, bounds)
        cache[key] = (now + 600, metrics)
        while len(cache) > 64:
            cache.popitem(last=False)
        return metrics
