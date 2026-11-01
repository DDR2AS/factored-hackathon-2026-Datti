"""boto3 clients shared by the cloud adapters: one per (service, region, config) and process.

Short timeouts and the standard retry mode (3 attempts with backoff): a chat turn never waits
long for AWS, and a throttled call is retried by botocore before it reaches our code. Region:
``AWS_REGION`` (Lambda always sets it), else ``AWS_DEFAULT_REGION``, else us-east-2 (cdk.json).
Tests inject their own clients or call ``reset_clients()`` inside ``moto.mock_aws``.
"""

from __future__ import annotations

import os
import threading
from typing import Any

DEFAULT_REGION = "us-east-2"
_lock = threading.Lock()
_clients: dict[tuple, Any] = {}
_session: Any = None  # one boto3 session per process: its model cache makes a new client ~5x cheaper


def region_name(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or DEFAULT_REGION


def client(service: str, region: str | None = None, *, connect_timeout: float = 2.0,
           read_timeout: float = 5.0, max_attempts: int = 3) -> Any:
    """A cached boto3 client. ``read_timeout`` is per request (raise it for S3 transfers)."""
    region = region or region_name()
    key = (service, region, connect_timeout, read_timeout, max_attempts)
    with _lock:
        found = _clients.get(key)
        if found is not None:
            return found
    import boto3  # in the Lambda runtime
    from botocore.config import Config

    global _session
    cfg = Config(connect_timeout=connect_timeout, read_timeout=read_timeout,
                 retries={"max_attempts": max_attempts, "mode": "standard"})
    with _lock:  # a boto3 session is not thread safe: create clients one at a time
        found = _clients.get(key)
        if found is None:
            if _session is None:
                _session = boto3.session.Session()
            found = _clients[key] = _session.client(service, region_name=region, config=cfg)
        return found


def reset_clients() -> None:
    """Forget the cached clients and session (tests: a new moto mock needs new clients)."""
    global _session
    with _lock:
        _clients.clear()
        _session = None


def error_code(exc: BaseException) -> str:
    """The AWS error code of a botocore ClientError ("" for anything else)."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return ""


def is_aws_error(exc: BaseException) -> bool:
    """ClientError or any other botocore failure (timeouts, connection errors)."""
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:  # pragma: no cover - boto3 is always there when this runs
        return False
    return isinstance(exc, (BotoCoreError, ClientError))
