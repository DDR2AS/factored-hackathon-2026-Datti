"""Console pending items (30 sep): AnalystContext.unavailable, the case's redacted conversation
for the analyst, and the request_information_default text. SYNTHETIC data only; no AWS, no model.
"""

from __future__ import annotations

import uuid

import pytest

from conversation import contract as C
from conversation import demo_gateway
from conversation.case import customer_view
from conversation.templates import forbidden_hits, render

from _support import Conv, FakeClock, make_app, reset_gateway

CLAIMS = {"sub": "analista-local", "email": "analista@demo.local"}


@pytest.fixture(autouse=True)
def _clean_gateway():
    reset_gateway()
    yield
    reset_gateway()


@pytest.fixture
def app():
    return make_app(clock=FakeClock(), investigation_delay=0)


def lucia_b(app, first="me cobraron como 450 en el super el 12, mi RFC es GODE561231AB1 y mi cel 55 1234 5678"):
    conv = Conv(app, "lucia")
    conv.say(first)
    conv.press("confirm")
    r = conv.press("deny")
    assert r["lane"] == "B"
    return conv, r["case_card"]["case_id"]


def decide(app, case_id, **body):
    d = app.analyst.get_detail(CLAIMS, case_id)
    return app.analyst.decide(CLAIMS, case_id, {"client_decision_id": uuid.uuid4().hex, "version": d["version"], **body})


# ---------------------------------------------------------------- AnalystContext.unavailable

def test_context_names_the_parts_that_could_not_be_read(app):
    _, case_id = lucia_b(app)
    ok = app.analyst.get_detail(CLAIMS, case_id)["context"]
    assert ok["unavailable"] == [] and ok["cards"] and ok["evidence_txns"]
    demo_gateway.set_tool_failure(None, True)  # process-wide, like local_api.py --fail tools
    try:
        ctx = app.analyst.get_detail(CLAIMS, case_id)["context"]
    finally:
        demo_gateway.set_tool_failure(None, False)
    assert ctx["unavailable"] == ["cards", "evidence_txns", "prior_contacts"]
    assert ctx["cards"] == [] and ctx["evidence_txns"] == [] and ctx["prior_contacts"] == []
    C.AnalystContext.model_validate(ctx)


def test_a_case_without_a_charge_has_nothing_unavailable(app):
    conv = Conv(app, "carlos")
    r = conv.say("Es la tercera vez que me quejo; si no lo arreglan voy al regulador")
    ctx = app.analyst.get_detail(CLAIMS, r["case_card"]["case_id"])["context"]
    assert ctx["unavailable"] == [] and ctx["evidence_txns"] == []


# ---------------------------------------------------------------- conversation for the analyst

def test_the_analyst_sees_the_redacted_conversation_of_the_case(app):
    conv, case_id = lucia_b(app)
    turns = app.analyst.get_detail(CLAIMS, case_id)["case"]["conversation"]
    assert [t["role"] for t in turns] == ["system", "customer", "system", "customer", "system", "customer", "system"]
    assert turns[0]["text"].startswith("Hola") and turns[0]["source"] == "template"  # the welcome
    first = turns[1]
    assert first["source"] == "human" and first["language"] == "es"
    assert "[documento]" in first["text"] and "[teléfono]" in first["text"]
    for secret in ("GODE561231AB1", "1234 5678"):
        assert all(secret not in t["text"] for t in turns)
    assert turns[3]["text"].startswith("[botón] ") and turns[5]["text"].startswith("[botón] ")
    assert all(t["source"] == "template" for t in turns if t["role"] == "system")
    for t in turns:
        C.ConversationTurn.model_validate(t)
    # Later turns are appended; the case version does not move for them.
    version = app.stores.cases.get(case_id).case.version
    conv.say("gracias, ¿cómo va?")
    turns2 = app.analyst.get_detail(CLAIMS, case_id)["case"]["conversation"]
    assert len(turns2) == len(turns) + 2 and turns2[-2]["text"] == "gracias, ¿cómo va?"
    assert app.stores.cases.get(case_id).case.version == version


def test_the_analyst_reply_is_added_as_a_human_turn(app):
    conv, case_id = lucia_b(app)
    decide(app, case_id, action="reject", reason="falta el ticket", next="request_information")
    turns = app.analyst.get_detail(CLAIMS, case_id)["case"]["conversation"]
    last = turns[-1]
    assert last["role"] == "analyst" and last["source"] == "human"
    assert last["text"].startswith(render("request_information_default", "es", "MX", case_id=case_id))
    conv.say("El ticket dice SUPER AHORRO pero yo no estuve ahí")
    turns = app.analyst.get_detail(CLAIMS, case_id)["case"]["conversation"]
    assert turns[-2]["role"] == "customer" and "no estuve" in turns[-2]["text"]


def test_a_case_never_carries_another_customers_turns(app):
    lucia, lucia_case = lucia_b(app)
    carlos = Conv(app, "carlos")
    r = carlos.say("Es la tercera vez que me quejo; si no lo arreglan voy al regulador")
    carlos_case = r["case_card"]["case_id"]
    lucia_turns = app.analyst.get_detail(CLAIMS, lucia_case)["case"]["conversation"]
    carlos_turns = app.analyst.get_detail(CLAIMS, carlos_case)["case"]["conversation"]
    assert not any("regulador" in t["text"] for t in lucia_turns)
    assert not any("[documento]" in t["text"] or "450" in t["text"] for t in carlos_turns)
    assert [t["role"] for t in carlos_turns] == ["system", "customer", "system"]
    # Never in what the customer sees.
    stored = app.stores.cases.get(lucia_case).case
    assert "conversation" not in customer_view(stored)
    assert "conversation" not in app.get_case(lucia.token, lucia_case)["case"]
    assert "conversation" not in r["case_card"]


def test_a_conversation_without_a_case_stays_in_the_session_only(app):
    conv = Conv(app, "lucia")
    conv.say("hola")
    assert app.stores.reviews.get_conversation("EV-NOPE0000") == []
    record = app.stores.sessions.get(conv.session_id)
    assert [t["role"] for t in record["conversation"]["transcript"]] == ["system", "customer", "system"]


def test_portuguese_turns_keep_their_language(app):
    conv = Conv(app, "joao")
    conv.say("Olá, cobraram uma tarifa de manutenção de 12.500 pesos no dia 1 e acho que está errada")
    r = conv.press("confirm")
    turns = app.analyst.get_detail(CLAIMS, r["case_card"]["case_id"])["case"]["conversation"]
    assert {t["language"] for t in turns} == {"pt"} and turns[-2]["text"].startswith("[botão] ")


# ---------------------------------------------------------------- request_information_default

@pytest.mark.parametrize("lang,country", [("es", "MX"), ("es", "CO"), ("es", "AR"), ("pt", "BR")])
def test_request_information_default_asks_to_answer_in_the_chat(lang, country):
    text = render("request_information_default", lang, country, case_id="EV-TEST0001")
    assert "contactar" not in text and "entrar em contato" not in text
    assert "por este chat" in text and "EV-TEST0001" in text
    assert not forbidden_hits(text)
