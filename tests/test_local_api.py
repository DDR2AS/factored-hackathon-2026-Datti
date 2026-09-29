"""scripts/local_api.py: route copy in sync with infra/stacks/api_stack.py, CloudFront prefix
rule, v2 events, and a real server on a free port. SYNTHETIC data only."""

import importlib.util
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("local_api", REPO / "scripts" / "local_api.py")
local_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_api)

# The stack lives in the team repo; in the team repo itself it is infra/stacks/api_stack.py.
STACK_CANDIDATES = [
    REPO / "infra" / "stacks" / "api_stack.py",
    REPO.parent / "factored-hackathon-2026-Datti" / "infra" / "stacks" / "api_stack.py",
]


def routes_in_stack(name: str) -> list[tuple[str, str]]:
    path = next((p for p in STACK_CANDIDATES if p.exists()), None)
    if path is None:
        pytest.skip("infra/stacks/api_stack.py not found (team repo not next to this one)")
    text = path.read_text(encoding="utf-8")
    block = re.search(rf"{name}\s*=\s*\[(.*?)\]", text, re.S).group(1)
    return re.findall(r'\(\s*"([^"]+)"\s*,\s*apigwv2\.HttpMethod\.(\w+)\s*\)', block)


@pytest.mark.parametrize("name", ["PUBLIC_ROUTES", "ANALYST_ROUTES"])
def test_route_copy_matches_the_cdk_stack(name):
    assert routes_in_stack(name) == getattr(local_api, name)


def test_prefix_is_stripped_like_the_cloudfront_function():
    assert local_api.strip_api_prefix("/api/health") == "/health"
    assert local_api.strip_api_prefix("/api") == "/"
    assert local_api.strip_api_prefix("/apiary") == "ary"  # same (literal) regex as CloudFront


def test_route_resolution():
    assert local_api.resolve_route("GET", "/cases/EV-ABCD2345") == ("GET /cases/{case_id}", {"case_id": "EV-ABCD2345"})
    assert local_api.resolve_route("POST", "/chat") == ("POST /chat", {})
    assert local_api.resolve_route("GET", "/chat") is None
    assert local_api.resolve_route("GET", "/cases/a/b") is None
    assert local_api.resolve_route("POST", "/analyst/cases/EV-1/decision")[0] == "POST /analyst/cases/{case_id}/decision"


def test_v2_event_shape():
    e = local_api.build_event("POST", "/chat", "", {"Authorization": "Bearer t", "Content-Type": "application/json"},
                              b'{"a":1}', "POST /chat", {})
    assert e["version"] == "2.0" and e["routeKey"] == "POST /chat" and e["body"] == '{"a":1}'
    assert e["headers"]["authorization"] == "Bearer t" and e["requestContext"]["http"]["method"] == "POST"
    assert e["isBase64Encoded"] is False and "pathParameters" not in e


def test_env_file_parser(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# comment\nSTAGE=local\nexport LLM_PROVIDER="mock"\nSESSION_TTL_MINUTES=15 # minutes\nALREADY=new\nbad line\n',
                 encoding="utf-8")
    env = {"ALREADY": "old"}
    loaded = local_api.load_env_file(f, env)
    assert env == {"ALREADY": "old", "STAGE": "local", "LLM_PROVIDER": "mock", "SESSION_TTL_MINUTES": "15"}
    assert "ALREADY" not in loaded


@pytest.fixture
def server(tmp_path):
    from _support import make_app, reset_gateway
    from handlers import api

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>EV</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "build.txt").write_text("sha test\n", encoding="utf-8")
    reset_gateway()
    api.set_app(make_app())
    srv = local_api.make_server("127.0.0.1", 0, dist)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    api.set_app(None)
    reset_gateway()


