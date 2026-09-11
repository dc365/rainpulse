"""Ephemeral interval products. No model execution or persistent product writes."""

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
from minio.error import S3Error

from rainpulse_algo.diagnostics.png import encode_rgba_png
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
    parse_s3_uri,
)

from .builder import rainfall_rgba
from .point_index import HEADER, validate_point_query_index
from .profile import PaletteStop

PALETTE = tuple(
    PaletteStop(v, c)
    for v, c in zip(
        (0.1, 0.5, 1, 2.5, 5, 10, 25, 50, 100),
        (
            "#9dd9ff",
            "#4ba3f2",
            "#2a79c7",
            "#3ca85b",
            "#9acb3c",
            "#efd23a",
            "#ee8a2d",
            "#cf453b",
            "#862f82",
        ),
        strict=True,
    )
)


def integrate(rates, valid, leads, start, end, *, quantile=None):
    """Right-endpoint mm/h integration, member first, complete support only."""
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= 120
        or start % 5
        or end % 5
    ):
        raise ValueError("interval must be within 0–120 minutes and snap to five minutes")
    wanted = list(range(start + 5, end + 1, 5))
    if len(set(leads)) != len(leads) or any(lead not in leads for lead in wanted):
        raise ValueError("missing or duplicate source time")
    indices = [leads.index(lead) for lead in wanted]
    rates, valid = np.asarray(rates), np.asarray(valid, dtype=bool)
    if rates.ndim != 4 or rates.shape != valid.shape or rates.shape[1] != len(leads):
        raise ValueError("source dimensions differ")
    values = rates[:, indices].astype(np.float64)
    support = valid[:, indices] & np.isfinite(values) & (values >= 0)
    member_valid = np.all(support, axis=1)
    amounts = np.sum(np.where(support, values, 0), axis=1) / 12
    complete = np.all(member_valid, axis=0)
    result = (
        np.mean(amounts, axis=0) if quantile is None else np.quantile(amounts, quantile, axis=0)
    )
    return np.where(complete, result, np.nan).astype(np.float32), complete


def decode_point(data):
    grid = validate_point_query_index(data)
    records = np.frombuffer(data, dtype=[("value", ">f4"), ("quality", "u1")], offset=HEADER.size)
    records = records.reshape(grid["height"], grid["width"], grid["lead_count"]).transpose(2, 0, 1)
    return records["value"].astype(np.float32), records["quality"] != 255, grid


