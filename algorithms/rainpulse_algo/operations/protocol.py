# ruff: noqa: E501, I001
"""Small, bounded HTTP receipts and frozen-input checks for managed workers."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

MAX_MARKER = 16 * 1024**2


class ControlError(RuntimeError):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"control response {status}: {code}")
        self.status, self.code = status, code


class FrozenInputChanged(Exception):
    """Not an optional-context transport error: must propagate through legacy QC."""


class ConfigurationChanged(Exception):
    pass


class CancelRequested(Exception):
    pass


_SECRET = re.compile(
    r'''(?i)(password|passwd|secret|access_key|api_key|token|authorization)(["']?\s*[:=]\s*["']?)([^\s,"'}]+)'''
)
_BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+")
_CREDS = re.compile(r"(https?://|postgres(?:ql)?://)[^\s/@:]+:[^\s/@]+@")


def redact(text: str, maximum: int | None = 4096) -> str:
    text = _CREDS.sub(r"\1[REDACTED]@", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET.sub(r"\1\2[REDACTED]", text)
    # Also redact configured secrets when a library prints a bare value.
    for key, value in os.environ.items():
        if len(value) >= 6 and any(word in key for word in ("PASSWORD", "SECRET", "TOKEN")):
            text = text.replace(value, "[REDACTED]")
    return text if maximum is None else text[:maximum]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a worker credential to a redirect target.


class ControlClient:
    def __init__(self, base: str, token: str, *, timeout: float = 10) -> None:
        from urllib.parse import urlsplit

        u = urlsplit(base)
        if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.query or u.fragment or u.path not in {"", "/"}:
            raise ValueError("managed control URL must be an HTTP(S) origin without path, query or credentials")
        if not token:
            raise ValueError("RAINPULSE_OPS_WORKER_TOKEN is required")
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def post(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > 1024**2:
            raise ValueError("control receipt exceeds 1 MiB")
        request = urllib.request.Request(
            self.base + "/internal/ops/v1/" + route,
            data=data,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024**2 + 1)
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read(4096))
                code = str(body.get("code", "request_failed"))[:96]
            except (ValueError, TypeError):
                code = "request_failed"
            raise ControlError(error.code, code) from None
        if len(raw) > 2 * 1024**2:
            raise ControlError(502, "response_too_large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ControlError(502, "invalid_response")
        return value


def transient(error: BaseException) -> bool:
    if isinstance(error, ControlError):
        return error.status in {429, 500, 502, 503, 504}
    return isinstance(error, (TimeoutError, ConnectionError, OSError, urllib.error.URLError))


def retry_io(call: Callable[[], Any], *, sleep=time.sleep, attempts: int = 3) -> Any:
    """Retry transport, never a numerical executor. Errors retain their category."""
    for number in range(attempts):
        try:
            return call()
        except Exception as error:
            if not transient(error) or number == attempts - 1:
                raise
            sleep(min(4, 2**number))
    raise AssertionError("unreachable")


class MarkerResponse(io.BytesIO):
    def release_conn(self) -> None:
        pass


class PinnedClient:
    """Pin every declared _SUCCESS read; existing readers verify object bytes.

    This avoids a second full download during preflight and forbids an optional
    context loader from accepting a changed marker as a mere missing observation.
    """
    def __init__(self, client: Any, inputs: list[dict[str, Any]]) -> None:
        self.client = client
        self.expected = {v["uri"].rstrip("/") + "/_SUCCESS.json": v["marker_sha256"] for v in inputs}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def get_object(self, bucket: str, key: str, *args, **kwargs):
        response = self.client.get_object(bucket, key, *args, **kwargs)
        expected = self.expected.get("s3://" + bucket + "/" + key)
        if expected is None:
            return response
        try:
            data = response.read(MAX_MARKER + 1)
        finally:
            response.close()
            response.release_conn()
        if len(data) > MAX_MARKER or hashlib.sha256(data).hexdigest() != expected:
            raise FrozenInputChanged("frozen input completion marker changed")
        return MarkerResponse(data)
