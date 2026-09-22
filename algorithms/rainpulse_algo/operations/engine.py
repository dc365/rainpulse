# ruff: noqa: E501, I001
"""One attempt, one numerical invocation; durable completion is a separate step."""
from __future__ import annotations

import contextlib
import io
import math
import resource
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any

from .protocol import (
    CancelRequested,
    ConfigurationChanged,
    ControlError,
    FrozenInputChanged,
    redact,
    retry_io,
)


class AttemptJournal:
    def __init__(self, maximum: int = 200) -> None:
        self.lock = threading.Lock()
        self.lines: deque[dict[str, Any]] = deque()
        self.next_sequence = 1
        self.maximum = maximum
        self.dropped = 0

    def add(self, level: str, event: str, message: str) -> None:
        with self.lock:
            if len(self.lines) >= self.maximum:
                self.dropped += 1
                return
            self.lines.append({"sequence": self.next_sequence, "level": level,
                               "event": event[:64], "message": redact(message)})
            self.next_sequence += 1

    def batch(self) -> list[dict[str, Any]]:
        with self.lock:
            if self.dropped and len(self.lines) < self.maximum:
                self.lines.append({"sequence": self.next_sequence, "level": "warning",
                    "event": "logs.client_overflow", "message": f"{self.dropped}条日志未进入有限传输缓冲；完整日志请查容器"})
                self.next_sequence += 1
                self.dropped = 0
            return list(self.lines)[:20]

    def acknowledged(self, lines: list[dict[str, Any]]) -> None:
        if not lines:
            return
        with self.lock:
            while self.lines and self.lines[0]["sequence"] <= lines[-1]["sequence"]:
                self.lines.popleft()


class Tee(io.TextIOBase):
    def __init__(self, stream: Any, journal: AttemptJournal) -> None:
        self.stream, self.journal, self.buffer = stream, journal, ""

    def write(self, text: str) -> int:
        # Console remains useful even when control receipts are unavailable.
        self.stream.write(redact(text, maximum=None))
        self.buffer += text
        while "\n" in self.buffer or len(self.buffer) >= 4096:
            index = self.buffer.find("\n")
            index = 4096 if index < 0 or index > 4096 else index
            line, self.buffer = self.buffer[:index], self.buffer[index + (self.buffer[index:index+1] == "\n"):]
            if line:
                self.journal.add("info", "algorithm.output", line)
        return len(text)

    def flush(self) -> None:
        self.stream.flush()

    def finish(self) -> None:
        if self.buffer:
            self.journal.add("info", "algorithm.output", self.buffer)
            self.buffer = ""
        self.flush()


