"""HTTP API entry point (API Gateway HTTP API, payload format 2.0). INTERFACES.md #1.

Owner: arturo. Thin router by ``routeKey``; the logic lives in ``conversation.app``.

- ``{"warmup": true}`` (EventBridge ping) answers ``{"warm": true}`` without touching anything.
- ``GET /health`` works even if pydantic/PyYAML are missing from the bundle: the
  conversation package is imported lazily, only by the routes that use it, and /health
  reports ``deps: "ok" | "missing"``.
- ``POST /session``, ``POST /chat``, ``GET /cases/{case_id}``: customer routes with the app's
  own session token, sent as ``Authorization: Bearer <token>`` or, as plan B when
  CloudFront drops Authorization on GET, as ``x-ev-session: <token>``.
- ``/analyst/*``: the analyst console (``conversation.analyst``). API Gateway's Cognito JWT
  authorizer runs first; the claims arrive in ``requestContext.authorizer.jwt.claims`` and
  are the ONLY identity used (``decided_by`` = claim ``sub``). Without claims: 401
  ``session_expired``. Locally, ``scripts/local_api.py`` imitates the authorizer.
- Errors: ``{"error": {"code", "message", "retryable"}}`` with the HTTP status of the
  contract (docs section 7). Every response is JSON with ``Cache-Control: no-store``.
- Never logged: tokens, message text, request bodies. There is no authentication bypass.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
import traceback

logger = logging.getLogger(__name__)

HEADERS = {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}
ANALYST_ROUTES = {
    "GET /analyst/cases",
    "GET /analyst/cases/{case_id}",
    "POST /analyst/cases/{case_id}/decision",
}
MAX_BODY_BYTES = 16 * 1024

_APP = None


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": dict(HEADERS), "body": json.dumps(body, ensure_ascii=False)}


def _error(status: int, code: str, message: str, retryable: bool) -> dict:
    # Built without pydantic so it also works when the dependencies are missing.
    return _response(status, {"error": {"code": code, "message": message, "retryable": retryable}})


def deps_status() -> str:
    """"ok" if the conversation package can be imported (pydantic and PyYAML present)."""
    try:
        ok = all(importlib.util.find_spec(m) is not None for m in ("pydantic", "yaml"))
    except (ImportError, ValueError):
        ok = False
    return "ok" if ok else "missing"


def demo_clock_scale() -> float | None:
    """Factor of the judge switch ``fast_clock`` on this server, for GET /health: the local
    lifecycle's ``sla_scale`` (``LOCAL_SLA_SCALE``, 1/1440 by default). None where there is no
    demo clock: ``LIFECYCLE_BACKEND`` other than local, or the dependencies missing. Never
    builds the app (health stays cheap)."""
    if _APP is not None:
        value = getattr(getattr(_APP, "lifecycle", None), "sla_scale", None)
    else:
        if (os.environ.get("LIFECYCLE_BACKEND") or "").strip().lower() not in ("", "local"):
            return None
        try:
            from conversation.lifecycle import sla_scale_from_env
        except ImportError:
            return None
        value = sla_scale_from_env()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
        return None
    return float(value)


def get_app():
    """The process-wide ChatApp, built from the environment on first use (lazy import)."""
    global _APP
    if _APP is None:
        from conversation.app import build_app

        _APP = build_app()
    return _APP


def set_app(app) -> None:
    """Dependency injection for tests and scripts/local_api.py (clock, stores, fakes)."""
    global _APP
    _APP = app


class _BadRequest(Exception):
    pass


def _body(event: dict):
    raw = event.get("body")
    if raw is None or raw == "":
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise _BadRequest("body is not valid base64 UTF-8")
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise _BadRequest("body too large")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):  # RecursionError: deeply nested JSON under the size limit
        raise _BadRequest("body is not valid JSON")


def _token(event: dict) -> str | None:
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = (headers.get("authorization") or "").strip()
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
        return None
    alt = (headers.get("x-ev-session") or "").strip()
    return alt or None


def _jwt_claims(event: dict) -> dict | None:
    """Claims put by API Gateway's JWT authorizer (payload 2.0). Never read from headers or body."""
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = (authorizer.get("jwt") or {}).get("claims") if isinstance(authorizer, dict) else None
    return claims if isinstance(claims, dict) else None


def _source_ip(event: dict) -> str | None:
    """Caller address from API Gateway (requestContext.http.sourceIp), for the session rate limit."""
    http = ((event.get("requestContext") or {}).get("http") or {})
    ip = http.get("sourceIp")
    return str(ip) if ip else None


def handler(event, context):
    if event.get("warmup"):
        return {"warm": True}

    route = event.get("routeKey", "")
    if route == "GET /health":
        return _response(200, {"status": "ok", "stage": os.environ.get("STAGE"), "deps": deps_status(),
                               "demo_clock_scale": demo_clock_scale()})
    if route not in ANALYST_ROUTES and route not in ("POST /session", "POST /chat", "GET /cases/{case_id}"):
        return _response(404, {"message": "Not Found"})

    try:
        from conversation.contract import ApiFailure
        from conversation.store import StoreConfigError
    except ImportError:
        logger.error("dependencies missing for %s", route)
        return _error(500, "internal", "server dependencies missing (pydantic, PyYAML)", False)

    try:
        app = get_app()
        if route == "POST /session":
            return _response(200, app.create_session(_body(event), source_ip=_source_ip(event)))
        if route == "POST /chat":
            return _response(200, app.chat(_token(event), _body(event)))
        case_id = (event.get("pathParameters") or {}).get("case_id")
        if route == "GET /cases/{case_id}":
            return _response(200, app.get_case(_token(event), case_id))
        claims = _jwt_claims(event)
        if route == "GET /analyst/cases":
            return _response(200, app.analyst.list_cases(claims, event.get("queryStringParameters") or {}))
        if route == "GET /analyst/cases/{case_id}":
            return _response(200, app.analyst.get_detail(claims, case_id))
        return _response(200, app.analyst.decide(claims, case_id, _body(event)))
    except _BadRequest as e:
        return _error(400, "invalid_request", str(e), False)
    except ApiFailure as e:
        return _response(e.http_status, e.body())
    except StoreConfigError as e:
        logger.error("store configuration: %s", e)
        return _error(500, "internal", "server store is not configured", False)
    except Exception as e:  # never leak details; log the type and the stack, not the message
        logger.error("unhandled %s on %s\n%s", type(e).__name__, route, "".join(traceback.format_tb(e.__traceback__)))
        return _error(500, "internal", "internal error", True)
