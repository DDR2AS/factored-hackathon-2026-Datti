"""Local API server (owner: arturo). Standard library only.

Runs the SAME ``src/handlers/api.py`` the Lambda runs, behind a tiny imitation of
CloudFront + API Gateway:

- ``/api/*``: strips ``^/api`` like the CloudFront Function, resolves ``routeKey`` and
  ``pathParameters`` from a copy of the routes in ``infra/stacks/api_stack.py``
  (tests/test_local_api.py compares the copy with that file), builds an HTTP API v2 event
  and calls ``handlers.api.handler`` with a fake 29 s context. Unknown route: 404
  ``{"message": "Not Found"}`` like API Gateway; a handler slower than 30 s: 503.
- everything else: serves ``frontend/dist`` (SPA: ``index.html`` for paths without an
  extension).
- ``/api/analyst/*``: imitation of API Gateway's Cognito JWT authorizer. The request must carry
  ``Authorization: Bearer <LOCAL_ANALYST_TOKEN>`` (compared in constant time); otherwise 401
  ``{"message":"Unauthorized"}`` without calling the handler, like API Gateway. When it
  matches, the claims ``{sub: "analista-local", email: "analista@demo.local"}`` are put in
  ``event.requestContext.authorizer.jwt.claims``, where the Lambda reads them in the cloud.
  ``LOCAL_ANALYST_TOKEN`` comes from ``.env`` or the environment; if missing, one is generated
  at start and printed. Only this script knows that name: ``src/`` has no bypass.

Usage (from the repo root, venv active):
    python scripts/local_api.py [--port 8000] [--host 127.0.0.1] [--fail tools] [--fail model] [--ttl-minutes 15]
                                [--investigation-delay 4] [--llm mock|none] [--sla-scale 0.000694]

``--sla-scale`` (``LOCAL_SLA_SCALE``) is what the judge switch ``fast_clock`` does to the SLA
timers of that session's lane B cases (conversation.lifecycle): their times are multiplied by
it, 1/1440 by default (1 day = 1 minute, so the 80 % notice of a 15-day SLA comes after 12 min).
For a quick check use a tiny factor, e.g. ``--sla-scale 0.00001``: +24 h -> 0.9 s, 80 % -> 10.4 s,
breach -> 13 s. Sessions without fast_clock always run on real time. Local simulation only:
in the cloud the timers are EventBridge Scheduler schedules and there is no fast clock.

Loads ``.env`` if present (minimal KEY=VALUE parser, no new dependency) and defaults to
STAGE=local, STORE_BACKEND=memory, LLM_PROVIDER=mock. Real environment variables win, and
``--llm`` wins over both. G1 (conversation.g1) with ``mock``: the deterministic #8 mock answers
from tests/fixtures/llm/g1_extract/*.yaml (no network, no model; cost is an estimate); a text
without fixture is answered by the regex extractor. ``none``: regex extractor only.
Sessions and cases live in this process's memory and are lost on restart.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import mimetypes
import os
import posixpath
import re
import secrets
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
DIST = REPO_ROOT / "frontend" / "dist"

# Copy of infra/stacks/api_stack.py (team repo). Keep in sync; a test reads that file.
PUBLIC_ROUTES = [
    ("/health", "GET"),
    ("/session", "POST"),
    ("/chat", "POST"),
    ("/cases/{case_id}", "GET"),
]
ANALYST_ROUTES = [
    ("/analyst/cases", "GET"),
    ("/analyst/cases/{case_id}", "GET"),
    ("/analyst/cases/{case_id}/decision", "POST"),
]
ROUTES = PUBLIC_ROUTES + ANALYST_ROUTES
ANALYST_ROUTE_KEYS = {f"{m} {p}" for p, m in ANALYST_ROUTES}
# What the Cognito authorizer would put in the event for the local analyst (synthetic identity).
LOCAL_ANALYST_CLAIMS = {"sub": "analista-local", "email": "analista@demo.local"}
ANALYST_TOKEN_ENV = "LOCAL_ANALYST_TOKEN"

LOCAL_DEFAULTS = {"STAGE": "local", "STORE_BACKEND": "memory", "LLM_PROVIDER": "mock"}
LAMBDA_TIMEOUT_S = 29
GATEWAY_TIMEOUT_S = 30
# Same limit as handlers/api.py MAX_BODY_BYTES (copied so this file needs nothing from src/ to
# reject a body): a bigger Content-Length is answered 413 without reading the body.
MAX_BODY_BYTES = 16 * 1024


# ---------------------------------------------------------------- environment

def load_env_file(path: Path, environ: dict | None = None) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE per line, # comments, optional quotes, optional
    ``export``. Existing variables are not overwritten. Returns what it set."""
    environ = os.environ if environ is None else environ
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and key not in environ:
            environ[key] = value
            loaded[key] = value
    return loaded