def process_metrics() -> dict[str, float]:
    values: dict[str, float] = {}
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        values["process_lifetime_peak_rss_bytes"] = float(peak if sys.platform == "darwin" else peak * 1024)
        if sys.platform.startswith("linux"):
            import os
            from pathlib import Path
            resident = int(Path("/proc/self/statm").read_text().split()[1])
            values["process_current_rss_bytes"] = float(resident * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        pass  # Absence is not a zero measurement.
    return values


class Engine:
    """Adapter and control are injectable; tests execute this same production code."""
    def __init__(self, control, adapter, *, interval: float = 10, sleep=time.sleep) -> None:
        self.control, self.adapter, self.interval, self.sleep = control, adapter, interval, sleep
        self.stage = "VERIFY_INPUT"
        self.journal = AttemptJournal()
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.receipt_lock = threading.Lock()
        self.metrics: dict[str, float] = {}
        self.claim: dict[str, Any] = {}

    def pulse(self) -> None:
        with self.receipt_lock:
            lines = self.journal.batch()
            current = process_metrics()
            peak = max(current.get("process_current_rss_bytes", 0), self.metrics.get("process_rss_sampled_peak_bytes", 0))
            self.metrics.update(current)
            if peak:
                self.metrics["process_rss_sampled_peak_bytes"] = peak
            response = self.control.post("heartbeat", {**self._identity(), "stage": self.stage,
                                                       "lines": lines, "metrics": dict(self.metrics)})
            self.journal.acknowledged(lines)
            if response.get("stop_requested"):
                self.cancel.set()

    def _identity(self) -> dict[str, str]:
        return {key: self.claim[key] for key in ("task_id", "attempt_id", "token")}

    def _heartbeat(self) -> None:
        while not self.finished.wait(self.interval):
            try:
                self.pulse()
            except ControlError as error:
                if error.status in {403, 409}:
                    self.cancel.set()  # Fenced: never submit a new publication.
                    return
            except Exception:
                # No automatic takeover. The API marks overdue heartbeats as stalled.
                continue

    def checkpoint(self, stage: str) -> None:
        self.stage = stage
        retry_io(self.pulse, sleep=self.sleep)
        if self.cancel.is_set():
            raise CancelRequested("operator cancellation or execution right revoked")
        self.adapter.check_identity(self.claim["identity"])

    def run(self, claim: dict[str, Any]) -> bool:
        self.claim = claim
        heartbeat = threading.Thread(target=self._heartbeat, daemon=True)
        heartbeat.start()
        committed = False
        start = time.perf_counter()
        try:
            self.checkpoint("VERIFY_INPUT")
            existing = retry_io(lambda: self.adapter.existing(claim), sleep=self.sleep)
            if existing:
                committed = True
                self.journal.add("info", "artifact.reused", "本次尝试已有完整产物，仅恢复登记")
            else:
                self.checkpoint("COMPUTE")
                out, err = Tee(sys.stdout, self.journal), Tee(sys.stderr, self.journal)
                compute = time.perf_counter()
                try:
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        result = self.adapter.execute(claim)
                finally:
                    out.finish()
                    err.finish()
                self.metrics["compute_wall_ms"] = (time.perf_counter() - compute) * 1000
                for key, value in self.adapter.metrics(result).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                        # Legacy QC rss_bytes is a process lifetime watermark, not task RSS.
                        name = "process_lifetime_peak_rss_bytes" if key == "rss_bytes" else key
                        if len(self.metrics) < 28:
                            self.metrics[name] = float(value)
                self.checkpoint("UPLOAD")
                upload = time.perf_counter()
                retry_io(lambda: self.adapter.publish(claim, result, start), sleep=self.sleep)
                self.metrics["publication_wall_ms"] = (time.perf_counter() - upload) * 1000
                committed = True
            self.stage = "COMMIT"
            self.journal.add("info", "artifact.committed", "完成标记已保存，正在登记候选结果")
            return self._finish("SUCCEEDED")
        except (CancelRequested, ControlError) as error:
            if isinstance(error, ControlError) and error.status not in {403, 409}:
                self.journal.add("error", "control.unavailable", str(error))
                return False  # Do not misclassify a lost result receipt as numerical failure.
            return self._finish("CANCELLED", "CANCEL_REQUESTED", str(error))
        except Exception as error:
            self.journal.add("error", "attempt.error", f"{type(error).__name__}: {error}")
            for line in traceback.format_exception(type(error), error, error.__traceback__):
                for part in line.splitlines():
                    self.journal.add("error", "attempt.traceback", part)
            if committed or self.stage in {"UPLOAD", "COMMIT"}:
                # A PUT may have succeeded before its response was lost. Keep
                # the attempt recoverable, rather than recording a false failure.
                return False
            blocked = _frozen_cause(error)
            return self._finish("BLOCKED" if blocked else "FAILED",
                                "FROZEN_INPUT_OR_CONFIG" if blocked else "ALGORITHM_OR_INPUT_ERROR",
                                f"{type(error).__name__}: {error}")
        finally:
            self.finished.set()
            heartbeat.join(timeout=15)

    def _finish(self, outcome: str, code: str = "", message: str = "") -> bool:
        try:
            # Flush a bounded backlog while keeping stable sequence numbers on retries.
            for _ in range(11):
                if not self.journal.batch():
                    break
                retry_io(self.pulse, sleep=self.sleep)
            with self.receipt_lock:
                lines = self.journal.batch()
                payload = {**self._identity(), "stage": self.stage, "lines": lines,
                           "metrics": dict(self.metrics), "outcome": outcome,
                           "error_code": code, "error_message": redact(message)[:2048]}
                retry_io(lambda: self.control.post("finish", payload), sleep=self.sleep)
                self.journal.acknowledged(lines)
            return True
        except ControlError as error:
            return error.status in {403, 409}  # Old attempt fenced; redelivery must not recompute it.
        except Exception:
            return False


def _frozen_cause(error: BaseException) -> bool:
    """Native stage wrappers retain the original cause; do not lose its meaning."""
    seen = set()
    for _ in range(12):
        if id(error) in seen:
            break
        seen.add(id(error))
        if isinstance(error, (FrozenInputChanged, ConfigurationChanged)):
            return True
        if type(error).__name__ == "S3Error" and getattr(error, "code", None) in {
            "NoSuchKey", "NoSuchObject", "NoSuchBucket"
        }:
            return True
        cause = error.__cause__ or error.__context__
        if cause is None:
            break
        error = cause
    return False
