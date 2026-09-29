"""Latency and capacity test of the chat API (owner: arturo). Standard library only.

Runs complete conversations of the six demo customers (the judge-mode suggestions of
frontend/src/demo/customers.ts and the buttons the server issues, like a judge clicking),
N at a time, and measures every request:

- client latency: time around the HTTP request as the caller sees it (connect + send + wait +
  read). No keep-alive: urllib opens one connection per request.
- server latency: ``trace_summary.latency_ms`` of each chat turn (orchestrator time only; it
  leaves out HTTP, JSON parsing, the session lock and, in the cloud, API Gateway and Lambda).

Request types: GET /health (the floor of the HTTP stack: no session, no state), POST /session, button (template, no model), text (rules only), text (with G1:
the turn has a ``g1_extract`` step), case opening (the turn whose answer first carries a case card,
text or button), card (GET /cases/{id}) and, with an analyst token, the console (queue and
detail, read only; nothing is decided). p50/p95/p99/max per type, errors by HTTP status and
code, throughput. Percentiles: linear interpolation between closest ranks (numpy's default).

Each worker runs ``--per-worker`` conversations one after another, rotating through the six
customers, with no pause between turns (closed loop: the worst case for the server; a person
takes seconds to read and type; ``--think-ms`` adds a pause, ``--max-rps`` caps the total rate).
``--concurrency 1,5,10,20`` runs each level in turn;
``--repeat 3`` repeats each level and the report gives the MEDIAN over repetitions of each
statistic (errors are summed). One warm-up conversation per server is not counted.

Against a running server (local or the deployed site; the script adds ``/api``):

    python scripts/load_test.py http://127.0.0.1:8000 --concurrency 1,5 --repeat 3 \\
        --analyst-token <token> --llm-mode mock

``--llm-mode`` (none | mock | bedrock | any) is what the server is expected to run; the script
checks it against the ``model`` steps of the traces and warns on a mismatch (it cannot change a
remote server). With ``--spawn-local PORT`` the script starts ``scripts/local_api.py`` itself,
once per level and repetition (fresh memory each time), with ``--llm <mode>``,
``SESSION_RATE_PER_MINUTE=0`` (otherwise 20 workers hit the 60 sessions/min/IP limit),
``TRACE_DIR`` in a temporary folder and a random analyst token, and stops it afterwards. There
``--llm-mode none,mock`` compares both modes in one report:

    python scripts/load_test.py --spawn-local 8601 --llm-mode none,mock \\
        --concurrency 1,5,10,20 --repeat 3 --per-worker 12 --md-out <file.md>

Everything is SYNTHETIC. The session and analyst tokens are never printed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]
LOCAL_API = REPO / "scripts" / "local_api.py"
LLM_MODES = ("none", "mock", "bedrock", "any")
SPAWN_MODES = ("none", "mock")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")

# Request types, in report order.
KINDS: dict[str, str] = {
    "health": "GET /health (piso HTTP)",
    "session": "POST /session",
    "button": "botón (plantilla)",
    "text_rules": "texto (solo reglas)",
    "text_g1": "texto (con G1)",
    "case_open": "apertura de caso",
    "chat_all": "turno de chat (todos)",
    "card": "tarjeta GET /cases/{id}",
    "console_queue": "consola: cola",
    "console_detail": "consola: detalle",
}
CHAT_KINDS = ("button", "text_rules", "text_g1", "case_open")
G1_STEP = "g1_extract"  # trace step of the G1 extractor (conversation.g1.PROMPT_ID)

# Judge-mode suggestions of frontend/src/demo/customers.ts (tests/test_load_test.py checks the copy)
# and the buttons pressed, as in tests/test_integration_m1.py. Steps: ("say", text) or
# ("press", button kind, label substring or None).
SUGGESTIONS: dict[str, list[str]] = {
    "lucia": ["Hola, me salió un cobro como de 450 en el súper el 12 y no lo ubico"],
    "sofia": ["Buenas, me cobraron algo raro", "Fueron como 52 mil de un domicilio el 9",
              "Y de paso, quisiera pedir un préstamo"],
    "andres": ["Me cobraron dos veces lo mismo, con minutos de diferencia"],
    "joao": ["Oi, me cobraram uma tarifa que não bate com a tabela de vocês"],
    "martina": ["Me aparece un consumo rarísimo en la tarjeta, creo que me la clonaron",
                "Fueron 145 mil en ELECTRO MUNDO ONLINE el 15"],
    "carlos": ["Es la tercera vez que me quejo; si no lo arreglan voy al regulador"],
}
_S = SUGGESTIONS
SCRIPTS: dict[str, list[tuple]] = {
    "lucia": [("say", _S["lucia"][0]), ("press", "confirm", None), ("press", "deny", None)],
    "sofia": [("say", _S["sofia"][0]), ("say", _S["sofia"][1]), ("press", "choice", "RAPPI*RESTAURANTE"),
              ("press", "deny", None), ("say", _S["sofia"][2])],
    "andres": [("say", _S["andres"][0]), ("press", "confirm", None)],
    "joao": [("say", _S["joao"][0]), ("press", "confirm", None)],
    "martina": [("say", _S["martina"][0]), ("say", _S["martina"][1]), ("press", "confirm", None),
                ("press", "confirm", "7730")],
    "carlos": [("say", _S["carlos"][0])],
}
CUSTOMERS = tuple(SCRIPTS)


# ---------------------------------------------------------------- statistics

def percentile(values: Iterable[float], p: float) -> float | None:
    """p-th percentile (0..100) with linear interpolation between closest ranks; None if empty."""
    v = sorted(values)
    if not v:
        return None
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    k = (len(v) - 1) * p / 100
    lo = math.floor(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def summarize(values: Sequence[float]) -> dict[str, float | None]:
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95),
            "p99": percentile(values, 99), "max": max(values) if values else None}


def median_of(values: Iterable[float | None]) -> float | None:
    v = [x for x in values if x is not None]
    return statistics.median(v) if v else None


# ---------------------------------------------------------------- HTTP

@dataclass
class Sample:
    kind: str
    client_ms: float
    server_ms: float | None
    status: int
    code: str | None  # None when 200


def api_base(base: str) -> str:
    base = base.rstrip("/")
    return (base[: -len("/api")] if base.endswith("/api") else base) + "/api"


def error_code(status: int, data: Any) -> str:
    if isinstance(data, dict):
        if isinstance(data.get("error"), dict) and data["error"].get("code"):
            return str(data["error"]["code"])
        if data.get("message"):
            return str(data["message"])
    return f"http_{status}"


def exc_code(e: BaseException) -> str:
    """exc:<type>, plus the reason of a URLError and its OS error number (10061 = refused on Windows)."""
    reason = getattr(e, "reason", None)
    if isinstance(e, urllib.error.URLError) and reason is not None:
        num = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
        return f"exc:URLError:{type(reason).__name__}" + (f":{num}" if num else "")
    return f"exc:{type(e).__name__}"


class Pacer:
    """Caps the requests per second of all workers together (0 = no cap) and adds a pause after
    each chat turn. The deployed API is throttled at 20 req/s with bursts of 50
    (infra/stacks/api_stack.py of the team repo): without a cap the test would measure 429s."""

    def __init__(self, max_rps: float = 0.0, think_ms: float = 0.0):
        self.interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self.think_s = think_ms / 1000
        self._next = 0.0
        self._lock = threading.Lock()

    def before_request(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.perf_counter()  # time.monotonic ticks every ~16 ms on Windows
            slot = max(now, self._next)
            self._next = slot + self.interval
        if slot > now:
            time.sleep(slot - now)

    def think(self) -> None:
        if self.think_s:
            time.sleep(self.think_s)


class Client:
    def __init__(self, base: str, timeout: float, analyst_token: str | None = None, pacer: Pacer | None = None):
        self.base = api_base(base)
        self.timeout = timeout
        self.analyst_token = analyst_token
        self.pacer = pacer or Pacer()

    def call(self, method: str, path: str, body: Any = None, token: str | None = None
             ) -> tuple[int, Any, float]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("accept", "application/json")
        if data is not None:
            req.add_header("content-type", "application/json")
        if token:
            req.add_header("authorization", f"Bearer {token}")
        self.pacer.before_request()  # waiting for a slot is not part of the latency
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                text = res.read().decode("utf-8")
                status = res.status
        except urllib.error.HTTPError as e:
            text, status = e.read().decode("utf-8", "replace"), e.code
        except Exception as e:  # timeout, refused, reset: status 0
            return 0, {"error": {"code": exc_code(e)}}, (time.perf_counter() - started) * 1000
        ms = (time.perf_counter() - started) * 1000
        try:
            return status, json.loads(text), ms
        except ValueError:
            return status, text, ms


class Abort(Exception):
    pass


class Conversation:
    """One demo conversation; appends one Sample per request."""

    def __init__(self, client: Client, key: str, samples: list[Sample], models: set[str]):
        self.client, self.key, self.samples, self.models = client, key, samples, models
        self.token: str | None = None
        self.last: dict | None = None
        self.case: dict | None = None

    def _record(self, kind: str, status: int, data: Any, ms: float, server_ms: float | None = None) -> None:
        self.samples.append(Sample(kind, ms, server_ms, status, None if status == 200 else error_code(status, data)))
        if status != 200:
            raise Abort(f"{kind}: {status} {error_code(status, data)}")

    def run(self) -> None:
        status, data, ms = self.client.call("GET", "/health")
        self._record("health", status, data, ms)
        status, data, ms = self.client.call("POST", "/session", {"demo_key": self.key, "channel": "web"})
        self._record("session", status, data, ms)
        self.token, self.last = data["session_token"], data["welcome"]
        for step in SCRIPTS[self.key]:
            if step[0] == "say":
                self._turn({"message": step[1]}, typed=True)
            else:
                self._turn({"button_id": self._button(step[1], step[2])}, typed=False)
        if self.case:
            self._after_case()

    def _button(self, kind: str, label: str | None) -> str:
        found = [b for b in (self.last or {}).get("buttons", [])
                 if b["kind"] == kind and (label is None or label in b["label"])]
        if not found:
            self.samples.append(Sample("button", 0.0, None, -1, f"no_button:{kind}"))
            raise Abort(f"no button {kind} {label!r} for {self.key}")
        return found[0]["id"]

    def _turn(self, fields: dict, typed: bool) -> None:
        body = {"client_msg_id": str(uuid.uuid4()), **fields}
        status, data, ms = self.client.call("POST", "/chat", body, self.token)
        kind = "text_rules" if typed else "button"
        server_ms = None
        if status == 200 and isinstance(data, dict):
            ts = data.get("trace_summary") or {}
            server_ms = ts.get("latency_ms")
            # The ML classifier and ranker are also ``model`` steps: G1 is the one named g1_extract.
            if any(s.get("name") == G1_STEP for s in ts.get("steps", [])):
                kind = "text_g1" if typed else kind
            if ts.get("model_id"):
                self.models.add(ts["model_id"])
            if data.get("case_card") and not self.case:
                kind, self.case = "case_open", data["case_card"]
            self.last = data
        elif typed:
            kind = "text"  # no answer: we cannot tell whether G1 ran
        self._record(kind, status, data, ms, server_ms)
        self.client.pacer.think()

    def _after_case(self) -> None:
        case_id, lane = self.case["case_id"], self.case.get("lane") or "B"
        status, data, ms = self.client.call("GET", f"/cases/{case_id}", token=self.token)
        self._record("card", status, data, ms)
        tok = self.client.analyst_token
        if not tok:
            return
        status, data, ms = self.client.call("GET", f"/analyst/cases?lane={lane}&limit=50", token=tok)
        self._record("console_queue", status, data, ms)
        status, data, ms = self.client.call("GET", f"/analyst/cases/{case_id}", token=tok)
        self._record("console_detail", status, data, ms)


# ---------------------------------------------------------------- one run

@dataclass
class RunResult:
    mode: str
    level: int
    conversations: int
    conversations_ok: int
    wall_s: float
    samples: list[Sample]
    models: set[str] = field(default_factory=set)
    aborts: Counter = field(default_factory=Counter)

    def errors(self) -> Counter:
        c: Counter = Counter()
        for s in self.samples:
            if s.status != 200:  # 0 = no HTTP answer (refused, reset, timeout): the code says which
                c[s.code if s.status == 0 else f"{s.status} {s.code}"] += 1
        return c

    def by_kind(self) -> dict[str, dict[str, dict]]:
        out: dict[str, dict[str, dict]] = {}
        for kind in KINDS:
            chosen = [s for s in self.samples if s.status == 200 and
                      (s.kind in CHAT_KINDS if kind == "chat_all" else s.kind == kind)]
            if not chosen:
                continue
            server = [s.server_ms for s in chosen if s.server_ms is not None]
            out[kind] = {"client": summarize([s.client_ms for s in chosen]), "server": summarize(server)}
        return out


def run_level(client: Client, mode: str, level: int, per_worker: int, conversations: int | None = None
              ) -> RunResult:
    total = conversations if conversations is not None else level * per_worker
    plan: list[list[str]] = [[] for _ in range(level)]
    for i in range(total):  # worker w starts with customer w, so every level mixes the six customers
        w = i % level
        plan[w].append(CUSTOMERS[(w + len(plan[w])) % len(CUSTOMERS)])
    lock = threading.Lock()
    samples: list[Sample] = []
    models: set[str] = set()
    aborts: Counter = Counter()
    ok = [0]
    barrier = threading.Barrier(level)

    def worker(keys: list[str]) -> None:
        local: list[Sample] = []
        seen: set[str] = set()
        done = 0
        bad: Counter = Counter()
        barrier.wait()
        for key in keys:
            try:
                Conversation(client, key, local, seen).run()
                done += 1
            except Abort as e:
                bad[str(e)] += 1
            except Exception as e:  # malformed answer: count it, keep going
                bad[f"{key}: {type(e).__name__}"] += 1
        with lock:
            samples.extend(local)
            models.update(seen)
            aborts.update(bad)
            ok[0] += done

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=level) as pool:
        list(pool.map(worker, plan))
    wall = time.perf_counter() - started
    return RunResult(mode, level, total, ok[0], wall, samples, models, aborts)


def mode_warning(mode: str, models: set[str]) -> str | None:
    if mode == "none" and models:
        return f"se esperaba --llm-mode none y las trazas traen modelo {sorted(models)}"
    if mode in ("mock", "bedrock") and not models:
        return f"se esperaba --llm-mode {mode} y ninguna traza trae model_id"
    return None


# ---------------------------------------------------------------- local server

def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """True when nothing is listening there. A connect test, not a bind: after a run the port keeps
    TIME_WAIT sockets that make a plain bind fail on Windows, while local_api.py (SO_REUSEADDR) can
    listen again."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) != 0


