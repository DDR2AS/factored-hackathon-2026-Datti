"""#7 trace sinks for Lambda, chosen by ``TRACE_SINK`` (INTERFACES.md #7, #9).

- ``stdout`` (default with ``STORE_BACKEND=dynamodb``): one JSON line per event on stdout. Lambda
  sends it to the function's CloudWatch log group (retention 1 month, ``python_function``), where
  Logs Insights parses the fields (``filter ispresent(trace_id) | stats pct(latency_ms, 95) by name``).
  No network call in the request path, so tracing adds no latency and cannot fail a turn.
- ``s3``: events are buffered and written as JSONL objects to ``BUCKET_TRACES`` under
  ``traces/dt=<UTC date>/`` (Athena-friendly partitions, the bucket expires them after 90 days).
  The buffer is written every ``S3_MAX_EVENTS`` events, when the oldest buffered event is older
  than ``S3_MAX_AGE_SECONDS`` (checked on the next event), on ``flush()`` and at exit. The
  workflow handlers call ``flush()`` at the end of every invocation; the API handler does not
  have that hook, so a frozen container can hold its last events until the next request: use
  ``stdout`` for the API function. A failed upload falls back to stdout, so nothing is lost.
- ``jsonl``: the local ``JsonlTraceSink`` (``TRACE_DIR``), e.g. ``/tmp/traces`` in Lambda.

Every sink writes exactly the event it receives (the #7 keys plus ``source``) and never raises.
"""

from __future__ import annotations

import atexit
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

SINKS = ("stdout", "s3", "jsonl")
S3_PREFIX = "traces/"
S3_MAX_EVENTS = 200
S3_MAX_AGE_SECONDS = 30.0


def _line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, default=str, separators=(",", ":"))


class StdoutTraceSink:
    """CloudWatch-friendly: ``print`` of one JSON object per event (Lambda captures stdout)."""

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream
        self._lock = threading.Lock()

    def emit(self, event: dict[str, Any]) -> None:
        try:
            line = _line(event)
            with self._lock:
                out = self.stream or sys.stdout
                out.write(line + "\n")
                out.flush()
        except Exception:
            pass  # tracing never breaks a request

    def flush(self) -> None:
        return None


class S3TraceSink:
    """Buffered JSONL objects in S3; ``flush()`` writes the buffer. Never raises."""

    def __init__(self, bucket: str, client: Any = None, prefix: str = S3_PREFIX,
                 max_events: int = S3_MAX_EVENTS, max_age_seconds: float = S3_MAX_AGE_SECONDS,
                 clock: Callable[[], float] = time.monotonic, fallback: Any = None,
                 register_atexit: bool = True) -> None:
        self.bucket = bucket
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        self.max_events = max(1, int(max_events))
        self.max_age = float(max_age_seconds)
        self.clock = clock
        self._client = client
        self._fallback = fallback or StdoutTraceSink()
        self._lock = threading.Lock()
        self._buffer: list[dict[str, Any]] = []
        self._oldest: float | None = None
        self.objects_written = 0
        if register_atexit:
            atexit.register(self.flush)

    def _s3(self) -> Any:
        if self._client is None:
            from cloud import aws

            self._client = aws.client("s3", read_timeout=10.0)
        return self._client

    def emit(self, event: dict[str, Any]) -> None:
        try:
            with self._lock:
                if self._oldest is None:
                    self._oldest = self.clock()
                self._buffer.append(dict(event))
                due = (len(self._buffer) >= self.max_events
                       or self.clock() - (self._oldest or 0.0) >= self.max_age)
            if due:
                self.flush()
        except Exception:
            pass

    def flush(self) -> None:
        with self._lock:
            batch, self._buffer, self._oldest = self._buffer, [], None
        if not batch:
            return
        now = datetime.now(timezone.utc)
        key = f"{self.prefix}dt={now:%Y-%m-%d}/{now:%H%M%S}-{uuid.uuid4().hex[:12]}.jsonl"
        body = ("\n".join(_line(e) for e in batch) + "\n").encode("utf-8")
        try:
            self._s3().put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/x-ndjson")
            self.objects_written += 1
        except Exception:
            for event in batch:  # nothing is lost: CloudWatch keeps them
                self._fallback.emit(event)


def make_trace_sink(env: dict[str, str]) -> Any:
    """``TRACE_SINK`` = stdout (default) | s3 (needs ``BUCKET_TRACES``) | jsonl (``TRACE_DIR``)."""
    name = (env.get("TRACE_SINK") or "stdout").strip().lower()
    if name not in SINKS:
        from conversation.store import StoreConfigError

        raise StoreConfigError(f"unknown TRACE_SINK={name!r}; use {', '.join(SINKS)}")
    if name == "s3":
        bucket = (env.get("BUCKET_TRACES") or "").strip()
        if not bucket:
            from conversation.store import StoreConfigError

            raise StoreConfigError("TRACE_SINK=s3 needs BUCKET_TRACES (DataStack.table_environment, #9)")
        return S3TraceSink(bucket)
    if name == "jsonl":
        from conversation.store import DEFAULT_TRACE_DIR, JsonlTraceSink

        return JsonlTraceSink(env.get("TRACE_DIR") or DEFAULT_TRACE_DIR)
    return StdoutTraceSink()


def flush(sink: Any) -> None:
    """Flush a sink that buffers (S3); a no-op for the others. Never raises."""
    fn = getattr(sink, "flush", None)
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass
