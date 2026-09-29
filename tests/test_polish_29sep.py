"""Polish of known pending items (29 sep). SYNTHETIC data only; no AWS, no model.

- Every field key the provisional investigator writes in ``evidence_records`` (and the case
  evidence the console shows with the same component) has a label in the console's ES and PT
  dictionaries (``evfield.<key>``), so no raw ``threshold`` or ``window_from`` reaches the analyst.
- GET /health reports the demo clock factor (``demo_clock_scale``): the judge panel and the
  console's Historial say the real scale instead of a fixed "1 día = 1 minuto".
- The switch confirmation ``switch.fast_clock_on`` says the scale the server really applies.
- The SLA notices never say that a date already past "sigue siendo" the estimate.
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from conversation import contract as C
from conversation import promise
from conversation.lifecycle import DEFAULT_SLA_SCALE
from conversation.templates import TEMPLATES_DIR, format_date, format_duration, forbidden_hits, render
from handlers import api

from _support import Conv, FakeClock, call, make_app, reset_gateway, v2_event
from test_console_backend import andres_b, app_complaint_b, joao_b, lucia_b, martina_c, overcharge_b, sofia_b

REPO = Path(__file__).resolve().parents[1]
I18N = REPO / "frontend" / "src" / "i18n"
FRONT_TEST = REPO / "frontend" / "test" / "polish_29sep.test.tsx"
LONG_TTL = 60 * 24 * 60


@pytest.fixture(autouse=True)
def _clean():
    reset_gateway()
    api.set_app(None)
    yield
    reset_gateway()
    api.set_app(None)


@pytest.fixture
def clock():
    return FakeClock()


# ================================================================ 1. evidence field labels

def _evfield_keys(lang: str) -> set[str]:
    files = {"es": ("es.core.ts", "es.ts"), "pt": ("pt.core.ts", "pt.ts")}[lang]
    text = "\n".join((I18N / f).read_text(encoding="utf-8") for f in files)
    return set(re.findall(r"'evfield\.(\w+)':", text))


def _stub_field_keys(clock) -> set[str]:
    """Field keys of a real run of the stub over the demo money cases, plus the case evidence
    (product, app_error) and risk features the console renders with the same component."""
    keys: set[str] = set()
    for flow in (andres_b, joao_b, overcharge_b, sofia_b, lucia_b, app_complaint_b, martina_c):
        reset_gateway()
        app = make_app(clock=clock, investigation_delay=0)
        _, case_id = flow(app)
        report = app.stores.reviews.get_report(case_id) or {}
        for rec in (report.get("evidence_records") or {}).values():
            keys |= set(rec["fields"])
        case = app.stores.cases.get(case_id).case
        for part in (case.evidence.product, case.evidence.app_error):
            keys |= set(part or {})
        if case.risk_evidence is not None:
            keys |= set(case.risk_evidence.features)
    return keys


def test_every_evidence_field_the_stub_writes_has_a_label_in_es_and_pt(clock):
    keys = _stub_field_keys(clock)
    # The ones the review found raw on screen are among them.
    assert {"threshold", "source", "window_from", "window_to", "n_txns", "median_amount", "p90_amount",
            "top_categories", "usual_hours", "window_days", "card_id"} <= keys, sorted(keys)
    for lang in ("es", "pt"):
        missing = sorted(keys - _evfield_keys(lang))
        assert not missing, f"{lang}: evidence fields without a label: {missing}"


def test_the_front_end_test_covers_every_stub_field(clock):
    """frontend/test/polish_29sep.test.tsx renders STUB_FIELDS and checks no raw key is left."""
    text = FRONT_TEST.read_text(encoding="utf-8")
    block = re.search(r"const STUB_FIELDS = \{(.*?)\n\}", text, re.S)
    assert block
    listed = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    missing = sorted(_stub_field_keys(clock) - listed)
    assert not missing, f"STUB_FIELDS lacks {missing}"


# ================================================================ 3. demo clock scale

def test_health_reports_the_demo_clock_scale_of_the_app(clock):
    api.set_app(make_app(clock=clock, investigation_delay=0, sla_scale=0.00001))
    status, body, _ = call(v2_event("GET /health"))
    assert status == 200 and body["demo_clock_scale"] == 0.00001
    C.HealthResponse.model_validate(body)


def test_health_before_the_app_reads_the_environment(monkeypatch):
    monkeypatch.delenv("LIFECYCLE_BACKEND", raising=False)
    monkeypatch.delenv("LOCAL_SLA_SCALE", raising=False)
    assert call(v2_event("GET /health"))[1]["demo_clock_scale"] == pytest.approx(DEFAULT_SLA_SCALE)
    monkeypatch.setenv("LOCAL_SLA_SCALE", "0.001")
    assert call(v2_event("GET /health"))[1]["demo_clock_scale"] == 0.001
    # No local lifecycle (the cloud's Step Functions): there is no demo clock.
    monkeypatch.setenv("LIFECYCLE_BACKEND", "stepfunctions")
    assert call(v2_event("GET /health"))[1]["demo_clock_scale"] is None


def test_health_without_dependencies_reports_no_scale():
    code = (
        "import sys, json; sys.path.insert(0, 'src');"
        "sys.modules['pydantic'] = None; sys.modules['yaml'] = None;"
        "import handlers.api as a;"
        "r = a.handler({'routeKey': 'GET /health'}, None); print(json.loads(r['body'])['demo_clock_scale'])"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "None", out


@pytest.mark.parametrize("seconds, lang, text", [
    (60.0, "es", "1 minuto"), (720.0, "es", "12 minutos"), (0.864, "es", "0,9 segundos"),
    (12.96, "es", "13 segundos"), (7200.0, "es", "2 horas"),
    (60.0, "pt", "1 minuto"), (0.864, "pt", "0,9 segundo"), (10.368, "pt", "10,4 segundos"),
])
def test_format_duration(seconds, lang, text):
    assert format_duration(seconds, lang) == text


@pytest.mark.parametrize("scale, lang, day", [
    (None, "es", "1 día = 1 minuto"), (0.00001, "es", "1 día = 0,9 segundos"),
    (None, "pt", "1 dia = 1 minuto"), (0.00001, "pt", "1 dia = 0,9 segundo"),
])
def test_fast_clock_confirmation_says_the_real_scale(clock, scale, lang, day):
    app = make_app(clock=clock, investigation_delay=0, **({"sla_scale": scale} if scale else {}))
    conv = Conv(app, "joao" if lang == "pt" else "lucia")
    r = app.chat(conv.token, {"client_msg_id": uuid.uuid4().hex, "demo_switches": {"fast_clock": True}})
    assert day in r["reply_text"], r["reply_text"]
    other = "1 minuto" if scale else "0,9"
    assert other not in r["reply_text"]


def test_fast_clock_confirmation_without_a_scale_is_neutral(clock):
    app = make_app(clock=clock, investigation_delay=0)
    app.lifecycle.sla_scale = None  # a lifecycle that does not say its factor
    conv = Conv(app, "lucia")
    r = app.chat(conv.token, {"client_msg_id": uuid.uuid4().hex, "demo_switches": {"fast_clock": True}})
    assert r["reply_text"] == render("switch.fast_clock_on_neutral", "es")
    assert "=" not in r["reply_text"] and "minuto" not in r["reply_text"]


def test_fast_clock_templates_keep_their_placeholders():
    for lang in ("es", "pt"):
        text = (TEMPLATES_DIR / f"{lang}.yaml").read_text(encoding="utf-8")
        line = re.search(r"switch\.fast_clock_on:\s*\"([^\"]*)\"", text)
        assert line and "{day}" in line.group(1) and "1 minuto" not in line.group(1)
        for key in ("switch.fast_clock_on", "switch.fast_clock_on_neutral"):
            assert forbidden_hits(render(key, lang, "MX", day="1 minuto")) == []


# ================================================================ 4. notices with a date already past

APP_COMPLAINT = {
    "lucia": ("La app se cierra cada vez que intento pagar, ya van tres días", "es", "MX"),
    "sofia": ("La app se cierra cada vez que intento pagar, ya van tres días", "es", "CO"),
    "martina": ("La app se cierra cada vez que intento pagar, ya van tres días", "es", "AR"),
    "joao": ("O app fecha toda vez que tento pagar, já faz três dias", "pt", "AR"),
}


@pytest.fixture
def short_promise(monkeypatch):
    """A promise_times.yaml where the promised date (p90, 10 days) comes before the SLA (15 days)."""
    data = promise.load_times()
    patched = {**data, "default": {**data["default"], "p90_days": 10, "median_days": 8}}
    monkeypatch.setattr(promise, "load_times", lambda path=None: patched)
    return patched


def _open_b(app, key):
    conv = Conv(app, key)
    r = conv.say(APP_COMPLAINT[key][0])
    assert r["lane"] == "B" and r["case_card"]["status"] == "awaiting_analyst"
    return conv, r["case_card"]["case_id"]


@pytest.mark.parametrize("key", sorted(APP_COMPLAINT))
def test_escalation_notice_does_not_repeat_a_promised_date_already_past(clock, short_promise, key):
    app = make_app(clock=clock, investigation_delay=0, ttl_minutes=LONG_TTL)
    conv, case_id = _open_b(app, key)
    _, lang, country = APP_COMPLAINT[key]
    case = app.stores.cases.get(case_id).case
    assert case.promise.expected_date < case.clock.breach_at.date()  # the scenario
    clock.advance(days=15, minutes=1)
    notices = app.get_case(conv.token, case_id)["case"]["notices"]
    esc = next(n for n in notices if n["kind"] == "escalated")
    date = format_date(case.promise.expected_date, lang)
    assert date not in esc["text"]
    assert "sigue siendo" not in esc["text"] and "continua sendo" not in esc["text"]
    assert esc["text"] == render("sla_notice.escalated_date_passed", lang, country, case_id=case_id)
    assert forbidden_hits(esc["text"]) == []
    # The 80 % notice (day 12) also comes after the 10-day promise: same wording rule.
    s80 = next(n for n in notices if n["kind"] == "sla_80")
    assert date not in s80["text"] and s80["text"] == render("sla_notice.sla_80_date_passed", lang, country,
                                                              case_id=case_id)


def test_notices_keep_the_date_while_it_is_still_ahead(clock):
    app = make_app(clock=clock, investigation_delay=0, ttl_minutes=LONG_TTL)
    conv, case_id = _open_b(app, "lucia")
    case = app.stores.cases.get(case_id).case
    assert case.promise.expected_date >= case.clock.breach_at.date()  # default: p90 28 d > SLA 15 d
    clock.advance(days=15, minutes=1)
    notices = app.get_case(conv.token, case_id)["case"]["notices"]
    date = format_date(case.promise.expected_date, "es")
    assert [n["kind"] for n in notices] == ["sla_80", "escalated"]
    assert all(date in n["text"] and "sigue siendo" in n["text"] for n in notices)


def test_promised_date_equal_to_the_breach_day_is_not_past(clock, monkeypatch):
    data = promise.load_times()
    monkeypatch.setattr(promise, "load_times",
                        lambda path=None: {**data, "default": {**data["default"], "p90_days": 15}})
    app = make_app(clock=clock, investigation_delay=0, ttl_minutes=LONG_TTL)
    conv, case_id = _open_b(app, "lucia")
    case = app.stores.cases.get(case_id).case
    clock.advance(days=15, minutes=1)
    esc = app.get_case(conv.token, case_id)["case"]["notices"][-1]
    assert esc["kind"] == "escalated" and format_date(case.promise.expected_date, "es") in esc["text"]


def test_date_passed_templates_exist_in_every_register_and_promise_nothing():
    for lang in ("es", "pt"):
        for kind in ("sla_80", "escalated"):
            for country in ("MX", "CO", "AR"):
                text = render(f"sla_notice.{kind}_date_passed", lang, country, case_id="EV-X")
                assert "EV-X" in text and forbidden_hits(text) == []
                assert "{" not in text
    t = {c: render("sla_notice.escalated_date_passed", "es", c, case_id="EV-X") for c in ("MX", "CO", "AR")}
    assert "te escribiremos" in t["MX"] and "le escribiremos" in t["CO"] and "te vamos a escribir" in t["AR"]


def test_timedelta_sanity():
    # 15 days * 0.00001 = 12.96 s and 12 days = 10.368 s (docs/como_correr_local.md: ~13 s, 10,4 s).
    assert timedelta(days=15).total_seconds() * 0.00001 == pytest.approx(12.96)
    assert timedelta(days=12).total_seconds() * 0.00001 == pytest.approx(10.368)