def resolve_analyst_token(environ: dict | None = None) -> tuple[str, bool]:
    """(token, generated). ``LOCAL_ANALYST_TOKEN`` from the environment (``.env`` is loaded
    before), else a new ``secrets.token_urlsafe(24)`` that is also set in the environment."""
    environ = os.environ if environ is None else environ
    token = (environ.get(ANALYST_TOKEN_ENV) or "").strip()
    if token:
        return token, False
    token = secrets.token_urlsafe(24)
    environ[ANALYST_TOKEN_ENV] = token
    return token, True


def analyst_authorized(headers: dict[str, str], expected: str | None) -> bool:
    """Bearer token equal to ``expected`` (constant-time). No expected token: never authorized."""
    if not expected:
        return False
    auth = ""
    for k, v in headers.items():
        if k.lower() == "authorization":
            auth = (v or "").strip()
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return False
    return hmac.compare_digest(value.strip().encode("utf-8"), expected.encode("utf-8"))


# ---------------------------------------------------------------- routing (API Gateway imitation)

def _route_regex(template: str) -> re.Pattern:
    parts = re.split(r"(\{[a-zA-Z_][a-zA-Z0-9_]*\})", template)
    out = ""
    for p in parts:
        if p.startswith("{") and p.endswith("}"):
            out += f"(?P<{p[1:-1]}>[^/]+)"
        else:
            out += re.escape(p)
    return re.compile("^" + out + "$")


_COMPILED = [(tpl, method, _route_regex(tpl)) for tpl, method in ROUTES]


def strip_api_prefix(path: str) -> str:
    """Same regex as the CloudFront Function: ``^/api`` removed, empty becomes ``/``."""
    stripped = re.sub(r"^/api", "", path)
    return stripped or "/"


def resolve_route(method: str, path: str) -> tuple[str, dict[str, str]] | None:
    """(routeKey, pathParameters) for an API path (prefix already stripped), or None."""
    for tpl, m, rx in _COMPILED:
        if m != method:
            continue
        match = rx.match(path)
        if match:
            return f"{m} {tpl}", {k: unquote(v) for k, v in match.groupdict().items()}
    return None


def build_event(method: str, raw_path: str, query: str, headers: dict[str, str], body: bytes,
                route_key: str, path_params: dict[str, str], source_ip: str = "127.0.0.1") -> dict:
    """An API Gateway HTTP API payload v2.0 event, as the Lambda would receive it."""
    now = datetime.now(timezone.utc)
    lowered: dict[str, str] = {}
    for k, v in headers.items():
        k = k.lower()
        lowered[k] = f"{lowered[k]},{v}" if k in lowered else v
    text_types = ("application/json", "text/")
    ctype = lowered.get("content-type", "")
    is_text = not body or any(ctype.startswith(t) for t in text_types) or not ctype
    try:
        body_text = body.decode("utf-8") if is_text else None
    except UnicodeDecodeError:
        body_text = None
    event = {
        "version": "2.0",
        "routeKey": route_key,
        "rawPath": raw_path,
        "rawQueryString": query,
        "headers": lowered,
        "requestContext": {
            "accountId": "local",
            "apiId": "local",
            "domainName": lowered.get("host", "localhost"),
            "http": {"method": method, "path": raw_path, "protocol": "HTTP/1.1", "sourceIp": source_ip,
                     "userAgent": lowered.get("user-agent", "")},
            "requestId": uuid.uuid4().hex,
            "routeKey": route_key,
            "stage": "$default",
            "time": now.strftime("%d/%b/%Y:%H:%M:%S +0000"),
            "timeEpoch": int(now.timestamp() * 1000),
        },
        "isBase64Encoded": False,
    }
    if query:
        event["queryStringParameters"] = dict(p.split("=", 1) if "=" in p else (p, "") for p in query.split("&") if p)
    if path_params:
        event["pathParameters"] = path_params
    if body:
        if body_text is None:
            event["body"] = base64.b64encode(body).decode("ascii")
            event["isBase64Encoded"] = True
        else:
            event["body"] = body_text
    return event


