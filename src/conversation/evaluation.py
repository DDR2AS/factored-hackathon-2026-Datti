"""GET /evaluation: the latest evaluation report for the app's evaluation page (owner: arturo).

``make eval`` (``scripts/evaluate.py``, ``src/eval/report.py``) writes the report; this module
only reads it, for a public route (no auth: judges open the page cold).

- Where: ``EVAL_REPORT_PATH`` if set, else ``<repo>/data/eval/report_latest.yaml`` resolved from
  this file. When that file does not exist and ``BUCKET_ARTIFACTS`` is set (the Lambda, whose
  package is only ``src/``), the object ``eval/report_latest.yaml`` of that bucket, read with
  boto3 inside a try/except.
- What: the report without ``rows`` (the per-scenario rows are most of its ~400 KB; only their
  count per configuration is sent, as ``row_counts``), normalized by ``contract.EvaluationReport``:
  a missing or mistyped value reads as null, unknown keys are dropped. Inside the loose model
  summaries (``models.m1/m2/m3``) per-item lists longer than ``MAX_MODEL_LIST`` are dropped too
  (a model report that dumps its per-message test predictions: ~46 KB for M2 on 29 oct, which the
  page never shows). ``configs[].env`` never carries a variable whose name looks like a secret.
- Missing, unreadable, too big or not a YAML mapping: ``{"available": false, "report": null}``,
  still HTTP 200. ``evaluation_response`` never raises.
- Cache: one entry per process, keyed by the file's path, mtime and size (a new ``make eval``
  shows on the next request); the S3 copy is read again after ``S3_TTL_SECONDS``.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from conversation.contract import EvaluationReport, EvaluationResponse

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = REPO_ROOT / "data" / "eval" / "report_latest.yaml"
REPORT_PATH_ENV = "EVAL_REPORT_PATH"
BUCKET_ENV = "BUCKET_ARTIFACTS"
S3_KEY = "eval/report_latest.yaml"
S3_TTL_SECONDS = 60.0
MAX_REPORT_BYTES = 8 * 1024 * 1024
# The report without its rows is ~2,000 values; a YAML full of aliases could expand to far more.
MAX_VALUES = 200_000
MAX_DEPTH = 40
MAX_MODEL_LIST = 50
SECRET_NAME = re.compile(r"key|token|secret|passw|credential|auth", re.IGNORECASE)
# The C loader is the same safe loader (no Python objects), ~7x faster on this file.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "body": None}


def unavailable() -> dict:
    return {"available": False, "report": None, "synthetic": True}


def report_path() -> Path:
    raw = (os.environ.get(REPORT_PATH_ENV) or "").strip()
    return Path(raw) if raw else DEFAULT_REPORT


def evaluation_response(clock: Callable[[], float] = time.monotonic) -> dict:
    """The body of GET /evaluation (contract.EvaluationResponse). Never raises."""
    try:
        return _cached(clock)
    except Exception as e:  # a report can never break the route
        logger.error("evaluation report: unexpected %s", type(e).__name__)
        return unavailable()


def clear_cache() -> None:
    with _lock:
        _cache.update(key=None, at=0.0, body=None)


def _cached(clock: Callable[[], float]) -> dict:
    path = report_path()
    try:
        st = path.stat()
    except OSError:
        st = None
    if st is not None:
        key: tuple = ("file", str(path), st.st_mtime_ns, st.st_size)
        ttl = None
    else:
        bucket = (os.environ.get(BUCKET_ENV) or "").strip()
        if not bucket:
            return unavailable()
        key, ttl = ("s3", bucket, S3_KEY), S3_TTL_SECONDS
    with _lock:
        if _cache["key"] == key and (ttl is None or clock() - _cache["at"] < ttl):
            return _cache["body"]
        if st is not None:
            raw = _read_file(path, st.st_size)
        else:
            raw = _read_s3(key[1], S3_KEY)
        body = build_response(_parse(raw)) if raw is not None else unavailable()
        _cache.update(key=key, at=clock(), body=body)
        return body


def _read_file(path: Path, size: int) -> bytes | None:
    if size > MAX_REPORT_BYTES:
        logger.warning("evaluation report: %s is larger than %d bytes; not served", path.name, MAX_REPORT_BYTES)
        return None
    try:
        return path.read_bytes()
    except OSError as e:
        logger.warning("evaluation report: cannot read %s (%s)", path.name, type(e).__name__)
        return None


def _s3_client():
    import boto3  # in the Lambda runtime; locally only with BUCKET_ARTIFACTS set
    from botocore.config import Config

    return boto3.client("s3", config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}))


def _read_s3(bucket: str, key: str) -> bytes | None:
    try:
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
        if int(obj.get("ContentLength") or 0) > MAX_REPORT_BYTES:
            logger.warning("evaluation report: s3 object is larger than %d bytes; not served", MAX_REPORT_BYTES)
            return None
        data = obj["Body"].read(MAX_REPORT_BYTES + 1)
    except Exception as e:  # no boto3, no region, no object, no permission, network: not available
        logger.warning("evaluation report: s3 read failed (%s)", type(e).__name__)
        return None
    if len(data) > MAX_REPORT_BYTES:
        logger.warning("evaluation report: s3 object is larger than %d bytes; not served", MAX_REPORT_BYTES)
        return None
    return data


def _parse(raw: bytes) -> Any:
    try:
        return yaml.load(raw.decode("utf-8"), Loader=_LOADER)  # noqa: S506 (safe loader)
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError) as e:
        logger.warning("evaluation report: not valid YAML (%s)", type(e).__name__)
        return None


class _TooBig(Exception):
    pass


def build_response(data: Any) -> dict:
    """EvaluationResponse body for a loaded report; ``unavailable()`` unless it is a mapping."""
    if not isinstance(data, dict):
        if data is not None:
            logger.warning("evaluation report: the YAML is a %s, not a mapping", type(data).__name__)
        return unavailable()
    rows = data.get("rows")
    budget = [MAX_VALUES]
    try:
        clean = {_key(k): _jsonable(v, 1, budget) for k, v in data.items() if k != "rows"}
    except _TooBig:
        logger.warning("evaluation report: more than %d values without the rows; not served", MAX_VALUES)
        return unavailable()
    clean["row_counts"] = ({_key(k): len(v) for k, v in rows.items() if isinstance(v, list)}
                           if isinstance(rows, dict) else None)
    if isinstance(clean.get("configs"), list):
        for cfg in clean["configs"]:
            if isinstance(cfg, dict) and isinstance(cfg.get("env"), dict):
                cfg["env"] = {k: v for k, v in cfg["env"].items() if not SECRET_NAME.search(k)}
    if isinstance(clean.get("models"), dict):
        clean["models"] = {k: _drop_long_lists(v) for k, v in clean["models"].items()}
    try:
        report = EvaluationReport.model_validate(clean)
        body = EvaluationResponse(available=True, report=report, synthetic=True)
    except ValueError as e:  # pydantic's ValidationError included; not expected with _Lenient
        logger.warning("evaluation report: does not match the contract (%s)", type(e).__name__)
        return unavailable()
    return body.model_dump(mode="json", by_alias=True)


def _drop_long_lists(value: Any) -> Any:
    """The value without the lists of more than MAX_MODEL_LIST items (per-item dumps)."""
    if isinstance(value, dict):
        return {k: _drop_long_lists(v) for k, v in value.items()
                if not (isinstance(v, list) and len(v) > MAX_MODEL_LIST)}
    if isinstance(value, list):
        return [_drop_long_lists(v) for v in value]
    return value


def _key(k: Any) -> str:
    """Mapping keys as JSON writes them: null, true, false, numbers and dates as text."""
    if k is None:
        return "null"
    if isinstance(k, bool):
        return "true" if k else "false"
    if isinstance(k, (date, datetime)):
        return k.isoformat()
    return str(k)


def _jsonable(value: Any, depth: int, budget: list[int]) -> Any:
    """Plain JSON values: dates as ISO text, NaN and infinities as None, unknown types (binary)
    as None. Raises _TooBig past MAX_VALUES values (aliases expanded)."""
    budget[0] -= 1
    if budget[0] < 0:
        raise _TooBig
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if depth >= MAX_DEPTH:
        return None
    if isinstance(value, dict):
        return {_key(k): _jsonable(v, depth + 1, budget) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1, budget) for v in value]
    return None