class IntervalService:
    """One bounded compute lane; health/NATS stay on the asyncio event loop."""

    def __init__(self, client=None):
        self.client = client
        self.lock = threading.Lock()
        self.compute_lock = threading.Lock()
        self.cache = OrderedDict()
        self.sources = OrderedDict()

    def _read(self, uri, maximum=32 * 1024**2):
        bucket, key = parse_s3_uri(uri)
        if self.client is None:
            self.client = minio_client_from_environment()
        response = self.client.get_object(bucket, key)
        try:
            data = response.read(maximum + 1)
        finally:
            response.close()
            response.release_conn()
        if len(data) > maximum:
            raise ValueError("source exceeds byte limit")
        return data

    def _point(self, source):
        identity = json.dumps(source, sort_keys=True)
        if identity in self.sources:
            data = self.sources.pop(identity)
            self.sources[identity] = data
            return decode_point(data)
        if source.get("local"):
            # Only Go-resolved UUID directories under this read-only mount.
            root = Path("/var/lib/rainpulse/nowcastnet-products").resolve()
            path = (root / source["local"] / source["object_path"]).resolve()
            if not path.is_relative_to(root) or path.stat().st_size > 32 * 1024**2:
                raise ValueError("invalid local source")
            data = path.read_bytes()
        elif source.get("object_path"):
            base = source["uri"].rstrip("/")
            relative = source["object_path"]
            if relative.startswith("/") or ".." in relative.split("/"):
                raise ValueError("invalid object path")
            try:
                data = self._read(base + "/" + relative)
            except S3Error as error:
                if error.code not in {"NoSuchKey", "NoSuchObject"}:
                    raise
                marker = json.loads(self._read(base + "/_SUCCESS.json"))
                entry = next(item for item in marker["objects"] if item["key"] == relative)
                if entry["sha256"] != source["sha256"]:
                    raise ValueError("source marker differs")
                prefix = marker.get("data_prefix", "")
                data = self._read("/".join(filter(None, [base, prefix, relative])))
        else:
            data = self._read(source["uri"])
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise ValueError("source checksum differs")
        self.sources[identity] = data
        while sum(map(len, self.sources.values())) > 64 * 1024**2:
            self.sources.popitem(last=False)
        return decode_point(data)

    def calculate(self, request):
        start, end = request["start"], request["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= 120
            or start % 5
            or end % 5
        ):
            raise ValueError("invalid interval")
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with self.compute_lock:
            now = time.monotonic()
            for old in list(self.cache):
                if self.cache[old][0] < now:
                    del self.cache[old]
            if key in self.cache:
                return self.cache[key][1]
            if request["algorithm"] == "steps":
                from .ensemble_builder import _open_group

                if self.client is None:
                    self.client = minio_client_from_environment()
                source = request["sources"][0]
                objects = ArtifactObjectReader(self.client, max_size_bytes=512 * 1024**2).load(
                    source["uri"]
                )
                from rainpulse_algo.worker.object_store import artifact_sha256

                if artifact_sha256(objects) != source["sha256"]:
                    raise ValueError("ensemble source differs")
                group = _open_group(objects)
                shape = group["rain_rate"].shape
                if len(shape) != 4 or shape[0] > 32 or np.prod(shape) > 64_000_000:
                    raise ValueError("ensemble decoded shape exceeds interval limit")
                lat, lon = group["lat"][:], group["lon"][:]
                if len(lat) < 2 or len(lon) < 2:
                    raise ValueError("invalid ensemble grid")
                dx = float(lon[-1] - lon[0]) / (len(lon) - 1)
                dy = float(lat[-1] - lat[0]) / (len(lat) - 1)
                expected_bounds = [
                    lon[0] - dx / 2,
                    lat[0] - dy / 2,
                    lon[-1] + dx / 2,
                    lat[-1] + dy / 2,
                ]
                leads = [int(v) for v in group["lead_time"][:]]
                rates = group["rain_rate"][:]
                valid = group["member_valid_mask"][:] == 1
                amount, support = integrate(rates, valid, leads, start, end, quantile=0.5)
            else:
                rates, masks, leads, expected_grid = [], [], [], None
                for source in request["sources"]:
                    values, valid, grid = self._point(source)
                    if expected_grid is not None and any(
                        grid[k] != expected_grid[k]
                        for k in (
                            "width",
                            "height",
                            "west",
                            "south",
                            "longitude_interval",
                            "latitude_interval",
                        )
                    ):
                        raise ValueError("source grids differ")
                    expected_grid = grid
                    for lead, index in zip(source["leads"], source["indices"], strict=True):
                        rates.append(values[index])
                        masks.append(valid[index])
                        leads.append(lead)
                amount, support = integrate(
                    np.array(rates)[None], np.array(masks)[None], leads, start, end
                )
                dx, dy = grid["longitude_interval"], grid["latitude_interval"]
                expected_bounds = [
                    grid["west"] - dx / 2,
                    grid["south"] - dy / 2,
                    grid["west"] + (grid["width"] - 0.5) * dx,
                    grid["south"] + (grid["height"] - 0.5) * dy,
                ]
            bounds = request["bounds"]
            if not np.allclose(bounds, expected_bounds, rtol=0, atol=1e-5):
                # Historical QPE headers used the difference of the first two
                # float32 coordinates, not the nominal spacing. Accept exactly
                # that encoding, never a generally relaxed grid tolerance.
                target_dx = (
                    (bounds[2] - bounds[0]) / grid["width"] if request["algorithm"] == "qpe" else 0
                )
                target_dy = (
                    (bounds[3] - bounds[1]) / grid["height"] if request["algorithm"] == "qpe" else 0
                )
                west, south = bounds[0] + target_dx / 2, bounds[1] + target_dy / 2
                legacy_dx = float(np.float32(west + target_dx)) - float(np.float32(west))
                legacy_dy = float(np.float32(south + target_dy)) - float(np.float32(south))
                if request["algorithm"] != "qpe" or not np.allclose(
                    [grid["west"], grid["south"], dx, dy],
                    [west, south, legacy_dx, legacy_dy],
                    rtol=0,
                    atol=1e-10,
                ):
                    raise ValueError("source grid differs from display grid")
            png = encode_rgba_png(
                rainfall_rgba(amount, support, PALETTE, transparent_below=0.1, opacity=255)
            )
            count = int(support.sum())
            valid_time = (
                datetime.fromisoformat(request["issue_time"].replace("Z", "+00:00"))
                + timedelta(minutes=end)
            ).isoformat()
            frame = dict(
                asset_id=key,
                image_url=f"/api/v1/workspace/accumulations/{key}/image",
                media_type="image/png",
                unit="mm",
                valid_time=valid_time,
                lead_time_minutes=end,
                bounds=bounds,
                frame_kind="derived",
                derivation=f"interval-{start}-{end}-right-endpoint-v1",
                source_leads=list(range(start + 5, end + 1, 5)),
                sha256=hashlib.sha256(png).hexdigest(),
                valid_cell_count=count,
                missing_cell_count=int(support.size - count),
                coverage_ratio=count / support.size,
            )
            self.cache[key] = (now + 600, frame, png, amount, bounds)
            while (
                len(self.cache) > 32
                or sum(len(v[2]) + v[3].nbytes for v in self.cache.values()) > 64 * 1024**2
            ):
                self.cache.popitem(last=False)
            return frame

    def dispatch(self, method, target, body):
        try:
            if method == "POST" and target in {"/interval/compare", "/interval/summary"}:
                from rainpulse_algo.verification.analysis import compare, summarize

                request = json.loads(body)
                result = compare(self, request) if target.endswith("compare") else summarize(request["records"], request["algorithms"])
                return 200, "application/json", json.dumps(result, allow_nan=False).encode()
            if method == "POST" and target == "/interval/verification":
                from rainpulse_algo.verification.workspace import calculate

                return 200, "application/json", json.dumps(calculate(self, json.loads(body)), allow_nan=False).encode()
            if method == "POST" and target == "/interval":
                return (
                    200,
                    "application/json",
                    json.dumps(self.calculate(json.loads(body))).encode(),
                )
            parsed = urlsplit(target)
            parts = parsed.path.split("/")
            if method != "GET" or len(parts) != 4 or parts[1] != "interval":
                return 404, "application/json", b"{}"
            with self.lock:
                cached = self.cache.get(parts[2])
                if cached is None or cached[0] < time.monotonic():
                    return 410, "application/json", b'{"error":"interval expired; recalculate"}'
                _, frame, png, values, bounds = cached
            if parts[3] == "image":
                return 200, "image/png", png
            if parts[3] != "sample":
                return 404, "application/json", b"{}"
            query = parse_qs(parsed.query)
            lon, lat = float(query["longitude"][0]), float(query["latitude"][0])
            west, south, east, north = bounds
            if not west <= lon < east or not south <= lat < north:
                raise ValueError("point outside grid")
            height, width = values.shape
            dx, dy = (east - west) / width, (north - south) / height
            x, y = int((lon - west) / dx), int((lat - south) / dy)
            value = float(values[y, x])
            result = dict(
                schema_version="1.0",
                asset_url=frame["image_url"],
                longitude=lon,
                latitude=lat,
                grid_longitude=west + (x + 0.5) * dx,
                grid_latitude=south + (y + 0.5) * dy,
                valid=math.isfinite(value),
                unit="mm",
                lead_time_minutes=frame["lead_time_minutes"],
                valid_time=frame["valid_time"],
                frame_kind="derived",
                derivation=frame["derivation"],
                source="on-demand-interval",
            )
            if result["valid"]:
                result["value"] = value
            return 200, "application/json", json.dumps(result).encode()
        except (ValueError, KeyError, IndexError, StopIteration) as error:
            return 422, "application/json", json.dumps({"error": str(error)}).encode()
        except Exception:
            # No credentials/URIs in browser-visible errors.
            import logging

            logging.exception("interval computation failed")
            return (
                503,
                "application/json",
                b'{"error":"source unavailable; interval was not calculated"}',
            )