class FakeContext:
    """Enough of the Lambda context for the handler: a 29 s deadline."""

    function_name = "local-api"
    memory_limit_in_mb = 1769
    invoked_function_arn = "arn:aws:lambda:local:000000000000:function:local-api"

    def __init__(self, timeout_s: float = LAMBDA_TIMEOUT_S):
        self.aws_request_id = uuid.uuid4().hex
        self._deadline = time.monotonic() + timeout_s

    def get_remaining_time_in_millis(self) -> int:
        return max(0, int((self._deadline - time.monotonic()) * 1000))


# ---------------------------------------------------------------- HTTP server

_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="lambda")


def invoke(event: dict, timeout_s: float = GATEWAY_TIMEOUT_S) -> dict:
    from handlers import api  # noqa: WPS433 (imported after sys.path is set)

    fut = _POOL.submit(api.handler, event, FakeContext())
    try:
        return fut.result(timeout=timeout_s)
    except FutureTimeout:
        return {"statusCode": 503, "headers": {"content-type": "application/json"},
                "body": json.dumps({"message": "Service Unavailable"})}


_UNSAFE_CHARS = re.compile(r"[\\:\x00-\x1f\x7f]")


def safe_relative_path(url_path: str) -> str | None:
    """The decoded URL path as a relative POSIX path inside dist, or None if it is unsafe.

    Checked on the string only, BEFORE any ``Path.resolve()``: on Windows, resolving
    ``//host/share/x`` written with backslashes, or ``C:/...``, opens an SMB connection or reads
    another drive (NTLM leak, thread blocked ~20 s).
    Rejects backslashes, drive letters and colons, control characters and NUL, absolute paths
    and any ``..`` segment. "" means the site root.
    """
    rel = unquote(url_path or "")
    if _UNSAFE_CHARS.search(rel):
        return None
    rel = rel.lstrip("/")
    if not rel:
        return ""
    if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).drive or PureWindowsPath(rel).anchor:
        return None
    if any(part == ".." for part in rel.split("/")):
        return None
    norm = posixpath.normpath(rel)
    if norm.startswith("../") or norm == ".." or norm.startswith("/"):
        return None
    return "" if norm == "." else norm