class LocalServer:
    """scripts/local_api.py in a child process, for one level and repetition."""

    def __init__(self, port: int, mode: str, extra: Sequence[str] = ()):
        self.port, self.mode, self.extra = port, mode, list(extra)
        self.token = secrets.token_urlsafe(32)
        self.proc: subprocess.Popen | None = None
        self._tmp = tempfile.TemporaryDirectory(prefix="load_test_", ignore_cleanup_errors=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "LocalServer":
        if not port_is_free(self.port):
            raise SystemExit(f"el puerto {self.port} está ocupado; elige otro con --spawn-local")
        env = dict(os.environ, SESSION_RATE_PER_MINUTE="0", TRACE_DIR=str(Path(self._tmp.name) / "traces"),
                   LOCAL_ANALYST_TOKEN=self.token, PYTHONUNBUFFERED="1")
        self._log = open(Path(self._tmp.name) / "server.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(LOCAL_API), "--port", str(self.port), "--host", "127.0.0.1",
             "--llm", self.mode, *self.extra],
            cwd=str(REPO), env=env, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        client = Client(self.url, timeout=2)
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            status, _, _ = client.call("GET", "/health")
            if status == 200:
                return self
            time.sleep(0.2)
        self.__exit__(None, None, None)
        raise SystemExit(f"local_api.py no respondió /health en el puerto {self.port}")

    def __exit__(self, *exc: Any) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        deadline = time.monotonic() + 10  # the next repetition reuses the port
        while not port_is_free(self.port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if getattr(self, "_log", None):
            self._log.close()
        self._tmp.cleanup()


def warm_up(client: Client, n: int) -> None:
    for i in range(n):
        try:
            Conversation(client, CUSTOMERS[i % len(CUSTOMERS)], [], set()).run()
        except Abort:
            pass


# ---------------------------------------------------------------- aggregation and report

def aggregate(runs: list[RunResult]) -> dict[str, Any]:
    """Median over repetitions of every statistic of one (mode, level)."""
    kinds: dict[str, dict[str, dict]] = {}
    per_run = [r.by_kind() for r in runs]
    for kind in KINDS:
        present = [k[kind] for k in per_run if kind in k]
        if not present:
            continue
        kinds[kind] = {side: {stat: median_of(p[side][stat] for p in present)
                              for stat in ("n", "p50", "p95", "p99", "max")}
                       for side in ("client", "server")}
    errors: Counter = Counter()
    aborts: Counter = Counter()
    models: set[str] = set()
    for r in runs:
        errors.update(r.errors())
        aborts.update(r.aborts)
        models |= r.models
    chat_p95 = [k["chat_all"]["client"]["p95"] for k in per_run if "chat_all" in k]
    return {
        "mode": runs[0].mode, "level": runs[0].level, "repeat": len(runs),
        "conversations": median_of(r.conversations for r in runs),
        "conversations_ok": median_of(r.conversations_ok for r in runs),
        "conversations_sum": sum(r.conversations for r in runs),
        "conversations_ok_sum": sum(r.conversations_ok for r in runs),
        "requests": median_of(len(r.samples) for r in runs),
        "wall_s": median_of(r.wall_s for r in runs),
        "req_per_s": median_of(len(r.samples) / r.wall_s for r in runs if r.wall_s > 0),
        "conv_per_s": median_of(r.conversations_ok / r.wall_s for r in runs if r.wall_s > 0),
        "chat_p95_range": (min(chat_p95), max(chat_p95)) if chat_p95 else None,
        "kinds": kinds, "errors": dict(errors), "aborts": dict(aborts), "models": sorted(models),
    }


def _f(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _errs(d: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(d.items())) or "0"


def markdown(aggs: list[dict], meta: dict) -> str:
    out = [f"Base: `{meta['base']}` · repeticiones por nivel: {meta['repeat']} (mediana entre repeticiones) · "
           f"conversaciones por trabajador: {meta['per_worker']} · consola: {meta['console']} · "
           f"calentamiento: {meta['warmup']} conversación(es) sin contar · ritmo: {meta.get('pacing', 'sin pausa')} · "
           f"{meta['when']}", ""]
    out += ["| Modo LLM | Concurrencia | Conversaciones completas (suma de rep.) | Peticiones | Duración (s) | Peticiones/s | "
            "Conversaciones/s | Chat p50 (ms) | Chat p95 (ms) | Chat p95 min–máx entre rep. | Chat p99 (ms) | "
            "Chat máx (ms) | Errores (suma) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for a in aggs:
        c = a["kinds"].get("chat_all", {}).get("client", {})
        rng = a["chat_p95_range"]
        out.append(f"| {a['mode']} | {a['level']} | {a['conversations_ok_sum']}/{a['conversations_sum']} | "
                   f"{_f(a['requests'], 0)} | {_f(a['wall_s'], 2)} | {_f(a['req_per_s'])} | {_f(a['conv_per_s'], 2)} | "
                   f"{_f(c.get('p50'))} | {_f(c.get('p95'))} | "
                   f"{'—' if not rng else f'{rng[0]:.1f}–{rng[1]:.1f}'} | {_f(c.get('p99'))} | {_f(c.get('max'))} | "
                   f"{_errs(a['errors'])} |")
    out += ["", "Por tipo de petición (ms; cliente = lo que ve quien llama; servidor = `trace_summary.latency_ms`, "
            "solo turnos de chat):", "",
            "| Modo LLM | Conc. | Tipo | n por rep. | Cliente p50 | Cliente p95 | Cliente p99 | Cliente máx | "
            "Servidor p50 | Servidor p95 | Servidor p99 | Servidor máx |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for a in aggs:
        for kind, st in a["kinds"].items():
            c, s = st["client"], st["server"]
            out.append(f"| {a['mode']} | {a['level']} | {KINDS[kind]} | {_f(c['n'], 0)} | {_f(c['p50'])} | "
                       f"{_f(c['p95'])} | {_f(c['p99'])} | {_f(c['max'])} | {_f(s['p50'])} | {_f(s['p95'])} | "
                       f"{_f(s['p99'])} | {_f(s['max'])} |")
    notes = [f"- {a['mode']}/{a['level']}: conversaciones cortadas {_errs(a['aborts'])}"
             for a in aggs if a["aborts"]]
    models = sorted({m for a in aggs for m in a["models"]})
    out += ["", f"Modelos vistos en las trazas: {', '.join(models) if models else 'ninguno'}."]
    out += notes
    out += [f"- aviso: {w}" for w in meta.get("warnings", [])]
    return "\n".join(out) + "\n"


def text_report(aggs: list[dict]) -> str:
    lines = []
    for a in aggs:
        c = a["kinds"].get("chat_all", {}).get("client", {})
        lines.append(f"[{a['mode']} x{a['level']}] {a['conversations_ok_sum']}/{a['conversations_sum']} "
                     f"conversaciones completas (suma de rep.) · {_f(a['requests'], 0)} peticiones en {_f(a['wall_s'], 2)} s · "
                     f"{_f(a['req_per_s'])} pet/s · chat p50 {_f(c.get('p50'))} p95 {_f(c.get('p95'))} "
                     f"p99 {_f(c.get('p99'))} máx {_f(c.get('max'))} ms · errores {_errs(a['errors'])}")
        for kind, st in a["kinds"].items():
            cl, sv = st["client"], st["server"]
            lines.append(f"    {KINDS[kind]:<26} n={_f(cl['n'], 0):>4}  cliente p50 {_f(cl['p50']):>7} "
                         f"p95 {_f(cl['p95']):>7} p99 {_f(cl['p99']):>7} máx {_f(cl['max']):>7}  |  servidor "
                         f"p50 {_f(sv['p50']):>6} p95 {_f(sv['p95']):>6} máx {_f(sv['max']):>6}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _levels(text: str) -> list[int]:
    try:
        levels = [int(x) for x in text.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"concurrencia inválida: {text!r}")
    if not levels or any(n < 1 or n > 200 for n in levels):
        raise argparse.ArgumentTypeError("cada nivel de concurrencia va de 1 a 200")
    return levels


def _modes(text: str) -> list[str]:
    modes = [x.strip() for x in text.split(",") if x.strip()]
    bad = [m for m in modes if m not in LLM_MODES]
    if not modes or bad:
        raise argparse.ArgumentTypeError(f"--llm-mode admite {', '.join(LLM_MODES)} (o una lista con coma)")
    return modes


def _positive(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"no es un entero: {text!r}")
    if n < 1:
        raise argparse.ArgumentTypeError("debe ser al menos 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Latencia y capacidad del chat con los clientes demo (sintético).")
    p.add_argument("base_url", nargs="?", help="raíz del sitio o de la API, p. ej. http://127.0.0.1:8000 "
                                               "(se agrega /api); se omite con --spawn-local")
    p.add_argument("--concurrency", type=_levels, default=[1], help="niveles separados por coma, p. ej. 1,5,10,20")
    p.add_argument("--per-worker", type=_positive, default=6, help="conversaciones seguidas por trabajador (6)")
    p.add_argument("--conversations", type=_positive, default=None,
                   help="total de conversaciones por nivel (por defecto concurrencia × --per-worker)")
    p.add_argument("--repeat", type=_positive, default=1, help="repeticiones por nivel; se reporta la mediana")
    p.add_argument("--llm-mode", type=_modes, default=["any"],
                   help="none | mock | bedrock | any: lo que se espera del servidor; con --spawn-local, "
                        "none y/o mock (lista con coma) y el script levanta el servidor con --llm")
    p.add_argument("--analyst-token", default=None,
                   help="token de la consola (o LOCAL_ANALYST_TOKEN); sin él no se mide la consola")
    p.add_argument("--no-console", action="store_true", help="no medir la consola aunque haya token")
    p.add_argument("--spawn-local", type=int, default=None, metavar="PORT",
                   help="levanta scripts/local_api.py en este puerto, uno nuevo por nivel y repetición")
    p.add_argument("--server-arg", action="append", default=[],
                   help="argumento extra para local_api.py con --spawn-local (repetible)")
    p.add_argument("--warmup", type=int, default=1, help="conversaciones de calentamiento sin contar (1)")
    p.add_argument("--max-rps", type=float, default=0.0,
                   help="tope de peticiones por segundo entre todos los trabajadores (0 = sin tope); "
                        "contra AWS, debajo de 20 (throttling de la API)")
    p.add_argument("--think-ms", type=float, default=0.0,
                   help="pausa después de cada turno de chat, como alguien que lee (0)")
    p.add_argument("--timeout", type=float, default=35.0, help="timeout por petición en segundos (35)")
    p.add_argument("--format", choices=("text", "md", "both"), default="both")
    p.add_argument("--md-out", default=None, help="también escribe la tabla markdown en este archivo")
    p.add_argument("--json-out", default=None, help="resultados agregados en JSON (fuera del repo)")
    return p


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = build_parser()
    a = p.parse_args(argv)
    if a.spawn_local is None and not a.base_url:
        p.error("falta base_url (o --spawn-local PORT)")
    if a.spawn_local is not None:
        if a.base_url:
            p.error("--spawn-local levanta su propio servidor: no se pasa base_url")
        if not 1024 <= a.spawn_local <= 65535:
            p.error("--spawn-local necesita un puerto entre 1024 y 65535")
        if any(m not in SPAWN_MODES for m in a.llm_mode):
            p.error("con --spawn-local, --llm-mode admite none y/o mock")
    elif len(a.llm_mode) > 1:
        p.error("contra un servidor ya levantado, --llm-mode es uno solo (el script no puede cambiarlo)")
    if a.server_arg and a.spawn_local is None:
        p.error("--server-arg solo tiene sentido con --spawn-local")
    if a.warmup < 0:
        p.error("--warmup no puede ser negativo")
    if a.max_rps < 0 or a.think_ms < 0:
        p.error("--max-rps y --think-ms no pueden ser negativos")
    if a.analyst_token is None and not a.no_console and a.spawn_local is None:
        a.analyst_token = os.environ.get("LOCAL_ANALYST_TOKEN") or None
    return a


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = parse_args(argv)
    aggs: list[dict] = []
    warnings: list[str] = []
    when = time.strftime("%Y-%m-%d %H:%M")
    pacer = Pacer(a.max_rps, a.think_ms)
    for mode in a.llm_mode:
        for level in a.concurrency:
            runs: list[RunResult] = []
            for rep in range(a.repeat):
                if a.spawn_local is not None:
                    with LocalServer(a.spawn_local, mode, a.server_arg) as srv:
                        client = Client(srv.url, a.timeout, None if a.no_console else srv.token, pacer)
                        warm_up(client, a.warmup)
                        runs.append(run_level(client, mode, level, a.per_worker, a.conversations))
                else:
                    client = Client(a.base_url, a.timeout, None if a.no_console else a.analyst_token, pacer)
                    if rep == 0 and not aggs:
                        warm_up(client, a.warmup)
                    runs.append(run_level(client, mode, level, a.per_worker, a.conversations))
                r = runs[-1]
                print(f"  {mode} x{level} rep {rep + 1}/{a.repeat}: {r.conversations_ok}/{r.conversations} "
                      f"conversaciones, {len(r.samples)} peticiones, {r.wall_s:.2f} s", file=sys.stderr)
                w = mode_warning(mode, r.models)
                if w and w not in warnings:
                    warnings.append(w)
            aggs.append(aggregate(runs))
    meta = {"base": a.base_url or f"local_api.py en 127.0.0.1:{a.spawn_local} (uno nuevo por nivel y repetición)",
            "repeat": a.repeat, "per_worker": a.conversations and "—" or a.per_worker,
            "console": "sí" if (a.spawn_local is not None and not a.no_console) or
                               (a.analyst_token and not a.no_console) else "no",
            "warmup": a.warmup, "when": when, "warnings": warnings,
            "pacing": f"tope {a.max_rps:g} pet/s, pausa {a.think_ms:g} ms" if (a.max_rps or a.think_ms) else "sin pausa",
            "machine": f"{platform.system()} {platform.release()} · Python {platform.python_version()} · "
                       f"{os.cpu_count()} CPU lógicas"}
    md = markdown(aggs, meta)
    if a.format in ("text", "both"):
        print(text_report(aggs))
    if a.format in ("md", "both"):
        print()
        print(md)
    for w in warnings:
        print(f"AVISO: {w}", file=sys.stderr)
    if a.md_out:
        Path(a.md_out).write_text(md, encoding="utf-8")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"meta": meta, "results": aggs}, ensure_ascii=False, indent=1,
                                               default=list), encoding="utf-8")
    bad = sum(sum(x["errors"].values()) for x in aggs)
    return 1 if bad or warnings else 0


if __name__ == "__main__":
    sys.exit(main())
