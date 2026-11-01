"""Console contract pieces that live in the server today (29 sep). SYNTHETIC data only.

- customer_view gains resolution and lifecycle_step; analyst_view (every #3 field except
  customer_id) validates against AnalystCaseView for real cases of the six demo customers.
- ChatResponse echoes demo_switches; poll_after_ms > 0 only while a lane B case can move.
- The server side of demo_switches, the lifecycle, the investigator stub and the analyst routes
  are tested in tests/test_console_backend.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from conversation import contract as C
from conversation import demo_gateway
from conversation.case import (
    AnalystDecision,
    CaseStatus,
    Lane,
    Resolution,
    analyst_view,
    customer_view,
    lifecycle_step,
)

from _support import Conv, make_app, reset_gateway


@pytest.fixture(autouse=True)
def _clean_gateway():
    reset_gateway()
    yield
    reset_gateway()


def lucia_b(app):
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    r = conv.press("deny")  # No lo reconozco -> B
    assert r["lane"] == "B"
    return conv, r


def martina_c(app):
    conv = Conv(app, "martina")
    conv.say("Me aparece un consumo de 145 mil en ELECTRO MUNDO ONLINE el 15 que no reconozco")
    r = conv.press("confirm")
    assert r["lane"] == "C"
    return conv, r


# ---------------------------------------------------------------- chat response

def test_every_chat_response_echoes_the_switch_state():
    app = make_app()
    conv, r = lucia_b(app)
    assert all(x["demo_switches"] == {"tools_down": False, "model_slow": False, "fast_clock": False}
               for x in conv.replies)
    demo_gateway.set_tool_failure(conv.session_id, True)
    r = conv.say("¿cómo va mi caso?")
    assert r["demo_switches"]["tools_down"] is True


def test_poll_after_ms_only_while_a_lane_b_case_can_move():
    app = make_app()
    _, r = lucia_b(app)
    assert r["case_card"]["status"] == "investigating" and r["poll_after_ms"] == C.CASE_POLL_MS > 0
    _, r = martina_c(app)
    assert r["case_card"]["status"] == "handed_off" and r["poll_after_ms"] is None
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    r = conv.press("confirm")  # Sí, lo reconozco -> A
    assert r["lane"] == "A" and r["poll_after_ms"] is None


def test_get_case_polls_for_lane_b_and_not_for_c():
    app = make_app()
    conv, r = lucia_b(app)
    assert app.get_case(conv.token, r["case_card"]["case_id"])["poll_after_ms"] == C.CASE_POLL_MS
    conv, r = martina_c(app)
    assert app.get_case(conv.token, r["case_card"]["case_id"])["poll_after_ms"] is None


def test_invalid_switches_are_400():
    app = make_app()
    conv = Conv(app, "lucia")
    for body in ({"demo_switches": {}}, {"demo_switches": {"lane": "A"}}):
        with pytest.raises(C.ApiFailure) as e:
            app.chat(conv.token, {"client_msg_id": uuid.uuid4().hex, **body})
        assert e.value.code == "invalid_request"


# ---------------------------------------------------------------- customer view

def test_lifecycle_step_per_status_in_lane_b_and_null_elsewhere():
    app = make_app()
    _, r = lucia_b(app)
    case = app.stores.cases.get(r["case_card"]["case_id"]).case
    expected = {"open": "open", "reopened": "open", "investigating": "investigating",
                "awaiting_analyst": "in_review", "handed_off": "in_review", "notified": "notified",
                "closed": "closed", "resolved_in_contact": None}
    for status, step in expected.items():
        assert lifecycle_step(case.model_copy(update={"status": CaseStatus(status)})) == step, status
    for lane in (Lane.A, Lane.C, None):
        assert lifecycle_step(case.model_copy(update={"lane": lane})) is None


def test_resolution_reaches_the_customer_marked_as_approved_by_a_person():
    app = make_app()
    _, r = lucia_b(app)
    case = app.stores.cases.get(r["case_card"]["case_id"]).case
    sent = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
    done = case.model_copy(update={
        "status": CaseStatus.notified,
        "resolution": Resolution(language="es", sent_at=sent,
                                 text="Revisamos tu caso (sintético). Respuesta aprobada por una persona del "
                                      "equipo. En esta demo ningún dinero se mueve."),
        "analyst_decision": AnalystDecision(action="approve", decided_by="analyst-sub-1", decided_at=sent),
    })
    view = C.CustomerCaseView.model_validate(customer_view(done)).model_dump()
    assert view["resolution"]["approved_by_human"] is True and view["resolution"]["sent_at"] == sent.isoformat()
    assert view["lifecycle_step"] == "notified" and view["outcome"] == "approve"
    assert "analyst-sub-1" not in str(view)  # who decided is for the console, not the customer


# ---------------------------------------------------------------- analyst view

def _cases_of_every_demo_customer(app):
    convs = []
    conv, _ = lucia_b(app)
    convs.append(conv)
    conv = Conv(app, "joao")
    conv.say("Oi, me cobraram uma tarifa que não bate com a tabela de vocês")
    conv.press("confirm")
    convs.append(conv)
    conv = Conv(app, "andres")
    conv.say("Me cobraron dos veces lo mismo, con minutos de diferencia")
    conv.press("confirm")
    convs.append(conv)
    conv, _ = martina_c(app)
    convs.append(conv)
    conv = Conv(app, "carlos")
    conv.say("Me cobraron 1.299 de STREAMING PLUS el 11, no lo reconozco y voy a ir a la CONDUSEF")
    convs.append(conv)
    conv = Conv(app, "sofia")
    conv.say("quiero hablar con una persona")
    convs.append(conv)
    ids = {r["case_card"]["case_id"] for c in convs for r in c.replies if r.get("case_card")}
    return [app.stores.cases.get(i).case for i in ids]


def test_analyst_view_of_real_cases_matches_the_contract_and_hides_the_customer_id():
    app = make_app()
    cases = _cases_of_every_demo_customer(app)
    assert {c.lane for c in cases} == {Lane.B, Lane.C} and len(cases) >= 6
    for case in cases:
        raw = analyst_view(case)
        view = C.AnalystCaseView.model_validate(raw).model_dump(by_alias=True)
        assert list(raw) == list(C.AnalystCaseView.model_fields)
        assert "customer_id" not in raw and case.customer_id not in str(view)
        if case.intent is not None:
            assert view["intent"]["class"] == case.intent.class_
        if case.evidence.transaction:
            assert view["evidence"]["transaction"]["txn_id"] == case.evidence.transaction["txn_id"]


def test_analyst_view_carries_the_decision_with_who_decided():
    app = make_app()
    _, r = lucia_b(app)
    case = app.stores.cases.get(r["case_card"]["case_id"]).case
    at = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
    decided = case.model_copy(update={
        "status": CaseStatus.handed_off, "version": 2, "updated_at": at,
        "analyst_decision": AnalystDecision(action="reject", reason="posible fraude", next="escalate",
                                            decided_by="analyst-sub-1", decided_at=at),
        "labels_emitted": ["decision:reject", "intent:dispute_charge:confirmed"],
    })
    view = C.AnalystCaseView.model_validate(analyst_view(decided)).model_dump()
    assert view["analyst_decision"] == {"action": "reject", "edited": False, "reason": "posible fraude",
                                        "decided_by": "analyst-sub-1", "decided_at": at.isoformat()}
    assert view["lifecycle_step"] == "in_review" and view["updated_at"] == at.isoformat()
