"""Third review round (29 sep): security, console and robustness findings on the backend.
Each test reproduces one finding (id in the name) and failed before its fix. SYNTHETIC data
only; no AWS, no model.
"""

from __future__ import annotations

import base64
import threading
import time
import uuid

import pytest

from conversation import contract as C
from conversation import demo_gateway
from conversation import investigator_stub as stub
from conversation.case import CaseStatus, Lane
from conversation.redact import redact
from conversation import sessions as sessions_mod
from conversation.sessions import SessionService
from conversation.store import MemorySessionStore

from _support import Conv, FakeClock, make_app, reset_gateway
MAX_SWITCH_REQUESTS = getattr(sessions_mod, "MAX_SWITCH_REQUESTS", 60)

from test_console_backend import (  # noqa: E402
    CLAIMS, andres_b, decide, detail, expect_failure, lucia_b, martina_c, sofia_b,
)


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
    return make_app(clock=clock, investigation_delay=0)


def _martina_blocked(app):
    conv, case_id = martina_c(app)
    r = conv.press("confirm", label="Sí")  # block offer: "Sí, bloquear"
    assert "7730" in r["reply_text"]
    return conv, case_id


# ================================================================ SEC-01

def test_sec01_expire_session_keeps_the_card_block_in_the_analyst_context(app):
    conv, case_id = _martina_blocked(app)
    assert detail(app, case_id)["context"]["cards"] == [{"card_last4": "7730", "status": "blocked"}]
    expect_failure("session_expired", app.chat, conv.token,
                   {"client_msg_id": "x1", "demo_switches": {"expire_session": True}})
    assert detail(app, case_id)["context"]["cards"] == [{"card_last4": "7730", "status": "blocked"}]


def test_sec01_the_block_recorded_in_the_case_wins_over_lost_demo_state(app):
    conv, case_id = _martina_blocked(app)
    demo_gateway.reset_demo_state()  # e.g. another process or a restarted server
    assert detail(app, case_id)["context"]["cards"] == [{"card_last4": "7730", "status": "blocked"}]


# ================================================================ SEC-02

def test_sec02_a_customer_tools_down_switch_does_not_empty_the_analyst_context(app):
    conv, case_id = _martina_blocked(app)
    before = detail(app, case_id)["context"]
    r = app.chat(conv.token, {"client_msg_id": "t1", "demo_switches": {"tools_down": True}})
    assert r["demo_switches"]["tools_down"] is True
    after = detail(app, case_id)["context"]
    assert after["cards"] == before["cards"] and after["cards"]
    assert after["evidence_txns"] == before["evidence_txns"] and after["evidence_txns"]
    assert after["prior_contacts"] == before["prior_contacts"]


def test_sec02_ttl_expiry_clears_the_session_failure_switch(clock):
    app = make_app(clock=clock, investigation_delay=0)
    conv = Conv(app, "lucia")
    sid = conv.session_id
    app.chat(conv.token, {"client_msg_id": "t1", "demo_switches": {"tools_down": True}})
    assert demo_gateway.tool_failure_on(sid)
    clock.advance(hours=3)
    expect_failure("session_expired", conv.say, "hola")
    assert not demo_gateway.tool_failure_on(sid)


# ================================================================ SEC-03

@pytest.mark.parametrize("text,lang,gone,label", [
    ("Vivo en Calle Falsa 123, Col. Roma, CDMX, CP 06700", "es",
     ["Falsa 123", "Col. Roma", "06700"], "[dirección]"),
    ("mi dirección es Av. Reforma 222 depto 5, Juárez", "es", ["Reforma 222", "Juárez"], "[dirección]"),
    ("vivo en la carrera 7 # 45-10 en Bogotá", "es", ["7 # 45-10"], "[dirección]"),
    ("Moro na Rua Augusta, 1500, bairro Consolação, CEP 01310-100", "pt",
     ["Augusta, 1500", "Consolação", "01310-100"], "[endereço]"),
    ("meu endereço é Avenida Paulista 900 ap 12", "pt", ["Paulista 900"], "[endereço]"),
])
def test_sec03_addresses_are_redacted(text, lang, gone, label):
    out = redact(text, lang)
    for g in gone:
        assert g not in out, (g, out)
    assert label in out
    assert redact(out, lang) == out  # idempotent