def fetch(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def test_server_routes_api_and_static(server):
    status, text, headers = fetch(server + "/api/health")
    assert status == 200 and json.loads(text)["status"] == "ok"
    status, text, _ = fetch(server + "/api/session", "POST", {"demo_key": "lucia", "channel": "web"})
    token = json.loads(text)["session_token"]
    status, text, _ = fetch(server + "/api/chat", "POST", {"client_msg_id": "m1", "message": "hola"},
                            {"authorization": f"Bearer {token}"})
    assert status == 200 and json.loads(text)["turn"] == 1
    status, text, _ = fetch(server + "/api/cases/EV-ZZZZZZZZ", headers={"x-ev-session": token})
    assert status == 403
    assert fetch(server + "/api/nope")[:2] == (404, '{"message":"Not Found"}')
    assert fetch(server + "/api/chat")[0] == 404  # wrong method: API Gateway has no such route
    status, text, headers = fetch(server + "/")
    assert status == 200 and "<title>EV</title>" in text and headers.get("cache-control") == "no-cache"
    assert fetch(server + "/chat")[1] == text  # SPA fallback for paths without extension
    assert fetch(server + "/assets/app.js")[0] == 200
    assert fetch(server + "/assets/missing.js")[0] == 404
    import http.client

    host, port = server.rsplit("/", 1)[1].split(":")
    for raw in ("/%2e%2e/%2e%2e/README.md", "/..%2f..%2fREADME.md", "/assets/%2e%2e/%2e%2e/secret.txt"):
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.request("GET", raw)
        res = conn.getresponse()
        assert res.status == 404, raw  # never a file outside dist
        conn.close()
    assert "sha test" in fetch(server + "/build.txt")[1]


def test_smoke_script_accepts_the_site_root_or_the_api_base():
    spec = importlib.util.spec_from_file_location("smoke_api", REPO / "scripts" / "smoke_api.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    for base in ("http://127.0.0.1:8000", "http://127.0.0.1:8000/", "http://127.0.0.1:8000/api",
                 "http://127.0.0.1:8000/api/"):
        assert smoke.Client(base).base == "http://127.0.0.1:8000"
    assert smoke.base_root("https://d1.cloudfront.net/api") == "https://d1.cloudfront.net"


def test_vite_dev_proxy_keeps_the_api_prefix_for_local_api():
    """local_api.py strips /api itself (like CloudFront); a rewrite in the Vite proxy would send
    /api/health as /health, which local_api serves as the SPA's index.html."""
    config = (REPO / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    proxy = config.split("proxy:", 1)[1]
    assert "'/api'" in proxy and "rewrite" not in proxy
    assert local_api.strip_api_prefix("/api/health") == "/health"


# ---------------------------------------------------------------- review fixes (SEC-01, SEC-03, F07)

@pytest.mark.parametrize("raw, expected", [
    ("/%5C%5C192.0.2.1%5Cshare%5Cx.png", None),  # UNC path: would open SMB to that host
    ("/C:%5CWindows%5Cwin.ini", None),            # absolute Windows path
    ("/C:/Windows/win.ini", None),
    ("/a%00b.js", None),                          # NUL byte
    ("/..%2fREADME.md", None),
    ("/assets/%2e%2e/%2e%2e/secret.txt", None),
    ("/assets/app.js", "assets/app.js"),
    ("/", ""),
    ("/chat", "chat"),
])
def test_static_paths_are_checked_before_touching_the_file_system(raw, expected):
    assert local_api.safe_relative_path(raw) == expected


def test_unc_and_drive_paths_are_an_immediate_404_without_resolving(server, monkeypatch):
    import http.client
    import time
    from pathlib import Path as _Path

    resolved = []
    real_resolve = _Path.resolve

    def spy(self, *a, **k):
        resolved.append(str(self))
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(_Path, "resolve", spy)
    host, port = server.rsplit("/", 1)[1].split(":")
    for raw in ("/%5C%5C192.0.2.1%5Cshare%5Cx.png", "/C:%5CWindows%5Cwin.ini"):
        resolved.clear()
        conn = http.client.HTTPConnection(host, int(port), timeout=5)
        started = time.perf_counter()
        conn.request("GET", raw)
        res = conn.getresponse()
        res.read()
        conn.close()
        assert res.status == 404 and time.perf_counter() - started < 2, raw
        assert not any("192.0.2.1" in p or "Windows" in p for p in resolved), resolved


def _raw_post(server, content_length: str, body: bytes = b"{}") -> tuple[int, bytes, float]:
    import socket
    import time

    host, port = server.rsplit("/", 1)[1].split(":")
    s = socket.create_connection((host, int(port)), timeout=5)
    started = time.perf_counter()
    s.sendall(("POST /api/chat HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
               f"Content-Length: {content_length}\r\n\r\n").encode("ascii") + body)
    data = b""
    try:
        while b"\r\n\r\n" not in data or len(data) < data.find(b"\r\n\r\n") + 4 + 10:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    status = int(data.split(b" ", 2)[1]) if data else 0
    return status, data, time.perf_counter() - started


@pytest.mark.parametrize("length, status", [("500000000", 413), (str(16 * 1024 + 1), 413), ("-1", 400), ("abc", 400)])
def test_bad_or_huge_content_length_is_answered_without_reading_the_body(server, length, status):
    got, data, elapsed = _raw_post(server, length)
    assert got == status and elapsed < 3, data[:200]


def test_fail_model_flag_plugs_a_model_slower_than_the_timeout():
    parser_choices = re.search(r'"--fail", choices=\[([^\]]*)\]', (REPO / "scripts" / "local_api.py").read_text(encoding="utf-8"))
    assert "'model'" in parser_choices.group(1).replace('"', "'")
    fake = local_api.SlowModelClient(delay=0.01)
    out = fake.complete("g1_extract", {"text": "x"})
    assert out["output"] == {} and out["model_id"] == "fake-slow-model"
