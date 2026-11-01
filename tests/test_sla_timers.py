"""SLA timers of the local lane B lifecycle (plan v2 sections 6 and 12) and the judge-mode demo
clock ``fast_clock``. LOCAL SIMULATION of Andrés's EventBridge Scheduler; the clock is
injected, so every firing is deterministic. SYNTHETIC data only; no AWS, no model.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from conversation import contract as C
from conversation.app import poll_after_ms
from conversation.case import CaseStatus
from conversation.lifecycle import (
    BREACH_NOTE, DEFAULT_SLA_SCALE, SENIOR_QUEUE, LocalLifecycle, make_lifecycle, sla_scale_from_env,
)
from conversation.templates import format_date, forbidden_hits, render

from _support import Conv, FakeClock, make_app, reset_gateway

CLAIMS = {"sub": "analista-local", "email": "analista@demo.local"}
APP_COMPLAINT = {
    "lucia": ("La app se cierra cada vez que intento pagar, ya van tres días", "es", "MX"),
    "sofia": ("La app se cierra cada vez que intento pagar, ya van tres días", "es", "CO"),
    "martina": ("La app se cierra cada vez que intento pagar, ya van tres días", "es", "AR"),
    "joao": ("O app fecha toda vez que tento pagar, já faz três dias", "pt", "AR"),
}
LONG_TTL = 60 * 24 * 60  # minutes: the customer session outlives a 15-day SLA in these tests


@pytest.fixture(autouse=True)
def _clean_gateway():
    reset_gateway()
    yield
    reset_gateway()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app(clock):
    return make_app(clock=clock, investigation_delay=0, ttl_minutes=LONG_TTL)


def open_b(app, key="lucia", fast_clock=False):
    """A lane B complaint without a money investigation (awaiting_analyst at once)."""
    conv = Conv(app, key)
    if fast_clock:
        app.chat(conv.token, {"client_msg_id": uuid.uuid4().hex, "demo_switches": {"fast_clock": True}})
    r = conv.say(APP_COMPLAINT[key][0])
    assert r["lane"] == "B" and r["case_card"]["status"] == "awaiting_analyst", r["trace_summary"]["rule_id"]
    return conv, r["case_card"]["case_id"]


def stored(app, case_id):
    return app.stores.cases.get(case_id).case


def queue_item(app, case_id):
    items = app.analyst.list_cases(CLAIMS, {"limit": 50})["items"]
    return next(i for i in items if i["case_id"] == case_id)


def detail(app, case_id):
    return app.analyst.get_detail(CLAIMS, case_id)


def card(app, conv, case_id):
    return app.get_case(conv.token, case_id)


def timer_events(app, case_id, name=None):
    return [e for e in app.stores.traces.events
            if e["case_id"] == case_id and e["name"].startswith("sla_timer") and (name is None or e["name"] == name)]


# ================================================================ scheduling

def test_opening_a_b_case_schedules_the_three_timers_from_the_promise(app, clock):
    conv, case_id = open_b(app)
    case = stored(app, case_id)
    assert case.sla_timers == ["unassigned", "sla_80", "breached"] and case.timer_scale == 1.0
    lc = app.lifecycle
    assert lc.fire_at(case, "unassigned") == case.clock.assigned_by == case.created_at + timedelta(hours=24)
    assert lc.fire_at(case, "sla_80") == case.clock.sla_alert_at == case.created_at + timedelta(days=12)
    assert lc.fire_at(case, "breached") == case.clock.breach_at == case.created_at + timedelta(days=15)
    (ev,) = timer_events(app, case_id, "sla_timer.schedule")
    assert ev["actor"] == "rule" and ev["trace_id"] == f"lc-{case_id}" and "scale:1;" in ev["output_ref"]
    # Nothing fired yet, nothing to show, and the card keeps polling.
    r = card(app, conv, case_id)
    assert r["case"]["notices"] == [] and r["poll_after_ms"] == C.CASE_POLL_MS
    assert queue_item(app, case_id)["sla_alerts"] == []


def test_lane_a_and_c_cases_get_no_timers(app):
    conv = Conv(app, "martina")
    conv.say("Me aparece un consumo de 145 mil en ELECTRO MUNDO ONLINE el 15 que no reconozco")
    r = conv.press("confirm")
    assert r["lane"] == "C"
    assert stored(app, r["case_card"]["case_id"]).sla_timers == []
    assert timer_events(app, r["case_card"]["case_id"]) == []


# ================================================================ unassigned (+24 h)

def test_unassigned_fires_after_24h_as_a_queue_alert_until_an_analyst_takes_it(app, clock):
    conv, case_id = open_b(app)
    clock.advance(hours=23, minutes=59)
    assert queue_item(app, case_id)["sla_alerts"] == []
    clock.advance(minutes=1)
    item = queue_item(app, case_id)
    assert item["sla_alerts"] == ["unassigned"] and item["status"] == "awaiting_analyst"
    (ev,) = timer_events(app, case_id, "sla_timer.unassigned")
    assert ev["actor"] == "rule" and ev["output_ref"] == "status:awaiting_analyst;alert:unassigned"
    assert card(app, conv, case_id)["case"]["notices"] == []  # an internal alert: nothing to the customer
    # The analyst opens it: taken; the event stays in the case, the queue alert goes away.
    d = detail(app, case_id)
    assert [e["kind"] for e in d["case"]["clock_events"]] == ["unassigned"]
    assert d["case"]["clock_events"][0]["fired_at"] == clock.now.isoformat()
    assert queue_item(app, case_id)["sla_alerts"] == []
    assert stored(app, case_id).sla_timers == ["sla_80", "breached"]


def test_opening_the_case_before_24h_deletes_the_unassigned_timer(app, clock):
    conv, case_id = open_b(app)
    clock.advance(hours=2)
    d = detail(app, case_id)
    case = stored(app, case_id)
    assert case.taken_at == clock.now and case.sla_timers == ["sla_80", "breached"]
    assert d["version"] == case.version  # the take is written before the detail is read
    (ev,) = timer_events(app, case_id, "sla_timer.cancel")
    assert ev["output_ref"] == "kinds:unassigned;why:taken"
    clock.advance(days=2)
    assert queue_item(app, case_id)["sla_alerts"] == [] and timer_events(app, case_id, "sla_timer.unassigned") == []
    # A second open writes nothing more.
    version = stored(app, case_id).version
    detail(app, case_id)
    assert stored(app, case_id).version == version


# ================================================================ sla_80

@pytest.mark.parametrize("key", sorted(APP_COMPLAINT))
def test_sla_80_notice_in_the_case_language_and_register_with_the_promised_date(clock, key):
    app = make_app(clock=clock, investigation_delay=0, ttl_minutes=LONG_TTL)
    conv, case_id = open_b(app, key)
    _, lang, country = APP_COMPLAINT[key]
    detail(app, case_id)  # taken: only the customer notice is in play
    clock.advance(days=12)
    r = card(app, conv, case_id)
    case = stored(app, case_id)
    expected = render("sla_notice.sla_80", lang, country, case_id=case_id,
                      expected_date=format_date(case.promise.expected_date, lang))
    (n,) = r["case"]["notices"]
    assert n == {"kind": "sla_80", "text": expected, "language": lang, "at": clock.now.isoformat()}
    assert case_id in n["text"] and format_date(case.promise.expected_date, lang) in n["text"]
    assert forbidden_hits(n["text"]) == []
    assert r["case"]["status"] == "awaiting_analyst" and r["poll_after_ms"] == C.CASE_POLL_MS
    # The promise itself does not move.
    assert r["case"]["expected_date"] == case.promise.expected_date.isoformat()
    # The analyst sees it in the conversation, as a template sent by the system.
    last = detail(app, case_id)["case"]["conversation"][-1]
    assert last == {"role": "system", "text": expected, "language": lang, "source": "template",
                    "at": clock.now.isoformat()}
    (ev,) = timer_events(app, case_id, "sla_timer.sla_80")
    assert ev["actor"] == "rule" and ev["output_ref"] == "status:awaiting_analyst;notice:sla_80"
    assert expected not in (ev["input_ref"] + ev["output_ref"])  # traces carry no text


def test_registers_differ_by_country():
    t = {c: render("sla_notice.sla_80", "es", c, case_id="EV-X", expected_date="1 de enero de 2027")
         for c in ("MX", "CO", "AR")}
    assert "tu caso" in t["MX"] and "te escribiremos" in t["MX"]
    assert "su caso" in t["CO"] and "le escribiremos" in t["CO"]
    assert "te vamos a escribir" in t["AR"]
    pt = render("sla_notice.escalated", "pt", "AR", case_id="EV-X", expected_date="1º de janeiro de 2027")
    assert "equipe sênior" in pt and "1º de janeiro de 2027" in pt
    for lang in ("es", "pt"):
        for key in ("sla_notice.sla_80", "sla_notice.escalated", "switch.fast_clock_on", "switch.fast_clock_off"):
            for c in ("MX", "CO", "AR"):
                assert forbidden_hits(render(key, lang, c, case_id="EV-X", expected_date="x", day="1 minuto")) == []


def test_each_notice_is_sent_once(app, clock):
    conv, case_id = open_b(app)
    clock.advance(days=12, hours=1)
    for _ in range(3):
        card(app, conv, case_id)
        queue_item(app, case_id)
    assert [n["kind"] for n in card(app, conv, case_id)["case"]["notices"]] == ["sla_80"]
    assert len(timer_events(app, case_id, "sla_timer.sla_80")) == 1


# ================================================================ breached

def test_breach_escalates_to_the_senior_queue_with_high_priority_and_tells_the_customer(app, clock):
    conv, case_id = open_b(app, "joao")
    clock.advance(days=1)
    other_conv, other = open_b(app, "lucia")  # opened a day later: not breached yet
    clock.advance(days=14)
    # One read after a long silence fires the three, in order, in one write.
    version = stored(app, case_id).version
    r = card(app, conv, case_id)
    assert [n["kind"] for n in r["case"]["notices"]] == ["sla_80", "escalated"]
    esc = r["case"]["notices"][1]
    case = stored(app, case_id)
    assert esc["language"] == "pt" and esc["text"] == render(
        "sla_notice.escalated", "pt", "AR", case_id=case_id, expected_date=format_date(case.promise.expected_date, "pt"))
    assert r["case"]["status"] == "awaiting_analyst" and r["case"]["lifecycle_step"] == "in_review"
    assert r["poll_after_ms"] == C.CASE_POLL_MS
    assert case.handoff.queue == SENIOR_QUEUE and case.handoff.priority == "high"
    assert BREACH_NOTE in case.handoff.open_questions and "sla_breached" in case.urgency_flags
    assert case.sla_timers == [] and case.version == version + 1
    item = queue_item(app, case_id)
    assert item["queue"] == "senior" and item["priority"] == "high"
    assert item["sla_alerts"] == ["unassigned", "sla_80", "breached"]
    items = app.analyst.list_cases(CLAIMS, {})["items"]
    assert [i["case_id"] for i in items].index(case_id) < [i["case_id"] for i in items].index(other)
    names = [e["name"] for e in timer_events(app, case_id) if e["name"] != "sla_timer.schedule"]
    assert names == ["sla_timer.unassigned", "sla_timer.sla_80", "sla_timer.breached"]
    (ev,) = timer_events(app, case_id, "sla_timer.breached")
    assert ev["output_ref"] == "status:awaiting_analyst;queue:senior;priority:high;notice:escalated"
    assert queue_item(app, other)["sla_alerts"] == ["unassigned", "sla_80"]  # 14 days: 80 %, not breached
    # Still decidable by the senior analyst.
    d = detail(app, case_id)
    assert d["allowed_actions"] == ["edit", "reject"]
    assert [e["kind"] for e in d["case"]["clock_events"]] == ["unassigned", "sla_80", "breached"]


def test_breach_while_investigating_escalates_without_moving_the_status(clock):
    app = make_app(clock=clock, investigation_delay=10 ** 9, ttl_minutes=LONG_TTL)  # the report never comes
    conv = Conv(app, "lucia")
    app.chat(conv.token, {"client_msg_id": "fc", "demo_switches": {"fast_clock": True}})
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    case_id = conv.press("deny")["case_card"]["case_id"]
    clock.advance(minutes=15)
    r = card(app, conv, case_id)
    assert r["case"]["status"] == "investigating" and [n["kind"] for n in r["case"]["notices"]] == ["sla_80", "escalated"]
    assert stored(app, case_id).handoff.queue == SENIOR_QUEUE


# ================================================================ cancellation and closed cases

def test_deciding_the_case_deletes_the_pending_timers(app, clock):
    conv, case_id = open_b(app)
    clock.advance(hours=30)  # unassigned fires first
    d = detail(app, case_id)
    out = app.analyst.decide(CLAIMS, case_id, {"client_decision_id": "d1", "version": d["version"], "action": "reject",
                                               "reason": "falta el dato del error", "next": "request_information"})
    assert out["status"] == "notified"
    case = stored(app, case_id)
    assert case.sla_timers == []
    cancel = [e for e in timer_events(app, case_id, "sla_timer.cancel") if "decided" in e["output_ref"]]
    assert [e["output_ref"] for e in cancel] == ["kinds:sla_80,breached;why:decided"]
    clock.advance(days=20)
    r = card(app, conv, case_id)
    assert r["case"]["notices"] == [] and r["poll_after_ms"] is None
    assert [e.kind for e in stored(app, case_id).clock_events] == ["unassigned"]
    assert timer_events(app, case_id, "sla_timer.sla_80") == timer_events(app, case_id, "sla_timer.breached") == []
    assert stored(app, case_id).handoff.queue is None  # never escalated by the clock


def test_escalation_by_the_analyst_also_deletes_them(app, clock):
    conv, case_id = open_b(app)
    d = detail(app, case_id)
    app.analyst.decide(CLAIMS, case_id, {"client_decision_id": "d1", "version": d["version"], "action": "reject",
                                         "reason": "revisar con senior", "next": "escalate"})
    clock.advance(days=16)
    card(app, conv, case_id)
    case = stored(app, case_id)
    assert case.status == CaseStatus.handed_off and case.notices == [] and case.clock_events == []


@pytest.mark.parametrize("status", ["notified", "closed", "resolved_in_contact", "handed_off", "reopened"])
def test_a_timer_left_behind_never_fires_on_a_case_that_is_not_open(app, clock, status):
    """Like an EventBridge schedule that fires after the case was answered: the target
    re-reads the case and does nothing."""
    conv, case_id = open_b(app)
    s = app.stores.cases.get(case_id)
    s.case.status = CaseStatus(status)
    app.stores.cases.put(s.case, s.session_id)
    clock.advance(days=16)
    app.lifecycle._fire_due(app.stores.cases.get(case_id))
    case = stored(app, case_id)
    assert case.clock_events == [] and case.notices == [] and case.handoff.queue is None
    assert [e for e in timer_events(app, case_id)
            if e["name"] not in ("sla_timer.schedule", "sla_timer.cancel")] == []  # never fired


def test_poll_after_ms_follows_pending_timers():
    view = {"case_id": "EV-X", "lane": "B", "status": "notified"}
    assert poll_after_ms(view) is None and poll_after_ms(view, pending_timers=True) == C.CASE_POLL_MS
    assert poll_after_ms({**view, "lane": "C"}, pending_timers=True) is None
    assert poll_after_ms({**view, "status": "awaiting_analyst"}) == C.CASE_POLL_MS


# ================================================================ fast_clock (demo only)

def test_fast_clock_scales_only_the_cases_of_that_session(app, clock):
    fast_conv, fast = open_b(app, "lucia", fast_clock=True)
    slow_conv, slow = open_b(app, "sofia")
    assert fast_conv.last["demo_switches"]["fast_clock"] is True
    assert slow_conv.last["demo_switches"]["fast_clock"] is False
    assert stored(app, fast).timer_scale == DEFAULT_SLA_SCALE and stored(app, slow).timer_scale == 1.0
    # The promised dates do not change with the demo clock.
    for key in ("expected_date", "first_response_by"):
        assert fast_conv.last["case_card"][key] == slow_conv.last["case_card"][key]
    clock.advance(seconds=59)
    assert queue_item(app, fast)["sla_alerts"] == []
    clock.advance(seconds=1)  # 24 h / 1440 = 1 minute
    assert queue_item(app, fast)["sla_alerts"] == ["unassigned"] and queue_item(app, slow)["sla_alerts"] == []
    clock.advance(minutes=11)  # 12 days / 1440 = 12 minutes
    assert [n["kind"] for n in card(app, fast_conv, fast)["case"]["notices"]] == ["sla_80"]
    clock.advance(minutes=3)  # 15 minutes
    assert [n["kind"] for n in card(app, fast_conv, fast)["case"]["notices"]] == ["sla_80", "escalated"]
    assert queue_item(app, fast)["queue"] == "senior"
    r = card(app, slow_conv, slow)
    assert r["case"]["notices"] == [] and stored(app, slow).clock_events == []
    (ev,) = timer_events(app, fast, "sla_timer.sla_80")
    assert f"scale:{DEFAULT_SLA_SCALE:g}" in ev["input_ref"]


def test_fast_clock_switched_on_after_opening_rescales_the_open_case(app, clock):
    conv, case_id = open_b(app)
    r = app.chat(conv.token, {"client_msg_id": "fc-on", "demo_switches": {"fast_clock": True}})
    assert r["demo_switches"]["fast_clock"] is True and r["turn"] == 1
    assert r["reply_text"] == render("switch.fast_clock_on", "es", day="1 minuto")  # 1/1440
    assert stored(app, case_id).timer_scale == DEFAULT_SLA_SCALE
    (ev,) = timer_events(app, case_id, "sla_timer.rescale")
    assert ev["output_ref"].startswith(f"scale:{DEFAULT_SLA_SCALE:g};unassigned@")
    clock.advance(minutes=13)
    assert [n["kind"] for n in card(app, conv, case_id)["case"]["notices"]] == ["sla_80"]
    # Off again: what is still pending goes back to real time.
    r = app.chat(conv.token, {"client_msg_id": "fc-off", "demo_switches": {"fast_clock": False}})
    assert r["demo_switches"]["fast_clock"] is False and r["reply_text"] == render("switch.fast_clock_off", "es")
    clock.advance(minutes=5)
    assert [n["kind"] for n in card(app, conv, case_id)["case"]["notices"]] == ["sla_80"]
    assert stored(app, case_id).sla_timers == ["breached"]


def test_the_session_switch_is_used_when_the_lifecycle_opens_the_case_itself(app):
    conv, case_id = open_b(app, fast_clock=True)
    # start() without the flag (e.g. a case first seen by the analyst queue) reads the session.
    lc = LocalLifecycle(app.stores, clock=app.lifecycle.clock, delay_seconds=0, sla_scale=0.5)
    s = app.stores.cases.get(case_id)
    s.case.status, s.case.sla_timers, s.case.timer_scale = CaseStatus.open, [], 1.0
    app.stores.cases.put(s.case, s.session_id)
    lc.start(case_id)  # no fast_clock argument: the session store says it is on
    case = stored(app, case_id)
    assert case.timer_scale == 0.5 and case.sla_timers == ["unassigned", "sla_80", "breached"]
    assert lc._session_fast_clock("no-such-session") is False


def test_fast_clock_exists_only_in_demo_sessions(app):
    conv = Conv(app, "lucia")
    rec = app.sessions.authenticate(conv.token)
    rec.source = "production"
    app.sessions.save(rec)
    with pytest.raises(C.ApiFailure) as e:
        app.chat(conv.token, {"client_msg_id": "fc", "demo_switches": {"fast_clock": True}})
    assert e.value.code == "invalid_request"


def test_sla_scale_comes_from_the_environment(app):
    assert DEFAULT_SLA_SCALE == pytest.approx(1 / 1440)
    assert sla_scale_from_env({}) == DEFAULT_SLA_SCALE
    assert sla_scale_from_env({"LOCAL_SLA_SCALE": "0.00001"}) == 0.00001
    for bad in ("0", "-1", "2", "rápido", ""):
        assert sla_scale_from_env({"LOCAL_SLA_SCALE": bad}) == DEFAULT_SLA_SCALE
    lc = make_lifecycle({"STAGE": "local", "LOCAL_SLA_SCALE": "0.001"}, app.stores)
    assert lc.sla_scale == 0.001


def test_sla_scale_injected_into_the_app(clock):
    app = make_app(clock=clock, investigation_delay=0, ttl_minutes=LONG_TTL, sla_scale=0.00001)
    conv, case_id = open_b(app, fast_clock=True)
    clock.advance(seconds=13)  # 15 days * 0.00001 = 12.96 s
    assert [n["kind"] for n in card(app, conv, case_id)["case"]["notices"]] == ["sla_80", "escalated"]


# ================================================================ scripts/local_api.py --sla-scale

def _local_api():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "local_api.py"
    spec = importlib.util.spec_from_file_location("local_api_sla", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_local_api_sla_scale_flag(monkeypatch, capsys):
    mod = _local_api()
    for bad in ("0", "-1", "1.5"):
        with pytest.raises(SystemExit):
            mod.main(["--sla-scale", bad, "--port", "0"])
        assert "--sla-scale must be > 0 and <= 1" in capsys.readouterr().err
    seen = {}
    monkeypatch.setattr(mod, "configure", lambda *a, **kw: seen.update(kw) or (_ for _ in ()).throw(SystemExit(0)))
    with pytest.raises(SystemExit):
        mod.main(["--sla-scale", "0.00001"])
    assert seen["sla_scale"] == 0.00001


@pytest.mark.parametrize("status", ["notified", "closed", "handed_off"])
def test_a_timer_left_behind_is_deleted_so_the_card_stops_polling(app, clock, status):
    """Review 29 sep: a leftover schedule on an answered case used to stay in sla_timers, so
    has_pending_timers kept poll_after_ms > 0 forever. Now it is never "pending" and the first
    read deletes it (like a one-time schedule whose target found the case answered)."""
    conv, case_id = open_b(app)
    s = app.stores.cases.get(case_id)
    s.case.status = CaseStatus(status)
    app.stores.cases.put(s.case, s.session_id)
    assert stored(app, case_id).sla_timers and not app.lifecycle.has_pending_timers(case_id)
    clock.advance(hours=1)
    app.lifecycle.advance(case_id)
    case = stored(app, case_id)
    assert case.sla_timers == [] and case.clock_events == [] and case.notices == []
    cancel = timer_events(app, case_id, "sla_timer.cancel")
    assert [e["output_ref"] for e in cancel] == [f"kinds:unassigned,sla_80,breached;why:not_open:{status}"]
    app.lifecycle.advance(case_id)  # nothing left: no second cancel
    assert len(timer_events(app, case_id, "sla_timer.cancel")) == 1


def test_a_timer_due_after_the_analyst_opened_the_case_makes_the_decision_reload(app, clock):
    """Race timer vs decision: the analyst read version v, then the breach fired (v+1). The
    decision with v is a 409 (the case changed: it is in the senior queue now); after the
    reload it goes through and nothing fires afterwards."""
    conv, case_id = open_b(app)
    d = detail(app, case_id)
    clock.advance(days=16)
    queue_item(app, case_id)  # the queue poll fires sla_80 and breached
    body = {"client_decision_id": "d1", "version": d["version"], "action": "reject",
            "reason": "revisar con senior", "next": "escalate"}
    with pytest.raises(C.ApiFailure) as err:
        app.analyst.decide(CLAIMS, case_id, body)
    assert err.value.code == "conflict"
    d2 = detail(app, case_id)
    assert d2["version"] > d["version"] and d2["case"]["handoff"]["queue"] == SENIOR_QUEUE
    app.analyst.decide(CLAIMS, case_id, {**body, "client_decision_id": "d2", "version": d2["version"]})
    before = list(stored(app, case_id).clock_events)
    clock.advance(days=30)
    card(app, conv, case_id)
    case = stored(app, case_id)
    assert case.clock_events == before and [n.kind for n in case.notices] == ["sla_80", "escalated"]
    assert not app.lifecycle.has_pending_timers(case_id)
