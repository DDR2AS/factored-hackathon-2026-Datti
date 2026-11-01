"""Console backend (30 sep): lane B lifecycle, PROVISIONAL investigator stub, analyst routes,
judge-mode switches. SYNTHETIC data only; no AWS, no model.

The clock and the investigation delay are injected, so every transition is deterministic.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from conversation import contract as C
from conversation import demo_gateway
from conversation import investigator_stub as stub
from conversation.case import CaseStatus, Lane
from conversation.lifecycle import LocalLifecycle, short_hash
from conversation.templates import forbidden_hits, render

from _support import Conv, FakeClock, call, make_app, reset_gateway, v2_event

REPO = Path(__file__).resolve().parents[1]
CLAIMS = {"sub": "analista-local", "email": "analista@demo.local"}
STAMP_ES = "Respuesta aprobada por una persona del equipo. En esta demo ningún dinero se mueve."
STAMP_PT = "Resposta aprovada por uma pessoa da equipe. Nesta demonstração nenhum dinheiro é movimentado."


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


# ---------------------------------------------------------------- conversations (synthetic)

def lucia_b(app):
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    r = conv.press("deny")
    assert r["lane"] == "B"
    return conv, r["case_card"]["case_id"]


def andres_b(app):
    conv = Conv(app, "andres")
    conv.say("Me cobraron dos veces lo mismo en TIENDA TECNO, con minutos de diferencia")
    r = conv.press("confirm")
    assert r["trace_summary"]["rule_id"] == "duplicate_not_reversed"
    return conv, r["case_card"]["case_id"]


def joao_b(app):
    conv = Conv(app, "joao")
    conv.say("Olá, cobraram uma tarifa de manutenção de 12.500 pesos no dia 1 e acho que está errada")
    r = conv.press("confirm")
    assert r["trace_summary"]["rule_id"] == "fee_does_not_match"
    return conv, r["case_card"]["case_id"]


def overcharge_b(app):
    conv = Conv(app, "lucia")
    conv.say("Me cobraron de más en SUPER AHORRO el 12, fueron 449.90")
    r = conv.press("confirm")
    assert r["trace_summary"]["rule_id"] == "purchase_amount_disputed"
    return conv, r["case_card"]["case_id"]


def sofia_b(app):
    conv = Conv(app, "sofia")
    conv.say("Buenas, me cobraron algo raro")
    conv.say("Fueron como 52 mil de un domicilio el 9")
    conv.press(label="9 de junio de 2026 · DIDI FOOD")
    r = conv.press("deny")
    assert r["lane"] == "B"
    return conv, r["case_card"]["case_id"]


def app_complaint_b(app):
    conv = Conv(app, "lucia")
    r = conv.say("La app se cierra cada vez que intento pagar, ya van tres días")
    assert r["trace_summary"]["rule_id"] == "other_complaint_complete"
    return conv, r["case_card"]["case_id"]


def martina_c(app):
    conv = Conv(app, "martina")
    conv.say("Me aparece un consumo de 145 mil en ELECTRO MUNDO ONLINE el 15 que no reconozco")
    r = conv.press("confirm")
    assert r["lane"] == "C" and r["trace_summary"]["rule_id"] == "high_fraud_score"
    return conv, r["case_card"]["case_id"]


def detail(app, case_id, claims=CLAIMS):
    return app.analyst.get_detail(claims, case_id)


def decide(app, case_id, claims=CLAIMS, **body):
    d = detail(app, case_id)
    payload = {"client_decision_id": uuid.uuid4().hex, "version": d["version"], **body}
    return app.analyst.decide(claims, case_id, payload)


def expect_failure(code, fn, *args, **kwargs):
    with pytest.raises(C.ApiFailure) as e:
        fn(*args, **kwargs)
    assert e.value.code == code, (e.value.code, e.value.message)
    return e.value


def events(app, **match):
    return [e for e in app.stores.traces.events if all(e.get(k) == v for k, v in match.items())]


# ================================================================ lifecycle

def test_money_case_investigates_for_the_injected_delay_then_waits_for_the_analyst(clock):
    app = make_app(clock=clock, investigation_delay=4)
    conv, case_id = lucia_b(app)
    card = conv.last["case_card"]
    assert card["status"] == "investigating" and card["lifecycle_step"] == "investigating"
    assert conv.last["poll_after_ms"] == C.CASE_POLL_MS
    clock.advance(seconds=3)
    view = app.get_case(conv.token, case_id)
    assert view["case"]["status"] == "investigating" and app.stores.reviews.get_report(case_id) is None
    clock.advance(seconds=1)
    view = app.get_case(conv.token, case_id)
    assert view["case"]["status"] == "awaiting_analyst" and view["case"]["lifecycle_step"] == "in_review"
    assert view["poll_after_ms"] == C.CASE_POLL_MS
    report = app.stores.reviews.get_report(case_id)
    stored = app.stores.cases.get(case_id).case
    assert stored.investigation_id == report["report_id"]
    names = [(e["actor"], e["name"], e["output_ref"]) for e in events(app, case_id=case_id)
             if e["trace_id"] == f"lc-{case_id}"]
    transitions = [o for a, n, o in names if n == "lifecycle.transition"]
    assert transitions[0].startswith("status:investigating") and transitions[-1].startswith("status:awaiting_analyst")
    assert ("rule", "investigator_stub") in {(a, n) for a, n, _ in names}
    assert sum(1 for a, _, _ in names if a == "tool") == report["tool_calls"]


def test_non_money_complaint_waits_for_the_analyst_without_investigator(app):
    conv, case_id = app_complaint_b(app)
    assert conv.last["case_card"]["status"] == "awaiting_analyst"
    assert app.stores.reviews.get_report(case_id) is None
    d = detail(app, case_id)
    assert d["report"] is None and d["allowed_actions"] == ["edit", "reject"]
    assert not events(app, case_id=case_id, name="investigator_stub")


def test_lane_c_case_is_handed_off_and_in_the_queue_with_its_package(app):
    conv, case_id = martina_c(app)
    assert conv.last["case_card"]["lifecycle_step"] is None and conv.last["poll_after_ms"] is None
    listing = app.analyst.list_cases(CLAIMS, {"lane": "C"})
    item = next(i for i in listing["items"] if i["case_id"] == case_id)
    assert item["status"] == "handed_off" and item["queue"] == "fraud" and item["priority"] == "high"
    assert item["has_report"] is False and item["report_reliable"] is None and item["display_name"] == "Martina"
    d = detail(app, case_id)
    assert d["allowed_actions"] == [] and d["report"] is None
    handoff = d["case"]["handoff"]
    assert handoff["queue"] == "fraud" and handoff["open_questions"] and handoff["facts_verified"]
    assert d["context"]["evidence_txns"][0]["txn_id"] == "TX-MAR-0001"
    expect_failure("precondition", decide, app, case_id, action="reject", reason="ya lo ve fraudes", next="escalate")


def test_notified_case_closes_after_the_timer(app, clock):
    # A final answer (open_chargeback). A draft that asks the customer for information waits
    # for the answer instead (CX-01/H1, tests/test_review_round3.py).
    conv, case_id = andres_b(app)
    decide(app, case_id, action="approve")
    clock.advance(seconds=29)
    assert app.get_case(conv.token, case_id)["case"]["status"] == "notified"
    clock.advance(seconds=1)
    view = app.get_case(conv.token, case_id)
    assert view["case"]["status"] == "closed" and view["case"]["lifecycle_step"] == "closed"
    assert view["poll_after_ms"] is None and view["case"]["resolution"]["approved_by_human"] is True


def test_concurrent_reads_run_the_investigator_once(clock):
    app = make_app(clock=clock, investigation_delay=4)
    conv, case_id = andres_b(app)
    clock.advance(seconds=5)
    threads = [threading.Thread(target=app.get_case, args=(conv.token, case_id)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(events(app, case_id=case_id, name="investigator_stub")) == 1
    assert app.stores.cases.get(case_id).case.status == CaseStatus.awaiting_analyst


def test_tools_down_during_the_investigation_leaves_no_report_and_no_approve(clock):
    app = make_app(clock=clock, investigation_delay=4)
    conv, case_id = andres_b(app)
    demo_gateway.set_tool_failure(conv.session_id, True)
    clock.advance(seconds=5)
    assert app.get_case(conv.token, case_id)["case"]["status"] == "awaiting_analyst"
    case = app.stores.cases.get(case_id).case
    assert app.stores.reviews.get_report(case_id) is None and "evidence_incomplete" in case.urgency_flags
    assert any("no produjo reporte (tool_unavailable)" in q for q in case.handoff.open_questions)
    assert events(app, case_id=case_id, name="investigator_stub")[0]["error_code"] == "tool_unavailable"
    demo_gateway.set_tool_failure(conv.session_id, False)
    assert detail(app, case_id)["allowed_actions"] == ["edit", "reject"]


def test_escalation_of_a_b_case_keeps_its_version_and_report(app):
    conv, case_id = lucia_b(app)
    before = app.stores.cases.get(case_id).case
    r = conv.say("Si no lo resuelven voy a ir a la CONDUSEF")
    after = app.stores.cases.get(case_id).case
    assert r["lane"] == "C" and after.lane == Lane.C and after.version > before.version
    assert after.investigation_id == before.investigation_id is not None


# ================================================================ investigator stub

@pytest.mark.parametrize("flow,recommendation", [
    (andres_b, "open_chargeback"),       # duplicate, not reversed
    (joao_b, "reverse_fee"),             # fee above the schedule
    (overcharge_b, "request_information"),  # purchase_amount_disputed
    (sofia_b, "open_chargeback"),        # unrecognized, low risk, merchant never seen before
    (lucia_b, "request_information"),    # unrecognized, low risk, known merchant
])
def test_stub_recommendation_per_scenario_and_report_shape(app, flow, recommendation):
    _, case_id = flow(app)
    report = app.stores.reviews.get_report(case_id)
    C.InvestigatorReport.model_validate(report)
    assert report["recommendation"] == recommendation
    assert report["model_id"] == stub.STUB_MODEL_ID == "stub-g2-deterministic"
    assert report["prompt_version"] == stub.STUB_PROMPT_VERSION == "stub-provisional"
    # CX-15: marked provisional and rule-based, without internal team notes.
    assert "PROVISIONAL" in report["open_questions"][0] and "sin modelo" in report["open_questions"][0]
    assert 1 <= report["tool_calls"] <= stub.MAX_TOOL_CALLS and report["latency_ms"] >= 0
    assert report["citations_valid"] is True and report["removed_claims"] == 0
    names = [h["name"] for h in report["hypotheses"]]
    assert sorted(names) == sorted(stub.HYPOTHESES)
    assert abs(sum(h["p"] for h in report["hypotheses"]) - 1) < 1e-9
    assert [h["p"] for h in report["hypotheses"]] == sorted((h["p"] for h in report["hypotheses"]), reverse=True)
    cited = {i for f in report["findings"] for i in f["evidence_ids"]}
    assert cited and cited == set(report["evidence_records"])
    assert all(f["evidence_ids"] for f in report["findings"])
    case = app.stores.cases.get(case_id).case
    assert case.customer_id not in json.dumps(report, ensure_ascii=False)
    assert report["draft_reply"]["language"] == case.language and forbidden_hits(report["draft_reply"]["text"]) == []
    assert case_id in report["draft_reply"]["text"]


def test_stub_is_deterministic_for_the_same_case(app, clock):
    _, case_id = andres_b(app)
    stored = app.stores.cases.get(case_id)
    ctx = demo_gateway.GatewayContext(customer_id=stored.case.customer_id, session_id=stored.session_id, trace_id="t")
    a = stub.investigate(stored.case, demo_gateway, ctx, clock)
    b = stub.investigate(stored.case, demo_gateway, ctx, clock)
    for r in (a, b):
        r.pop("latency_ms")
    assert a == b


def test_stub_evidence_ids_come_from_the_tools_it_called(app, clock):
    _, case_id = andres_b(app)
    stored = app.stores.cases.get(case_id)
    calls = []

    class Spy:
        def __getattr__(self, name):
            target = getattr(demo_gateway, name)
            if callable(target) and name in ("get_transaction", "find_duplicates", "get_reversals", "get_fee_schedule",
                                             "get_customer_baseline", "get_txn_history", "get_prior_contacts",
                                             "get_cards"):
                def wrapped(*a, **k):
                    calls.append(name)
                    return target(*a, **k)
                return wrapped
            return target

    ctx = demo_gateway.GatewayContext(customer_id=stored.case.customer_id, session_id=stored.session_id, trace_id="t")
    report = stub.investigate(stored.case, Spy(), ctx, clock)
    assert len(calls) == report["tool_calls"] <= 12
    assert set(report["evidence_records"]) >= {"TX-AND-0001", "TX-AND-0002", "RISK:TX-AND-0002", "BASELINE",
                                               "MERCHANT:M-TECNO", "DEMO-K-AND-01"}
    kinds = {v["kind"] for v in report["evidence_records"].values()}
    assert kinds <= set(C.EvidenceKind.__args__)


def test_validate_citations_removes_findings_with_unknown_ids():
    report = {"findings": [{"claim": "ok", "evidence_ids": ["TX-1"]},
                           {"claim": "inventado", "evidence_ids": ["TX-1", "TX-NOPE"]},
                           {"claim": "sin cita", "evidence_ids": []}],
              "evidence_records": {}}
    records = {"TX-1": {"kind": "transaction", "summary": "s", "fields": {}},
               "TX-2": {"kind": "transaction", "summary": "s", "fields": {}}}
    out = stub.validate_citations(report, records)
    assert [f["claim"] for f in out["findings"]] == ["ok"]
    # What is left is valid (citations_valid), but two removed claims make it unreliable (plan §6).
    assert out["removed_claims"] == 2 and out["citations_valid"] is True
    assert C.report_reliable(out) is False
    assert out["evidence_records"] == {"TX-1": records["TX-1"]}  # only what is still cited
    assert len(report["findings"]) == 3  # the input is not mutated
    rec = {"kind": "transaction", "summary": "s", "fields": {}}
    clean = stub.validate_citations({"findings": [{"claim": "ok", "evidence_ids": ["TX-1"]}],
                                     "evidence_records": {"TX-1": rec}}, ["TX-1"])
    assert clean["citations_valid"] is True and clean["removed_claims"] == 0
    # Only ids, no record to open: the citation cannot be shown, so it is not valid.
    bare = stub.validate_citations({"findings": [{"claim": "ok", "evidence_ids": ["TX-1"]}]}, ["TX-1"])
    assert bare["citations_valid"] is False
    one = stub.validate_citations({"findings": [{"claim": "ok", "evidence_ids": ["TX-1"]},
                                                {"claim": "x", "evidence_ids": ["TX-NOPE"]}]}, records)
    assert one["removed_claims"] == 1 and C.report_reliable(one) is True  # one removal is tolerated


def test_a_report_with_two_invented_citations_is_unreliable_and_needs_evidence_reviewed(clock):
    def invent(case, gw, ctx, clk, **kw):
        return stub.investigate(case, gw, ctx, clk, extra_findings=[
            {"claim": "El cliente ya había reclamado este cargo", "evidence_ids": ["DEMO-CT-FAKE-99"]},
            {"claim": "Hubo otro cargo igual", "evidence_ids": ["TX-FAKE-98"]}], **kw)

    app = make_app(clock=clock, investigation_delay=0)
    app.lifecycle.investigator = invent
    _, case_id = andres_b(app)
    report = app.stores.reviews.get_report(case_id)
    assert report["removed_claims"] == 2 and report["citations_valid"] is True
    assert all(i not in ("DEMO-CT-FAKE-99", "TX-FAKE-98") for f in report["findings"] for i in f["evidence_ids"])
    item = next(i for i in app.analyst.list_cases(CLAIMS, {})["items"] if i["case_id"] == case_id)
    assert item["has_report"] is True and item["report_reliable"] is False
    expect_failure("precondition", decide, app, case_id, action="approve")
    r = decide(app, case_id, action="approve", evidence_reviewed=True)
    assert r["status"] == "notified"


def test_safe_draft_replaces_a_promise():
    fallback = render("investigator.draft.generic", "es", "MX", case_id="EV-X", expected_date="mañana")
    text, replaced = stub.safe_draft("Te vamos a hacer el reembolso mañana", fallback)
    assert replaced and text == fallback and forbidden_hits(text) == []
    assert stub.safe_draft("Revisamos tu caso", fallback) == ("Revisamos tu caso", False)


@pytest.mark.parametrize("language,country", [("es", "MX"), ("es", "CO"), ("es", "AR"), ("pt", "AR")])
def test_every_investigator_draft_is_free_of_promises(language, country):
    variables = dict(case_id="EV-X", amount="1 MXN", merchant="M", date="hoy", expected_date="mañana",
                     fee_name="F", fee_amount="2 MXN")
    for scenario in stub.SCENARIOS:
        text = render(f"investigator.draft.{scenario}", language, country, **variables)
        assert forbidden_hits(text) == [], (scenario, text)


def test_tool_budget_is_enforced():
    run = stub._Run(gw=demo_gateway, ctx=None, sleep=lambda s: None, on_tool=None)
    for _ in range(stub.MAX_TOOL_CALLS):
        run.call("x", lambda: 1, "x")
    with pytest.raises(stub.ToolBudgetExceeded):
        run.call("x", lambda: 1, "x")


# ================================================================ analyst decision

def test_lucia_approve_notifies_with_a_stamped_spanish_resolution(app):
    conv, case_id = lucia_b(app)
    d = detail(app, case_id)
    assert d["allowed_actions"] == ["approve", "edit", "reject"] and d["version"] >= 1
    draft = d["report"]["draft_reply"]["text"]
    r = decide(app, case_id, action="approve", labels={"intent_confirmed": True})
    assert r["status"] == "notified" and r["analyst_decision"]["decided_by"] == "analista-local"
    assert r["labels_emitted"] == ["decision:approve", "recommendation:request_information:accepted",
                                   "lane:B:confirmed", "reply:as_drafted", "intent:dispute_charge:confirmed"]
    # A customer who does not read GET /cases (e.g. WhatsApp): the next chat turn says the
    # resolution once, then not again.
    r1 = conv.say("¿cómo va mi caso?")
    assert r1["reply_text"].startswith(f"Novedades de tu caso {case_id}: {draft}")
    res = r1["case_card"]["resolution"]
    r2 = conv.say("¿cómo va mi caso?")
    assert "Novedades" not in r2["reply_text"]
    view = app.get_case(conv.token, case_id)
    assert view["case"]["resolution"] == res
    assert view["case"]["status"] == "notified" and view["case"]["lifecycle_step"] == "notified"
    assert view["poll_after_ms"] is None and view["case"]["outcome"] == "approve"
    assert res["language"] == "es" and res["approved_by_human"] is True
    assert res["text"] == f"{draft} {STAMP_ES}" and "ningún dinero se mueve" in res["text"]
    stored = app.stores.cases.get(case_id).case
    assert stored.analyst_decision.draft_text == draft and stored.analyst_decision.sent_text == res["text"]
    assert "analista-local" not in json.dumps(view)  # who decided is for the console, not the customer


def test_joao_edit_in_portuguese(app):
    conv, case_id = joao_b(app)
    text = "Olá, João. Revisamos a tarifa de manutenção e o caso segue com a equipe de tarifas."
    r = decide(app, case_id, action="edit", reply={"language": "pt", "text": text}, reason="tom mais curto",
               labels={"intent_class": "dispute_fee"})
    assert r["status"] == "notified" and r["analyst_decision"]["edited"] is True
    assert "recommendation:reverse_fee:edited" in r["labels_emitted"] and "reply:edited" in r["labels_emitted"]
    assert "intent:dispute_fee:confirmed" in r["labels_emitted"]
    reply = conv.say("Oi, alguma novidade?")  # before any GET /cases: the chat says it once
    assert reply["reply_text"].startswith(f"Novidades do seu caso {case_id}:")
    res = app.get_case(conv.token, case_id)["case"]["resolution"]
    assert res["language"] == "pt" and res["text"] == f"{text} {STAMP_PT}"


def test_edit_that_already_ends_with_the_stamp_is_not_stamped_twice(app):
    _, case_id = lucia_b(app)
    decide(app, case_id, action="edit", reply={"language": "es", "text": f"Hola. {STAMP_ES}"}, reason="x")
    assert app.stores.cases.get(case_id).case.resolution.text == f"Hola. {STAMP_ES}"


def test_reject_escalate_goes_to_the_senior_queue_without_sending_anything(app):
    conv, case_id = andres_b(app)
    r = decide(app, case_id, action="reject", reason="posible fraude, revisar", next="escalate",
               labels={"intent_class": "lost_card"})
    assert r["status"] == "handed_off"
    predicted = app.stores.cases.get(case_id).case.intent.class_
    assert predicted != "lost_card"
    assert r["labels_emitted"] == ["decision:reject", "recommendation:open_chargeback:rejected", "lane:B:escalated",
                                   f"intent:{predicted}:corrected_to:lost_card"]
    view = app.get_case(conv.token, case_id)["case"]
    assert view["resolution"] is None and view["lifecycle_step"] == "in_review" and view["handoff_queue"] is None
    case = app.stores.cases.get(case_id).case
    assert case.lane == Lane.B and case.handoff.queue == "senior"
    item = next(i for i in app.analyst.list_cases(CLAIMS, {"status": "handed_off"})["items"] if i["case_id"] == case_id)
    assert item["queue"] == "senior"


def test_reject_request_information_sends_the_template_or_the_analyst_question(app):
    conv, case_id = overcharge_b(app)
    r = decide(app, case_id, action="reject", reason="falta el ticket", next="request_information")
    assert r["status"] == "notified" and "reply:template" in r["labels_emitted"]
    res = app.get_case(conv.token, case_id)["case"]["resolution"]
    expected = render("request_information_default", "es", "MX", case_id=case_id)
    assert res["text"] == f"{expected} {STAMP_ES}"
    conv2, case2 = sofia_b(app)
    decide(app, case2, action="reject", reason="x", next="request_information",
           reply={"language": "es", "text": "¿Nos confirma si pidió comida el 9 de junio?"})
    assert app.stores.cases.get(case2).case.resolution.text.startswith("¿Nos confirma si pidió comida")


def test_decided_by_is_the_jwt_sub_and_never_the_body(app):
    _, case_id = lucia_b(app)
    d = detail(app, case_id)
    body = {"client_decision_id": "d1", "version": d["version"], "action": "approve", "decided_by": "otro"}
    expect_failure("invalid_request", app.analyst.decide, CLAIMS, case_id, body)
    r = app.analyst.decide({"sub": "cognito-sub-123"}, case_id,
                           {"client_decision_id": "d2", "version": d["version"], "action": "approve"})
    assert r["analyst_decision"]["decided_by"] == "cognito-sub-123"
    assert detail(app, case_id)["case"]["analyst_decision"]["decided_by"] == "cognito-sub-123"


def test_conflicts_stale_version_already_decided_and_idempotent_retry(app):
    _, case_id = lucia_b(app)
    v = detail(app, case_id)["version"]
    expect_failure("conflict", app.analyst.decide, CLAIMS, case_id,
                   {"client_decision_id": "old", "version": v - 1, "action": "approve"})
    body = {"client_decision_id": "same-id", "version": v, "action": "approve"}
    first = app.analyst.decide(CLAIMS, case_id, body)
    assert app.analyst.decide(CLAIMS, case_id, dict(body)) == first  # safe retry
    expect_failure("conflict", app.analyst.decide, CLAIMS, case_id, {**body, "action": "reject", "reason": "x",
                                                                     "next": "escalate"})
    e = expect_failure("conflict", app.analyst.decide, CLAIMS, case_id,
                       {"client_decision_id": "second", "version": v + 1, "action": "approve"})
    assert e.http_status == 409 and e.retryable is False
    assert len(events(app, case_id=case_id, name="analyst_decision")) == 1


def test_two_analysts_at_once_one_wins(app):
    _, case_id = lucia_b(app)
    v = detail(app, case_id)["version"]
    results = []

    def go(sub):
        try:
            results.append(app.analyst.decide({"sub": sub}, case_id,
                                              {"client_decision_id": sub, "version": v, "action": "approve"}))
        except C.ApiFailure as e:
            results.append(e.code)

    threads = [threading.Thread(target=go, args=(f"analyst-{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if isinstance(r, dict)) == 1 and results.count("conflict") == 5


def test_preconditions_are_422(clock):
    app = make_app(clock=clock, investigation_delay=4)
    _, investigating = lucia_b(app)
    expect_failure("precondition", decide, app, investigating, action="edit",
                   reply={"language": "es", "text": "hola"}, reason="x")
    assert detail(app, investigating)["allowed_actions"] == []
    app_now = make_app(clock=FakeClock(), investigation_delay=0)
    _, no_report = app_complaint_b(app_now)
    e = expect_failure("precondition", decide, app_now, no_report, action="approve")
    assert e.http_status == 422
    _, case_id = lucia_b(app_now)
    expect_failure("precondition", decide, app_now, case_id, action="edit",
                   reply={"language": "pt", "text": "Olá"}, reason="idioma")


@pytest.mark.parametrize("body", [
    {"action": "approve", "reply": {"language": "es", "text": "x"}},
    {"action": "approve", "next": "escalate"},
    {"action": "edit", "reason": "x"},
    {"action": "reject", "reason": "x"},
    {"action": "reject", "next": "escalate", "reason": "x", "reply": {"language": "es", "text": "x"}},
    {"action": "approve", "labels": {"intent_class": "lost_card", "intent_confirmed": True}},
    {"action": "edit", "reason": "x", "reply": {"language": "es", "text": "x" * 2001}},
    {"action": "approve", "customer_id": "DEMO-C-0001"},
])
def test_malformed_decisions_are_400(app, body):
    _, case_id = lucia_b(app)
    v = detail(app, case_id)["version"]
    expect_failure("invalid_request", app.analyst.decide, CLAIMS, case_id,
                   {"client_decision_id": uuid.uuid4().hex, "version": v, **body})


def test_audit_event_is_human_with_hashes_and_no_free_text(app):
    _, case_id = joao_b(app)
    reason = "El cliente mencionó otra cosa en la llamada"
    text = "Olá, João. Texto novo para o cliente."
    decide(app, case_id, action="edit", reply={"language": "pt", "text": text}, reason=reason)
    audit = events(app, case_id=case_id, name="analyst_decision")
    assert len(audit) == 1 and audit[0]["actor"] == "human"
    out = audit[0]["output_ref"]
    case = app.stores.cases.get(case_id).case
    assert "action:edit" in out and f"reason_sha256:{short_hash(reason)}" in out
    assert f"draft_sha256:{short_hash(case.analyst_decision.draft_text)}" in out
    assert f"sent_sha256:{short_hash(case.resolution.text)}" in out
    assert "by:analista-local" in audit[0]["input_ref"]
    everything = json.dumps(app.stores.traces.events, ensure_ascii=False)
    assert reason not in everything and text not in everything
    assert case.analyst_decision.reason == reason  # the text stays in the case (audit record)
    transitions = [e["output_ref"] for e in events(app, case_id=case_id, name="lifecycle.transition")]
    assert transitions[-1] == "status:notified"


def test_without_claims_every_analyst_call_is_401(app):
    _, case_id = lucia_b(app)
    for claims in (None, {}, {"email": "x@y"}, {"sub": ""}, {"sub": 12}):
        expect_failure("session_expired", app.analyst.list_cases, claims, {})
        expect_failure("session_expired", app.analyst.get_detail, claims, case_id)
        e = expect_failure("session_expired", app.analyst.decide, claims, case_id,
                           {"client_decision_id": "x", "version": 1, "action": "approve"})
        assert e.http_status == 401
    assert app.stores.cases.get(case_id).case.analyst_decision is None


def test_unknown_and_lane_a_cases_are_the_same_403(app):
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    conv.press("confirm")
    r = conv.press("confirm")  # lane A, resolved in contact
    a_case = r["case_card"]["case_id"]
    e1 = expect_failure("not_authorized", app.analyst.get_detail, CLAIMS, a_case)
    e2 = expect_failure("not_authorized", app.analyst.get_detail, CLAIMS, "EV-ZZZZZZZZ")
    assert e1.message == e2.message and e1.http_status == 403
    assert all(i["case_id"] != a_case for i in app.analyst.list_cases(CLAIMS, {})["items"])


def test_analyst_detail_never_carries_the_customer_id_or_contact_data(app):
    ids = [f(app)[1] for f in (lucia_b, andres_b, joao_b, martina_c, app_complaint_b)]
    for case_id in ids:
        d = detail(app, case_id)
        C.AnalystCaseDetail.model_validate(d)
        text = json.dumps(d, ensure_ascii=False)
        assert "DEMO-C-" not in text and "customer_id" not in text
        assert set(d["context"]["profile"]) == {"display_name", "country", "segment", "language"}


def test_blocked_card_of_the_chat_shows_in_the_analyst_context(app):
    conv, case_id = martina_c(app)
    conv.press("confirm")  # yes, block it
    assert detail(app, case_id)["context"]["cards"] == [{"card_last4": "7730", "status": "blocked"}]


# ================================================================ analyst queue

def _many_cases(app):
    ids = [lucia_b(app)[1], andres_b(app)[1], joao_b(app)[1], martina_c(app)[1], app_complaint_b(app)[1],
           overcharge_b(app)[1], sofia_b(app)[1]]
    return ids


def test_queue_filters_order_and_cursor_pages(app, clock):
    ids = []
    for flow in (lucia_b, andres_b, joao_b, martina_c, app_complaint_b, overcharge_b, sofia_b):
        ids.append(flow(app)[1])
        clock.advance(minutes=7)
    full = app.analyst.list_cases(CLAIMS, {"limit": "50"})
    assert {i["case_id"] for i in full["items"]} == set(ids) and full["next_cursor"] is None
    assert full["poll_after_ms"] == C.ANALYST_QUEUE_POLL_MS == 15000
    # CX-03: priority high first (Martina, fraud), then breach_at ascending.
    keys = [(i["priority"] != "high", i["breach_at"] is None, i["breach_at"] or "") for i in full["items"]]
    assert keys == sorted(keys) and full["items"][0]["case_id"] == ids[3]
    seen, cursor = [], None
    while True:
        page = app.analyst.list_cases(CLAIMS, {"limit": "2", **({"cursor": cursor} if cursor else {})})
        assert len(page["items"]) <= 2
        seen += [i["case_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [i["case_id"] for i in full["items"]]
    pt = app.analyst.list_cases(CLAIMS, {"language": "pt"})["items"]
    assert [i["case_id"] for i in pt] == [ids[2]]
    b_awaiting = app.analyst.list_cases(CLAIMS, {"lane": "B", "status": "awaiting_analyst"})["items"]
    assert len(b_awaiting) == 6 and all(i["lane"] == "B" for i in b_awaiting)


@pytest.mark.parametrize("query", [
    {"limit": "0"}, {"limit": "51"}, {"limit": "diez"}, {"status": "lost"}, {"lane": "D"},
    {"customer_id": "DEMO-C-0001"}, {"cursor": "no-es-un-cursor"},
])
def test_bad_queue_queries_are_400(app, query):
    lucia_b(app)
    expect_failure("invalid_request", app.analyst.list_cases, CLAIMS, query)


def test_a_cursor_only_works_with_its_own_filters(app):
    for flow in (lucia_b, andres_b, overcharge_b):
        flow(app)
    page = app.analyst.list_cases(CLAIMS, {"limit": "1", "lane": "B"})
    assert page["next_cursor"]
    expect_failure("invalid_request", app.analyst.list_cases, CLAIMS, {"limit": "1", "cursor": page["next_cursor"]})


# ================================================================ judge-mode switches (RF-22)

def test_tools_down_switch_only_request_then_lane_c_tool_failure(app):
    conv = Conv(app, "lucia")
    welcome_buttons = conv.last["buttons"]
    r = app.chat(conv.token, {"client_msg_id": "sw-1", "demo_switches": {"tools_down": True}})
    assert r["turn"] == 0 and r["buttons"] == [] and r["degraded"] == [] and r["case_card"] is None
    assert r["demo_switches"] == {"tools_down": True, "model_slow": False, "fast_clock": False}
    assert r["reply_text"] == render("switch.tools_down_on", "es")
    assert r["trace_summary"]["steps"][0]["name"] == "demo_switches" and r["trace_summary"]["rule_id"] == "demo_switches"
    assert app.chat(conv.token, {"client_msg_id": "sw-1", "demo_switches": {"tools_down": True}}) == r  # idempotent
    assert demo_gateway.tool_failure_on(conv.session_id)
    conv.last = {"buttons": welcome_buttons}  # the welcome buttons are still valid
    r = conv.press(label=welcome_buttons[0]["label"])  # "no reconozco un cargo": first tool call fails
    assert r["lane"] == "C" and r["trace_summary"]["rule_id"] == "tool_failure"
    assert r["degraded"] == ["tool_unavailable"] and "evidence_incomplete" in \
        app.stores.cases.get(r["case_card"]["case_id"]).case.urgency_flags
    r = app.chat(conv.token, {"client_msg_id": "sw-2", "demo_switches": {"tools_down": False}})
    assert r["demo_switches"]["tools_down"] is False and r["turn"] == 1  # repeats the last turn number
    assert r["reply_text"] == render("switch.tools_down_off", "es")


def test_model_slow_switch_forces_the_model_timeout_path():
    sleeps = []
    app = make_app(clock=FakeClock(), sleeps=sleeps, investigation_delay=0, llm_timeout=8)
    conv = Conv(app, "lucia")
    r = app.chat(conv.token, {"client_msg_id": "m1", "message": "me cobraron como 450 en el super el 12",
                              "demo_switches": {"model_slow": True}})
    assert r["degraded"] == ["model_timeout"] and r["demo_switches"]["model_slow"] is True
    assert 8 in sleeps  # the turn waited the whole G1 timeout (fake sleep in tests)
    step = next(s for s in r["trace_summary"]["steps"] if s["name"] == "g1_extract")
    assert step["actor"] == "model" and step["error_code"] == "model_timeout"
    assert "SUPER AHORRO" in r["reply_text"]  # the regex extractor still answered
    r = conv.say("hola")
    assert r["degraded"] == ["model_timeout"] and r["demo_switches"]["model_slow"] is True
    r = app.chat(conv.token, {"client_msg_id": "m3", "demo_switches": {"model_slow": False}})
    assert r["demo_switches"]["model_slow"] is False
    assert conv.say("hola")["degraded"] == []


def test_expire_session_switch_is_401_now_and_after(app):
    conv = Conv(app, "lucia")
    session_id = conv.session_id
    demo_gateway.set_tool_failure(session_id, True)
    e = expect_failure("session_expired", app.chat, conv.token,
                       {"client_msg_id": "x1", "message": "hola", "demo_switches": {"expire_session": True}})
    assert e.http_status == 401
    expect_failure("session_expired", app.chat, conv.token, {"client_msg_id": "x2", "message": "hola"})
    assert not demo_gateway.tool_failure_on(session_id)  # the session's demo state is gone too


def test_switch_only_requests_do_not_count_for_the_turn_cap(app):
    conv = Conv(app, "lucia")
    for i in range(35):
        r = app.chat(conv.token, {"client_msg_id": f"s{i}", "demo_switches": {"model_slow": i % 2 == 0}})
        assert r["turn"] == 0
    assert conv.say("hola")["turn"] == 1


def test_switches_only_for_demo_sessions(app):
    conv = Conv(app, "lucia")
    record = app.sessions.authenticate(conv.token)
    record.source = "production"
    app.sessions.save(record)
    expect_failure("invalid_request", app.chat, conv.token, {"client_msg_id": "p1", "demo_switches": {"tools_down": True}})
    assert not demo_gateway.tool_failure_on(conv.session_id)


# ================================================================ HTTP handler

def _analyst_event(route_key, claims=CLAIMS, body=None, query=None, case_id=None):
    event = v2_event(route_key, body=body, path_params={"case_id": case_id} if case_id else None)
    if claims is not None:
        event["requestContext"]["authorizer"] = {"jwt": {"claims": claims, "scopes": None}}
    if query:
        event["queryStringParameters"] = query
    return event


def test_handler_routes_the_analyst_console_with_the_authorizer_claims(app):
    from handlers import api

    api.set_app(app)
    try:
        _, case_id = lucia_b(app)
        status, body, headers = call(_analyst_event("GET /analyst/cases", query={"lane": "B"}))
        assert status == 200 and body["items"][0]["case_id"] == case_id and headers["cache-control"] == "no-store"
        status, body, _ = call(_analyst_event("GET /analyst/cases/{case_id}", case_id=case_id))
        assert status == 200 and body["case"]["case_id"] == case_id
        payload = {"client_decision_id": "h1", "version": body["version"], "action": "approve"}
        status, out, _ = call(_analyst_event("POST /analyst/cases/{case_id}/decision", body=payload, case_id=case_id))
        assert status == 200 and out["analyst_decision"]["decided_by"] == "analista-local"
        status, out, _ = call(_analyst_event("POST /analyst/cases/{case_id}/decision", body={**payload,
                                                                                            "client_decision_id": "h2"},
                                             case_id=case_id))
        assert status == 409 and out["error"]["code"] == "conflict"
        for route in ("GET /analyst/cases", "GET /analyst/cases/{case_id}", "POST /analyst/cases/{case_id}/decision"):
            status, out, _ = call(_analyst_event(route, claims=None, case_id=case_id, body=payload))
            assert status == 401 and out["error"]["code"] == "session_expired", route
        # A header or a body can never stand in for the authorizer.
        event = _analyst_event("GET /analyst/cases", claims=None)
        event["headers"]["x-analyst-sub"] = "analista-local"
        assert call(event)[0] == 401
        status, out, _ = call(_analyst_event("GET /analyst/cases/{case_id}", case_id="EV-ZZZZZZZZ"))
        assert status == 403 and out["error"]["code"] == "not_authorized"
        status, out, _ = call(_analyst_event("GET /analyst/cases", query={"limit": "99"}))
        assert status == 400
    finally:
        api.set_app(None)


def test_src_has_no_analyst_token_bypass():
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "LOCAL_ANALYST_TOKEN" not in text, path
        assert "analista-local" not in text, path


# ================================================================ scripts/local_api.py

def _local_api():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("local_api_console", REPO / "scripts" / "local_api.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["local_api_console"] = module
    spec.loader.exec_module(module)
    return module


def test_local_token_is_read_or_generated():
    local_api = _local_api()
    env = {"LOCAL_ANALYST_TOKEN": "  secreto-demo  "}
    assert local_api.resolve_analyst_token(env) == ("secreto-demo", False)
    env = {}
    token, generated = local_api.resolve_analyst_token(env)
    assert generated and len(token) >= 24 and env["LOCAL_ANALYST_TOKEN"] == token
    assert local_api.analyst_authorized({"Authorization": f"Bearer {token}"}, token)
    assert not local_api.analyst_authorized({"Authorization": f"Bearer {token}x"}, token)
    assert not local_api.analyst_authorized({"Authorization": token}, token)
    assert not local_api.analyst_authorized({}, token)
    assert not local_api.analyst_authorized({"Authorization": "Bearer "}, None)


def test_local_server_imitates_the_jwt_authorizer(tmp_path, clock):
    import urllib.error
    import urllib.request

    from handlers import api

    local_api = _local_api()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    app = make_app(clock=clock, investigation_delay=0)
    api.set_app(app)
    srv = local_api.make_server("127.0.0.1", 0, dist, analyst_token="tok-demo-123")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/api"

    def fetch(path, token=None, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body else None,
                                     method="POST" if body else "GET")
        if token:
            req.add_header("authorization", f"Bearer {token}")
        if body:
            req.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        _, case_id = lucia_b(app)
        assert fetch("/analyst/cases") == (401, {"message": "Unauthorized"})
        assert fetch("/analyst/cases", token="otro") == (401, {"message": "Unauthorized"})
        status, body = fetch("/analyst/cases?lane=B&limit=5", token="tok-demo-123")
        assert status == 200 and [i["case_id"] for i in body["items"]] == [case_id]
        status, d = fetch(f"/analyst/cases/{case_id}", token="tok-demo-123")
        status, out = fetch(f"/analyst/cases/{case_id}/decision", token="tok-demo-123",
                            body={"client_decision_id": "srv-1", "version": d["version"], "action": "approve"})
        assert status == 200 and out["analyst_decision"]["decided_by"] == "analista-local"
        assert fetch(f"/analyst/cases/{case_id}/decision",
                     body={"client_decision_id": "srv-2", "version": 1, "action": "approve"})[0] == 401
    finally:
        srv.shutdown()
        srv.server_close()
        api.set_app(None)


def test_analyst_view_carries_the_rules_version_that_chose_the_lane(clock):
    # Integration 29 sep: the console shows "rule X, version Y" next to the lane.
    from conversation import lane_rules

    app = make_app(clock=clock, investigation_delay=0)
    _, case_id = andres_b(app)
    detail = app.analyst.get_detail(CLAIMS, case_id)
    assert detail["case"]["lane_reason"] == "duplicate_not_reversed"
    assert detail["case"]["rules_version"] == lane_rules.load_rules()["version"]
    C.AnalystCaseDetail.model_validate(detail)


def test_resolution_read_by_the_live_card_is_not_repeated_in_the_chat(app):
    # Integration 29 sep: the web card polls GET /cases and shows the resolution as a human
    # message; the next chat reply must not say it again.
    conv, case_id = lucia_b(app)
    decide(app, case_id, action="approve")
    view = app.get_case(conv.token, case_id)
    assert view["case"]["resolution"]["approved_by_human"] is True
    r = conv.say("¿cómo va mi caso?")
    assert "Novedades" not in r["reply_text"]
    assert view["case"]["resolution"]["text"] not in r["reply_text"]


def test_status_after_the_resolution_does_not_repeat_the_promised_date(app):
    conv, case_id = lucia_b(app)
    decide(app, case_id, action="approve")
    app.get_case(conv.token, case_id)  # the live card already showed the resolution
    r = conv.say("¿cómo va mi caso?")
    assert f"Tu caso {case_id} está en estado" in r["reply_text"]
    assert "Fecha estimada de resolución" not in r["reply_text"]



def test_answered_cases_sort_after_the_pending_ones(app, clock):
    # Integration 29 sep: a notified case (its SLA no longer runs) must not sit on top of the
    # queue above a case that still waits for someone.
    _, first = lucia_b(app)
    clock.advance(seconds=300)
    _, second = andres_b(app)
    decide(app, first, action="approve")
    ids = [i["case_id"] for i in app.analyst.list_cases(CLAIMS, {})["items"]]
    assert ids.index(second) < ids.index(first)
    page1 = app.analyst.list_cases(CLAIMS, {"limit": "1"})
    assert page1["items"][0]["case_id"] == second
    page2 = app.analyst.list_cases(CLAIMS, {"limit": "1", "cursor": page1["next_cursor"]})
    assert page2["items"][0]["case_id"] == first