class Handler(BaseHTTPRequestHandler):
    server_version = "EVLocal/1.0"
    dist: Path = DIST
    analyst_token: str | None = None  # None: every /analyst/* request is 401

    def log_message(self, fmt, *args):  # no bodies, no tokens: method, path and status only
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for k, v in headers.items():
            if k.lower() not in ("content-length", "connection"):
                self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        if url.path == "/api" or url.path.startswith("/api/"):
            self._api(method, url.path, url.query)
        elif method == "GET":
            self._static(url.path)
        else:
            self._send(404, {"content-type": "application/json"}, b'{"message":"Not Found"}')

    def _reject_body(self, status: int, message: bytes) -> None:
        # The body was not read: close the connection so its bytes are never parsed as a request.
        self.close_connection = True
        self._send(status, {"content-type": "application/json", "connection": "close"}, message)

    def _api(self, method: str, path: str, query: str) -> None:
        raw_length = (self.headers.get("content-length") or "0").strip()
        if not raw_length.isdigit():  # negative, not a number, several values
            self._reject_body(400, b'{"message":"Bad Request"}')
            return
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            self._reject_body(413, b'{"message":"Request Entity Too Large"}')
            return
        body = self.rfile.read(length) if length else b""
        api_path = strip_api_prefix(path)
        resolved = resolve_route(method, api_path)
        if resolved is None:
            self._send(404, {"content-type": "application/json"}, b'{"message":"Not Found"}')
            return
        route_key, params = resolved
        if route_key in ANALYST_ROUTE_KEYS and not analyst_authorized(dict(self.headers.items()), self.analyst_token):
            self._send(401, {"content-type": "application/json"}, b'{"message":"Unauthorized"}')
            return
        event = build_event(method, api_path, query, dict(self.headers.items()), body, route_key, params,
                            source_ip=self.client_address[0])
        if route_key in ANALYST_ROUTE_KEYS:
            event["requestContext"]["authorizer"] = {"jwt": {"claims": dict(LOCAL_ANALYST_CLAIMS), "scopes": None}}
        result = invoke(event)
        if not isinstance(result, dict) or "statusCode" not in result:
            self._send(502, {"content-type": "application/json"}, b'{"message":"Internal Server Error"}')
            return
        raw = result.get("body") or ""
        data = base64.b64decode(raw) if result.get("isBase64Encoded") else raw.encode("utf-8")
        self._send(int(result["statusCode"]), result.get("headers") or {}, data)

    def _static(self, path: str) -> None:
        dist = self.dist.resolve()
        if not (dist / "index.html").exists():
            self._send(404, {"content-type": "text/plain; charset=utf-8"},
                       "frontend/dist no existe: corre `npm run build` en frontend/".encode("utf-8"))
            return
        rel = safe_relative_path(path)
        if rel is None:  # rejected before touching the file system (UNC, drive, NUL, "..")
            self._send(404, {"content-type": "text/plain"}, b"Not Found")
            return
        target = (dist / rel).resolve() if rel else dist / "index.html"
        if dist not in target.parents and target != dist / "index.html":
            self._send(404, {"content-type": "text/plain"}, b"Not Found")
            return
        if target.is_file():
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            cache = "no-cache" if target.name == "index.html" else "public, max-age=31536000, immutable"
            self._send(200, {"content-type": ctype, "cache-control": cache}, target.read_bytes())
            return
        if "." in Path(rel).name:  # a missing asset is a real 404
            self._send(404, {"content-type": "text/plain"}, b"Not Found")
            return
        index = dist / "index.html"  # SPA: hash routes and paths without extension
        self._send(200, {"content-type": "text/html; charset=utf-8", "cache-control": "no-cache"}, index.read_bytes())


def make_server(host: str, port: int, dist: Path = DIST, analyst_token: str | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"dist": dist, "analyst_token": analyst_token})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