@pytest.mark.parametrize("text", [
    "me cobraron como 450 en el super el 12",
    "me cobraron 52 mil de un domicilio el 9",
    "Olá, cobraram uma tarifa de manutenção de 12.500 pesos no dia 1",
    "Me cobraron dos veces lo mismo en TIENDA TECNO, con minutos de diferencia",
    "en la calle me robaron la tarjeta y hoy veo un cargo de 450 pesos",
])
def test_sec03_complaint_text_without_an_address_is_untouched(text):
    assert redact(text, "pt" if text.startswith("Olá") else "es") == text


def test_sec03_the_address_never_reaches_the_analyst_statement(app):
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12. Vivo en Calle Falsa 123, Col. Roma, CDMX, CP 06700")
    conv.press("confirm")
    r = conv.press("deny")
    case_id = r["case_card"]["case_id"]
    statement = detail(app, case_id)["case"]["customer_statement"]
    assert "Falsa" not in statement and "06700" not in statement and "Roma" not in statement
    assert "[dirección]" in statement and "450" in statement
    assert "Falsa" not in (app.stores.cases.get(case_id).case.customer_statement or "")


# ================================================================ SEC-04

def test_sec04_switch_only_requests_have_their_own_cap(app):
    conv = Conv(app, "lucia")
    for i in range(MAX_SWITCH_REQUESTS):
        app.chat(conv.token, {"client_msg_id": f"s{i}", "demo_switches": {"model_slow": i % 2 == 0}})
    e = expect_failure("session_limit", app.chat, conv.token,
                       {"client_msg_id": "one-more", "demo_switches": {"model_slow": False}})
    assert e.http_status == 409
    record = app.sessions.authenticate(conv.token)
    assert len(record.replies) == MAX_SWITCH_REQUESTS
    assert len(record.model_dump_json()) < 100_000
    assert conv.say("hola")["turn"] == 1  # real turns keep their own cap


# ================================================================ SEC-05

def test_sec05_a_second_turn_of_a_busy_session_is_a_fast_retryable_409():
    svc = SessionService(MemorySessionStore(), ttl_minutes=15, turn_wait_seconds=0.2)
    held, release = threading.Event(), threading.Event()

    def hold():
        with svc.turn_guard("S-1", "m-1"):
            held.set()
            release.wait(5)

    th = threading.Thread(target=hold)
    th.start()
    held.wait(2)
    started = time.perf_counter()
    try:
        with pytest.raises(C.ApiFailure) as e:
            with svc.turn_guard("S-1", "m-2"):
                pass
        assert time.perf_counter() - started < 1.5
        assert e.value.code == "conflict" and e.value.retryable is True
    finally:
        release.set()
        th.join()
    with svc.turn_guard("S-1", "m-3"):  # free again afterwards
        pass


def test_sec05_model_slow_turn_does_not_hold_other_turns_of_the_session(clock):
    gate = threading.Event()

    def slow_sleep(seconds):
        gate.wait(5)

    from conversation.app import build_app
    from conversation.store import MemoryCaseStore, MemoryTraceSink, Stores

    stores = Stores(MemorySessionStore(), MemoryCaseStore(), MemoryTraceSink(), "memory")
    app = build_app(env={"STAGE": "local"}, clock=clock, stores=stores, sleep=slow_sleep, llm=None,
                    use_env_llm=False, investigation_delay=0)
    app.sessions.turn_wait = 0.2
    conv = Conv(app, "lucia")
    app.chat(conv.token, {"client_msg_id": "sw", "demo_switches": {"model_slow": True}})
    th = threading.Thread(target=lambda: app.chat(conv.token, {"client_msg_id": "m1", "message": "hola"}))
    th.start()
    time.sleep(0.1)
    started = time.perf_counter()
    try:
        e = expect_failure("conflict", app.chat, conv.token, {"client_msg_id": "m2", "message": "otra"})
        assert e.retryable is True and time.perf_counter() - started < 1.5
    finally:
        gate.set()
        th.join()


