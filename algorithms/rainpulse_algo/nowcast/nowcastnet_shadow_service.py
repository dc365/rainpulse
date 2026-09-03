from __future__ import annotations

import json
import os
import signal
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from .nowcastnet_shadow import (
    NowcastNetShadowProfile,
    cadence_aligned,
    load_nowcastnet_shadow_profile,
    prepare_shadow_input,
    required_frame_times,
)


class ShadowProbeError(RuntimeError):
    """Raised when the realtime shadow probe cannot produce trustworthy status."""


@dataclass(frozen=True)
class AnalysisReference:
    analysis_id: str
    analysis_time: datetime
    grid_id: str
    analysis_uri: str


@dataclass(frozen=True)
class ShadowProbeStatus:
    status: str
    reason: str | None
    checked_at: str
    profile_version: str
    grid_id: str
    grid_config_version: str
    issue_time: str | None
    required_frame_times: tuple[str, ...]
    frame_count: int
    common_valid_ratio: float
    roi: dict[str, int]
    issue_cadence_minutes: int
    input_timestep_minutes: int
    inference_enabled: bool
    spatial_shape_validated: bool
    product_publication_enabled: bool = False
    operational_eligible: bool = False


FrameLoader = Callable[[AnalysisReference], tuple[np.ndarray, np.ndarray]]


class AnalysisFrameCache:
    """Bounded immutable frame cache shared across consecutive probe cycles."""

    def __init__(self, maximum_entries: int = 32) -> None:
        if maximum_entries < 1:
            raise ValueError("analysis frame cache size must be positive")
        self._maximum_entries = maximum_entries
        self._values: OrderedDict[
            tuple[str, str], tuple[np.ndarray, np.ndarray]
        ] = OrderedDict()

    def load(
        self,
        reference: AnalysisReference,
        loader: FrameLoader,
    ) -> tuple[np.ndarray, np.ndarray]:
        key = (reference.analysis_id, reference.analysis_uri)
        existing = self._values.get(key)
        if existing is not None:
            self._values.move_to_end(key)
            return existing
        rate, valid = loader(reference)
        rate_value = np.ascontiguousarray(rate, dtype="float32")
        valid_value = np.ascontiguousarray(valid, dtype="uint8")
        rate_value.setflags(write=False)
        valid_value.setflags(write=False)
        value = (rate_value, valid_value)
        self._values[key] = value
        self._values.move_to_end(key)
        while len(self._values) > self._maximum_entries:
            self._values.popitem(last=False)
        return value

    @property
    def size(self) -> int:
        return len(self._values)


class StatusStore:
    def __init__(self, initial: ShadowProbeStatus) -> None:
        self._lock = threading.Lock()
        self._value = initial

    def replace(self, value: ShadowProbeStatus) -> None:
        with self._lock:
            self._value = value

    def value(self) -> ShadowProbeStatus:
        with self._lock:
            return self._value


def parse_analysis_catalog(
    payload: Any,
    *,
    grid_id: str,
) -> list[AnalysisReference]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ShadowProbeError("analysis catalog payload is invalid")
    references: list[AnalysisReference] = []
    seen: set[datetime] = set()
    for item in payload["items"]:
        if not isinstance(item, dict) or item.get("grid_id") != grid_id:
            continue
        uri = item.get("analysis_uri")
        identity = item.get("analysis_id")
        raw_time = item.get("analysis_time")
        if not isinstance(uri, str) or not uri or not isinstance(identity, str):
            continue
        try:
            analysis_time = _parse_time(raw_time)
        except ShadowProbeError:
            continue
        if analysis_time in seen:
            continue
        seen.add(analysis_time)
        references.append(
            AnalysisReference(
                analysis_id=identity,
                analysis_time=analysis_time,
                grid_id=grid_id,
                analysis_uri=uri,
            )
        )
    references.sort(key=lambda item: item.analysis_time)
    return references