class SlowModelClient:
    """Fake #8 client for ``--fail model``: answers only after the orchestrator gave up.

    It sleeps ``delay`` seconds (LLM_TIMEOUT_SECONDS + 1 by default), so every turn shows
    ``degraded: ["model_timeout"]`` and the regex extractor (or the ``model_slow`` template
    with fixed buttons, when the regex finds nothing) answers. No network, no real model.
    """

    def __init__(self, delay: float):
        self.delay = delay
        self._stop = threading.Event()

    def complete(self, prompt_id, variables, output_schema=None, model_role="chat") -> dict:
        self._stop.wait(self.delay)
        return {"output": {}, "usage": {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
                "model_id": "fake-slow-model", "prompt_version": "fail-model"}


def llm_timeout_seconds() -> float:
    try:
        return float(os.environ.get("LLM_TIMEOUT_SECONDS") or 8)
    except ValueError:
        return 8.0


def configure(env_file: Path | None, fail_tools: bool, ttl_minutes: float | None,
              fail_model: bool = False, investigation_delay: float | None = None,
              llm: str | None = None, sla_scale: float | None = None) -> None:
    """Environment, sys.path and demo switches; builds the app eagerly so errors show at start.
    ``llm`` ("mock" | "none") overrides LLM_PROVIDER from .env or the environment."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    load_env_file(env_file or REPO_ROOT / ".env")
    for k, v in LOCAL_DEFAULTS.items():
        os.environ.setdefault(k, v)
    if llm is not None:
        os.environ["LLM_PROVIDER"] = llm
    if ttl_minutes is not None:
        os.environ["SESSION_TTL_MINUTES"] = str(ttl_minutes)
    if investigation_delay is not None:
        os.environ["LOCAL_INVESTIGATION_DELAY_SECONDS"] = str(max(0.0, investigation_delay))
    if sla_scale is not None:
        os.environ["LOCAL_SLA_SCALE"] = repr(float(sla_scale))
    from conversation import demo_gateway
    from handlers import api

    demo_gateway.set_tool_failure(None, fail_tools)
    api.set_app(None)
    if fail_model:
        from conversation.app import build_app

        api.set_app(build_app(llm=SlowModelClient(llm_timeout_seconds() + 1)))
    api.get_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expediente Vivo: local API + frontend/dist")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--fail", choices=["tools", "model"], action="append", default=[],
                        help="simulate failures: 'tools' makes every gateway tool unavailable; 'model' "
                             "plugs a fake model that answers after LLM_TIMEOUT_SECONDS (default 8)")
    parser.add_argument("--ttl-minutes", type=float, default=None, help="session lifetime (default 15)")
    parser.add_argument("--investigation-delay", type=float, default=None,
                        help="seconds a lane B money case stays investigating before the (stub) report "
                             "(LOCAL_INVESTIGATION_DELAY_SECONDS, default 4)")
    parser.add_argument("--llm", choices=["mock", "none"], default=None,
                        help="G1 extractor: 'mock' (deterministic fixtures, default via LLM_PROVIDER) or "
                             "'none' (regex extractor only); overrides LLM_PROVIDER")
    parser.add_argument("--sla-scale", type=float, default=None,
                        help="factor the judge switch fast_clock applies to the SLA timers of the session's "
                             "cases (LOCAL_SLA_SCALE, default 1/1440: 1 day = 1 minute); 0 < x <= 1. For a "
                             "quick check: 0.00001 (80 %% notice after ~10 s, breach after ~13 s)")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--dist", type=Path, default=DIST)
    args = parser.parse_args(argv)
    if args.sla_scale is not None and not 0 < args.sla_scale <= 1:
        parser.error("--sla-scale must be > 0 and <= 1")

    configure(args.env_file, "tools" in args.fail, args.ttl_minutes, fail_model="model" in args.fail,
              investigation_delay=args.investigation_delay, llm=args.llm, sla_scale=args.sla_scale)
    token, generated = resolve_analyst_token()
    server = make_server(args.host, args.port, args.dist, analyst_token=token)
    print(f"API local en http://{args.host}:{args.port}/api/health · front en http://{args.host}:{args.port}/ "
          f"(STAGE={os.environ.get('STAGE')}, STORE_BACKEND={os.environ.get('STORE_BACKEND')}, "
          f"SESSION_TTL_MINUTES={os.environ.get('SESSION_TTL_MINUTES', '15')}, "
          f"LOCAL_INVESTIGATION_DELAY_SECONDS={os.environ.get('LOCAL_INVESTIGATION_DELAY_SECONDS', '4')}, "
          f"fail={','.join(args.fail) or 'none'})", flush=True)
    from conversation.lifecycle import sla_scale_from_env

    scale = sla_scale_from_env()
    print(f"Temporizadores de SLA (simulación local): reloj real; con el interruptor fast_clock de una sesión, "
          f"x{scale:g} (LOCAL_SLA_SCALE; 1 día = {86400 * scale:g} s)", flush=True)
    from conversation import llm_port

    g1_line = llm_port.describe()
    if "model" in args.fail:
        g1_line = "G1: modelo falso lento (--fail model): cada turno cae a reglas con model_timeout"
    print(g1_line + f" · LLM_TIMEOUT_SECONDS={llm_timeout_seconds():g}", flush=True)
    if generated:
        print(f"Consola del analista: {ANALYST_TOKEN_ENV} no estaba definido; token generado para esta "
              f"ejecución: {token}", flush=True)
    else:
        print(f"Consola del analista: {ANALYST_TOKEN_ENV} tomado del entorno o de .env", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