# ================================================================ SEC-06 / H5

def test_sec06_h5_concurrent_retries_of_the_same_decision_all_get_the_same_reply(app):
    _, case_id = lucia_b(app)
    v = detail(app, case_id)["version"]
    original = app.lifecycle.on_decision

    def slow_on_decision(*args, **kwargs):
        out = original(*args, **kwargs)
        time.sleep(0.1)  # the case is written, the idempotent reply not yet
        return out

    app.lifecycle.on_decision = slow_on_decision
    body = {"client_decision_id": "dup-1", "version": v, "action": "approve"}
    barrier = threading.Barrier(8)
    results = []

    def go():
        barrier.wait()
        try:
            results.append(app.analyst.decide(CLAIMS, case_id, dict(body)))
        except C.ApiFailure as e:
            results.append(f"{e.code}: {e.message}")

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(isinstance(r, dict) for r in results), results
    assert all(r == results[0] for r in results)
    assert len([e for e in app.stores.traces.events if e.get("name") == "analyst_decision"]) == 1


# ================================================================ SEC-07

def _cursor(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@pytest.mark.parametrize("key", [
    "[1e999,0,0,0,\"x\"]", "[0,0,1e999,0,\"x\"]", "[0,0,0,-1e999,\"x\"]", "[0,0,NaN,0,\"x\"]",
    "[0,0,0,1e999,0,\"x\"]", "[0,1e999,0,0,0,\"x\"]", "[0,0,0,NaN,0,\"x\"]", "[0,0,0,0,Infinity,\"x\"]",
])
def test_sec07_a_cursor_with_huge_or_nan_numbers_is_400(app, key):
    lucia_b(app)
    cur = _cursor('{"k":' + key + ',"f":"None|None|None"}')
    e = expect_failure("invalid_request", app.analyst.list_cases, CLAIMS, {"cursor": cur})
    assert e.http_status == 400


# ================================================================ CX-01 / H1 (request information)

def test_cx01_approving_a_request_information_draft_waits_for_the_customer(app, clock):
    conv, case_id = lucia_b(app)
    assert detail(app, case_id)["report"]["recommendation"] == "request_information"
    decide(app, case_id, action="approve")
    clock.advance(seconds=31)
    assert app.get_case(conv.token, case_id)["case"]["status"] == "notified"  # no close timer
    r = conv.say("Nadie más usa mi tarjeta y no tengo el comprobante")
    assert "cerrado" not in r["reply_text"] and case_id in r["reply_text"]
    assert r["case_card"]["status"] == "awaiting_analyst" and r["poll_after_ms"]
    d = detail(app, case_id)
    assert "Nadie más usa mi tarjeta" in d["case"]["customer_statement"]
    assert d["case"]["analyst_decision"] is None and d["allowed_actions"]
    assert any("respondió al pedido de información" in q for q in d["case"]["handoff"]["open_questions"])
    assert any("Decisión anterior" in q for q in d["case"]["handoff"]["open_questions"])
    clock.advance(seconds=60)
    assert detail(app, case_id)["case"]["status"] == "awaiting_analyst"
    # The next decision is said in the chat again (a second resolution of the same case).
    decide(app, case_id, action="reject", reason="falta el ticket", next="request_information",
           reply={"language": "es", "text": "¿Nos puede enviar el ticket de la compra?"})
    r = conv.say("¿cómo va mi caso?")
    assert "¿Nos puede enviar el ticket de la compra?" in r["reply_text"]


def test_h1_reject_request_information_attaches_the_answer_instead_of_another_complaint(app, clock):
    conv, case_id = lucia_b(app)
    out = decide(app, case_id, action="reject", reason="falta comprobante", next="request_information")
    assert out["status"] == "notified"
    r = conv.say("sí, tengo el comprobante, pagué 449.90 con mi tarjeta 4821 el 12 de junio en super ahorro")
    assert "otro reclamo" not in r["reply_text"]
    assert r["case_card"]["status"] == "awaiting_analyst"
    d = detail(app, case_id)
    assert "tengo el comprobante" in d["case"]["customer_statement"]
    assert d["allowed_actions"] == ["approve", "edit", "reject"]
    clock.advance(seconds=31)
    assert detail(app, case_id)["case"]["status"] == "awaiting_analyst"


def test_h1_a_status_question_while_waiting_does_not_reopen_and_says_we_wait(app, clock):
    conv, case_id = lucia_b(app)
    decide(app, case_id, action="reject", reason="falta comprobante", next="request_information")
    app.get_case(conv.token, case_id)
    r = conv.say("¿cómo va mi caso?")
    assert "cerrado" not in r["reply_text"] and "esperando tu respuesta" in r["reply_text"]
    assert app.stores.cases.get(case_id).case.status == CaseStatus.notified


def test_cx01_a_final_answer_still_closes_after_the_timer(app, clock):
    conv, case_id = andres_b(app)  # open_chargeback: nothing asked to the customer
    decide(app, case_id, action="approve")
    clock.advance(seconds=31)
    assert app.get_case(conv.token, case_id)["case"]["status"] == "closed"


# ================================================================ H6 (disagreement: second opinion)

@pytest.mark.parametrize("wait,text", [
    (0, "no estoy de acuerdo con la respuesta"),
    (31, "quiero una segunda opinión, no estoy de acuerdo"),
    (31, "quiero reabrir el caso"),
])
def test_h6_disagreement_reopens_the_case_for_a_person(app, clock, wait, text):
    conv, case_id = andres_b(app)
    decide(app, case_id, action="approve")
    clock.advance(seconds=wait)
    app.get_case(conv.token, case_id)
    r = conv.say(text)
    assert "notificado" not in r["reply_text"] and "cerrado" not in r["reply_text"]
    assert r["case_card"]["status"] == "awaiting_analyst"
    d = detail(app, case_id)
    assert d["case"]["handoff"]["priority"] == "high" and d["case"]["handoff"]["queue"] == "senior"
    assert any("no está de acuerdo" in q for q in d["case"]["handoff"]["open_questions"])
    assert d["allowed_actions"] and d["case"]["analyst_decision"] is None
    assert any(e["name"] == "lifecycle.transition" and "reopened" in (e["input_ref"] or "")
               for e in app.stores.traces.events if e.get("case_id") == case_id)


# ================================================================ H2

def test_h2_asking_for_a_person_on_a_senior_case_keeps_queue_priority_and_clock(app, clock):
    conv, case_id = lucia_b(app)
    decide(app, case_id, action="reject", reason="caso complejo", next="escalate")
    before = app.stores.cases.get(case_id).case
    assert before.handoff.queue == "senior" and before.status == CaseStatus.handed_off
    clock.advance(minutes=10)
    r = conv.say("¿cómo va mi caso?")
    assert not any(b["kind"] == "handoff" for b in r["buttons"])
    conv.say("quiero hablar con una persona")
    after = app.stores.cases.get(case_id).case
    assert after.handoff.queue == "senior"
    assert after.handoff.priority == (before.handoff.priority or "normal") or after.handoff.priority == "high"
    assert after.clock.breach_at == before.clock.breach_at
    assert after.clock.first_response_by == before.clock.first_response_by


# ================================================================ H3

def test_h3_an_investigation_of_one_case_does_not_block_another_case(clock):
    app = make_app(clock=clock, investigation_delay=4)
    _, slow_case = andres_b(app)
    conv2, other = lucia_b(app)
    clock.advance(seconds=5)
    gate = threading.Event()
    original = app.lifecycle.investigator

    def investigator(case, *args, **kwargs):
        if case.case_id == slow_case:
            gate.wait(5)
        return original(case, *args, **kwargs)

    app.lifecycle.investigator = investigator
    th = threading.Thread(target=app.lifecycle.advance, args=(slow_case,))
    th.start()
    time.sleep(0.1)
    started = time.perf_counter()
    try:
        stored = app.lifecycle.advance(other)
        assert time.perf_counter() - started < 1.0
        assert stored.case.status == CaseStatus.awaiting_analyst
    finally:
        gate.set()
        th.join()
    assert app.stores.cases.get(slow_case).case.status == CaseStatus.awaiting_analyst


def test_h3_the_queue_listing_has_a_time_budget_for_investigations(clock):
    app = make_app(clock=clock, investigation_delay=4)
    ids = [lucia_b(app)[1] for _ in range(6)]
    clock.advance(seconds=5)
    original = app.lifecycle.investigator

    def investigator(case, *args, **kwargs):
        time.sleep(0.2)
        return original(case, *args, **kwargs)

    app.lifecycle.investigator = investigator
    app.lifecycle.advance_budget_seconds = 0.3
    started = time.perf_counter()
    app.analyst.list_cases(CLAIMS, {})
    assert time.perf_counter() - started < 0.9
    for _ in range(6):  # the next polls finish the rest
        app.analyst.list_cases(CLAIMS, {})
    assert all(app.stores.cases.get(i).case.status == CaseStatus.awaiting_analyst for i in ids)


# ================================================================ CX-03

def test_cx03_high_priority_first_then_deadline_then_fifo_not_updated_at(app, clock):
    _, lucia = lucia_b(app)
    _, sofia = sofia_b(app)  # same second: same breach_at as Lucía
    _, martina = martina_c(app)
    ids = [i["case_id"] for i in app.analyst.list_cases(CLAIMS, {})["items"]]
    assert ids[0] == martina
    # A later write to Lucía's case must not move it above Sofía's (updated_at is not the order).
    order_before = [i for i in ids if i in (lucia, sofia)]
    clock.advance(minutes=3)
    stored = app.stores.cases.get(lucia)
    app.stores.cases.put(stored.case, stored.session_id)
    ids2 = [i["case_id"] for i in app.analyst.list_cases(CLAIMS, {})["items"]]
    assert [i for i in ids2 if i in (lucia, sofia)] == order_before
    items = app.analyst.list_cases(CLAIMS, {})["items"]
    page1 = app.analyst.list_cases(CLAIMS, {"limit": "1"})
    page2 = app.analyst.list_cases(CLAIMS, {"limit": "2", "cursor": page1["next_cursor"]})
    assert [i["case_id"] for i in page1["items"] + page2["items"]] == [i["case_id"] for i in items]


# ================================================================ CX-15

def test_cx15_the_report_carries_no_internal_team_notes(app):
    _, case_id = sofia_b(app)
    report = app.stores.reviews.get_report(case_id)
    text = " ".join(report["open_questions"]) + " " + str(report["evidence_records"])
    for internal in ("Andrés", "G2", "la demo no tiene", "get_merchant_stats"):
        assert internal not in text, internal
    assert "PROVISIONAL" in report["open_questions"][0] and "sin modelo" in report["open_questions"][0]
    assert stub.PROVISIONAL_NOTE == report["open_questions"][0]


# ================================================================ CX-16

def test_cx16_martina_is_not_asked_again_whether_she_recognizes_the_charge(app):
    _, case_id = martina_c(app)
    qs = detail(app, case_id)["case"]["handoff"]["open_questions"]
    assert "Preguntar si reconoce el cargo" not in qs
    assert any(q.startswith("Confirmar que no reconoce el cargo") for q in qs)