def select_latest_complete_sequence(
    references: list[AnalysisReference],
    *,
    profile: NowcastNetShadowProfile,
) -> tuple[datetime | None, list[AnalysisReference]]:
    by_time = {item.analysis_time: item for item in references}
    candidates = sorted(
        (
            item.analysis_time
            for item in references
            if cadence_aligned(
                item.analysis_time,
                profile.issue_cadence_minutes,
            )
        ),
        reverse=True,
    )
    for issue_time in candidates:
        required = required_frame_times(
            issue_time,
            input_frames=profile.input_frames,
            timestep_minutes=profile.timestep_minutes,
            issue_cadence_minutes=profile.issue_cadence_minutes,
        )
        if all(value in by_time for value in required):
            return issue_time, [by_time[value] for value in required]
    return (candidates[0] if candidates else None), []


def probe_sequence(
    references: list[AnalysisReference],
    *,
    profile: NowcastNetShadowProfile,
    loader: FrameLoader,
    checked_at: datetime | None = None,
) -> ShadowProbeStatus:
    now = (checked_at or datetime.now(UTC)).astimezone(UTC)
    issue_time, selected = select_latest_complete_sequence(
        references,
        profile=profile,
    )
    if issue_time is None:
        return _status(
            profile,
            now,
            status="waiting",
            reason="no_analysis_cycle",
        )
    required = required_frame_times(
        issue_time,
        input_frames=profile.input_frames,
        timestep_minutes=profile.timestep_minutes,
        issue_cadence_minutes=profile.issue_cadence_minutes,
    )
    if not selected:
        return _status(
            profile,
            now,
            status="input_ineligible",
            reason="missing_required_frame",
            issue_time=issue_time,
            required=required,
        )

    rates: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    for reference in selected:
        rate, valid = loader(reference)
        rates.append(np.asarray(rate, dtype="float32"))
        masks.append(np.asarray(valid, dtype="uint8"))
    shape = rates[0].shape
    if any(rate.shape != shape for rate in rates) or any(
        mask.shape != shape for mask in masks
    ):
        return _status(
            profile,
            now,
            status="input_ineligible",
            reason="source_grid_shape_mismatch",
            issue_time=issue_time,
            required=required,
        )
    prepared = prepare_shadow_input(
        [item.analysis_time for item in selected],
        np.stack(rates, axis=0),
        np.stack(masks, axis=0),
        issue_time=issue_time,
        profile=profile,
    )
    status = "input_eligible" if prepared.eligible else "input_ineligible"
    return _status(
        profile,
        now,
        status=status,
        reason=prepared.reason,
        issue_time=issue_time,
        required=prepared.frame_times,
        frame_count=len(selected),
        common_valid_ratio=prepared.common_valid_ratio,
    )


def load_analysis_frame(
    reader: Any,
    reference: AnalysisReference,
) -> tuple[np.ndarray, np.ndarray]:
    # Keep the probe module import-safe for lightweight control-plane tests.
    # Zarr/MinIO are loaded only by the running service.
    import zarr
    from zarr.storage import MemoryStore

    from rainpulse_algo.radar.analysis_zarr import (
        validate_radar_analysis_zarr_store,
    )

    objects = reader.load(reference.analysis_uri)
    validate_radar_analysis_zarr_store(objects)
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("analysis_id") != reference.analysis_id:
        raise ShadowProbeError(
            "RadarAnalysis identity differs from the catalog"
        )
    if root.attrs.get("grid_id") != reference.grid_id:
        raise ShadowProbeError("RadarAnalysis grid differs from the catalog")
    if _parse_time(root.attrs.get("analysis_time")) != reference.analysis_time:
        raise ShadowProbeError("RadarAnalysis time differs from the catalog")
    return (
        np.asarray(root["RATE_QPE"][:], dtype="float32"),
        np.asarray(root["VALID_MASK"][:], dtype="uint8"),
    )


def fetch_catalog(url: str, *, timeout_seconds: float = 5.0) -> Any:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        # The URL comes from a deployment-only internal service setting.
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise ShadowProbeError(
                    f"analysis catalog returned HTTP {response.status}"
                )
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ShadowProbeError(f"cannot read analysis catalog: {exc}") from exc


