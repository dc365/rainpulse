"""Shared-support comparisons, defensible PSD crops and matched macro summaries."""

import hashlib
import json
import time
from collections import OrderedDict

import numpy as np

from .workspace import forecast_field, point_field, spatial_metrics


def common_metrics(obs, forecasts, bounds):
    fields = [obs, *forecasts.values()]
    if any(f.shape != obs.shape for f in fields):
        raise ValueError("comparison grid shapes differ")
    common = np.logical_and.reduce([np.isfinite(f) & (f >= 0) for f in fields])
    truth = np.where(common, obs, np.nan)
    return {
        name: spatial_metrics(truth, np.where(common, field, np.nan), bounds)
        for name, field in forecasts.items()
    }


def spectrum(fields, bounds):
    arrays = list(fields.values())
    if not arrays or any(a.ndim != 2 or a.shape != arrays[0].shape for a in arrays):
        raise ValueError("spectral shapes differ")
    height, width = arrays[0].shape
    valid = np.logical_and.reduce([np.isfinite(a) & (a >= 0) for a in arrays])
    # Integral-image sum tests exact complete squares, no tolerance or dry fill.
    integral = np.pad(valid.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    crop = None
    for size in [256, 128, 64, 32]:
        if size > min(height, width):
            continue
        counts = (
            integral[size:, size:]
            - integral[:-size, size:]
            - integral[size:, :-size]
            + integral[:-size, :-size]
        )
        candidates = np.argwhere(counts == size * size)
        if len(candidates):
            y, x = map(int, candidates[0])
            crop = y, x, size
            break
    if crop is None:
        return dict(
            status="unavailable", reason="共同有效域内没有至少 32×32 格的完整区域，无法可靠计算 PSD"
        )
    y, x, n = crop
    west, south, east, north = bounds
    dlon, dlat = (east - west) / width, (north - south) / height
    lat = south + (y + n / 2) * dlat
    dx, dy = dlon * 111.32 * np.cos(np.deg2rad(lat)), dlat * 111.32
    fx, fy = np.fft.fftfreq(n, dx), np.fft.fftfreq(n, dy)
    radial = np.hypot(fy[:, None], fx[None, :])
    lower, upper = max(1 / (n * dx), 1 / (n * dy)), min(0.5 / dx, 0.5 / dy)
    edges = np.geomspace(lower, upper, 17)
    bins = [
        (radial >= a) & (radial < b if i < 15 else radial <= b)
        for i, (a, b) in enumerate(zip(edges[:-1], edges[1:]))
    ]
    bins = [b for b in bins if b.any()]
    scales = [1 / float(radial[b].mean()) for b in bins]
    window = np.outer(np.hanning(n), np.hanning(n))
    series = {}
    for name, field in fields.items():
        data = field[y : y + n, x : x + n].astype(float)
        power = (
            np.abs(np.fft.fft2((data - data.mean()) * window)) ** 2 * dx * dy / np.sum(window**2)
        )
        series[name] = [float(power[b].mean()) for b in bins]
    return dict(
        status="ready",
        scale_km=scales,
        series=series,
        shape=[n, n],
        bounds=[west + x * dlon, south + y * dlat, west + (x + n) * dlon, south + (y + n) * dlat],
        coverage=n * n / (height * width),
        unit="(mm/h)^2 km^2",
        method="demean-hann-2d-radial-density-v1",
    )


def compare(service, request):
    key = hashlib.sha256(
        json.dumps(["comparison-v1", request], sort_keys=True).encode()
    ).hexdigest()
    with service.compute_lock:
        if not hasattr(service, "comparison_cache"):
            service.comparison_cache = OrderedDict()
        cache = service.comparison_cache
        now = time.monotonic()
        if key in cache and cache[key][0] > now:
            return cache[key][1]
        bounds = request["bounds"]
        obs = point_field(service, request["truth"][0], bounds, "qpe")
        forecasts = {
            name: forecast_field(
                service,
                dict(
                    algorithm=name,
                    forecast=sources,
                    bounds=bounds,
                    lead_minutes=request["lead_minutes"],
                ),
            )
            for name, sources in request["forecasts"].items()
        }
        result = dict(metrics=common_metrics(obs, forecasts, bounds), source_fingerprint=key)
        if request.get("psd"):
            result["psd"] = spectrum({"qpe": obs, **forecasts}, bounds)
        cache[key] = (now + 600, result)
        while len(cache) > 32:
            cache.popitem(last=False)
        return result


def summarize(records, algorithms):
    names = ["csi", "fss", "neighborhood_csi", "mae", "rmse", "coverage"]
    ready = [r for r in records if r.get("status") == "ready"]

    def aggregate(rows):
        result = {a: {} for a in algorithms}
        for name in names:
            paired = [
                r
                for r in rows
                if all(
                    isinstance(r.get("metrics", {}).get(a, {}).get(name), (int, float))
                    and np.isfinite(r["metrics"][a][name])
                    for a in algorithms
                )
            ]
            for a in algorithms:
                values = [r["metrics"][a][name] for r in paired]
                result[a][name] = dict(
                    mean=float(np.mean(values)) if values else None, n=len(values)
                )
        return result

    return dict(
        overall=aggregate(ready),
        by_lead=[
            dict(
                lead_minutes=lead,
                algorithms=aggregate([r for r in ready if r["lead_minutes"] == lead]),
            )
            for lead in sorted({r["lead_minutes"] for r in records})
        ],
        matched_records=len(ready),
        skipped_records=len(records) - len(ready),
        method="matched-macro-mean-v1",
    )