def run_probe_loop(
    store: StatusStore,
    *,
    profile: NowcastNetShadowProfile,
    catalog_url: str,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    from rainpulse_algo.worker.object_store import (
        ArtifactObjectReader,
        minio_client_from_environment,
    )

    reader = ArtifactObjectReader(minio_client_from_environment())
    cache_size = int(
        os.getenv("RAINPULSE_NOWCASTNET_SHADOW_FRAME_CACHE_SIZE", "32")
    )
    if cache_size < profile.input_frames or cache_size > 512:
        raise ShadowProbeError(
            "shadow frame cache size must be between input frame count and 512"
        )
    frame_cache = AnalysisFrameCache(cache_size)
    while not stop_event.is_set():
        checked_at = datetime.now(UTC)
        try:
            catalog = fetch_catalog(catalog_url)
            references = parse_analysis_catalog(
                catalog,
                grid_id=profile.grid_id,
            )
            status = probe_sequence(
                references,
                profile=profile,
                loader=lambda reference: frame_cache.load(
                    reference,
                    lambda item: load_analysis_frame(reader, item),
                ),
                checked_at=checked_at,
            )
        except Exception as exc:  # noqa: BLE001 - normalize the service boundary
            status = _status(
                profile,
                checked_at,
                status="failed",
                reason=f"{type(exc).__name__}:{exc}",
            )
        store.replace(status)
        stop_event.wait(interval_seconds)


def serve_status(
    store: StatusStore,
    address: tuple[str, int],
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            if self.path not in {"/healthz", "/status"}:
                self.send_error(404)
                return
            payload = json.dumps(
                asdict(store.value()),
                separators=(",", ":"),
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(address, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> None:
    profile_path = _required_path("RAINPULSE_NOWCASTNET_SHADOW_CONFIG")
    profile = load_nowcastnet_shadow_profile(profile_path)
    catalog_url = os.getenv(
        "RAINPULSE_ANALYSIS_CATALOG_URL",
        "http://api:8080/api/v1/analysis-cycles?status=ANALYSIS_READY&limit=200",
    )
    interval = float(
        os.getenv("RAINPULSE_NOWCASTNET_SHADOW_INTERVAL_SECONDS", "30")
    )
    if interval < 5 or interval > 300:
        raise ShadowProbeError(
            "shadow probe interval must be between 5 and 300 seconds"
        )
    host = os.getenv(
        "RAINPULSE_NOWCASTNET_SHADOW_STATUS_HOST",
        "0.0.0.0",
    )
    port = int(
        os.getenv("RAINPULSE_NOWCASTNET_SHADOW_STATUS_PORT", "8094")
    )
    initial = _status(
        profile,
        datetime.now(UTC),
        status="starting",
        reason=None,
    )
    store = StatusStore(initial)
    server = serve_status(store, (host, port))
    stop_event = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        run_probe_loop(
            store,
            profile=profile,
            catalog_url=catalog_url,
            interval_seconds=interval,
            stop_event=stop_event,
        )
    finally:
        server.shutdown()
        server.server_close()


def _status(
    profile: NowcastNetShadowProfile,
    checked_at: datetime,
    *,
    status: str,
    reason: str | None,
    issue_time: datetime | None = None,
    required: tuple[datetime, ...] = (),
    frame_count: int = 0,
    common_valid_ratio: float = 0.0,
) -> ShadowProbeStatus:
    roi = profile.roi
    return ShadowProbeStatus(
        status=status,
        reason=reason,
        checked_at=checked_at.astimezone(UTC).isoformat(),
        profile_version=profile.profile_version,
        grid_id=profile.grid_id,
        grid_config_version=profile.grid_config_version,
        issue_time=(
            issue_time.astimezone(UTC).isoformat()
            if issue_time
            else None
        ),
        required_frame_times=tuple(
            value.astimezone(UTC).isoformat() for value in required
        ),
        frame_count=frame_count,
        common_valid_ratio=common_valid_ratio,
        roi={
            "y_start": roi.y_start,
            "x_start": roi.x_start,
            "height": roi.height,
            "width": roi.width,
        },
        issue_cadence_minutes=profile.issue_cadence_minutes,
        input_timestep_minutes=profile.timestep_minutes,
        inference_enabled=profile.activation.inference_enabled,
        spatial_shape_validated=(
            profile.activation.spatial_shape_validated
        ),
    )


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ShadowProbeError("analysis time is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ShadowProbeError("analysis time is invalid") from exc
    if parsed.utcoffset() is None:
        raise ShadowProbeError("analysis time lacks UTC offset")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _required_path(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ShadowProbeError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise ShadowProbeError(f"{name} must identify a file")
    return path


if __name__ == "__main__":
    main()
