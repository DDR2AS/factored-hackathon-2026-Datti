"""G2 investigator: agent loop with the deterministic policy, budget, citation check, fake model,
refused records, and the app wiring (INVESTIGATOR_BACKEND=g2). SYNTHETIC data only."""

from __future__ import annotations

import uuid

import pytest

from _support import Conv, FakeClock
from conversation.app import build_app
from conversation.store import MemoryCaseStore, MemorySessionStore, MemoryTraceSink, Stores
from investigator import agent
from investigator.tools import HYPOTHESES, RECOMMENDATIONS


def _app(env_extra=None):
    stores = Stores(MemorySessionStore(), MemoryCaseStore(), MemoryTraceSink(), "memory")
    env = {"STAGE": "local", "INVESTIGATOR_BACKEND": "g2", "LLM_PROVIDER": "mock", **(env_extra or {})}
    return build_app(env=env, clock=FakeClock(), stores=stores, sleep=lambda s: None, use_env_llm=False,
                     investigation_delay=0)


def _open_case(app, key, message, labels):
    conv = Conv(app, key)
    r = conv.send(message)
    for label in labels:
        b = next(b for b in r["buttons"] if b["label"].startswith(label))
        r = conv.send(button_id=b["id"])
    case_id = r["case_card"]["case_id"]
    app.lifecycle.advance(case_id)
    return case_id, app.stores.reviews.get_report(case_id)


@pytest.mark.parametrize("key, message, labels, rec, top", [
    ("andres", "Me cobraron dos veces lo mismo, con minutos de diferencia", ["Es este"], "open_chargeback", "duplicate"),
    ("joao", "Olá, cobraram uma tarifa de manutenção de 12.500 pesos no dia 1 e acho que está errada", ["É esta"],
     "reverse_fee", "fee_error"),
])
def test_policy_report_on_demo_cases(key, message, labels, rec, top):
    app = _app()
    case_id, rep = _open_case(app, key, message, labels)
    assert rep is not None
    assert rep["model_id"] == "policy-g2-v1" and rep["prompt_version"] == "g2_investigator@v1"
    assert rep["recommendation"] == rec and rep["hypotheses"][0]["name"] == top
    assert rep["citations_valid"] and rep["removed_claims"] == 0
    assert 0 < rep["tool_calls"] <= 12
    assert {h["name"] for h in rep["hypotheses"]} <= set(HYPOTHESES)
    cited = {e for f in rep["findings"] for e in f["evidence_ids"]}
    assert cited and cited <= set(rep["evidence_records"])
    lang = "pt" if key == "joao" else "es"
    assert rep["draft_reply"]["language"] == lang
    # trace says it was the investigator (model actor), not the stub
    names = {(e["actor"], e["name"]) for e in app.stores.traces.events}
    assert ("model", "investigator") in names


class _FakeLLM:
    """A scripted 'model' through the #8 converse interface: calls tools, then submits a report
    that cites one real record and one invented id."""

    ProviderUnavailable = type("ProviderUnavailable", (Exception,), {})
    ModelError = type("ModelError", (Exception,), {})

    def __init__(self, charge_id):
        self.charge_id = charge_id
        self.turn = 0

    def provider_name(self):
        return "anthropic"

    def render_parts(self, prompt_id, variables):
        import llm

        return llm.render_parts(prompt_id, variables)

    def converse(self, system, messages, tools, model_role="investigator", provider=None, timeout=None):
        self.turn += 1
        if self.turn == 1:
            return {"content": [{"type": "tool_use", "id": "t1", "name": "get_transaction",
                                 "input": {"txn_id": self.charge_id}},
                                {"type": "tool_use", "id": "t2", "name": "get_transaction",
                                 "input": {"txn_id": "TX-LUC-0001"}}],  # another customer's record
                    "stop_reason": "tool_use", "model_id": "fake-model", "usage": {"tokens_in": 10, "tokens_out": 5}}
        return {"content": [{"type": "tool_use", "id": "t3", "name": "submit_report", "input": {
            "findings": [{"claim": "Cargo verificado", "evidence_ids": [self.charge_id]},
                         {"claim": "Inventado", "evidence_ids": ["TX-INVENTED"]},
                         {"claim": "Sin evidencia", "evidence_ids": []}],
            "hypotheses": [{"name": "duplicate", "p": 0.7}, {"name": "fraud", "p": 0.3}, {"name": "not_a_class", "p": 0.5}],
            "recommendation": "refund_now", "confidence": 1.4,
            "draft_reply": {"language": "es", "text": "Te devolveremos el dinero mañana."},
            "open_questions": []}}], "stop_reason": "tool_use", "model_id": "fake-model", "usage": {}}


def test_model_report_is_checked_in_code():
    app = _app()
    conv = Conv(app, "andres")
    r = conv.send("Me cobraron dos veces lo mismo, con minutos de diferencia")
    b = next(b for b in r["buttons"] if b["label"].startswith("Es este"))
    r = conv.send(button_id=b["id"])
    case_id = r["case_card"]["case_id"]
    stored = app.stores.cases.get(case_id)
    charge = stored.case.evidence.transaction["txn_id"]
    gw = app.orchestrator.gw
    ctx = gw.GatewayContext(customer_id=stored.case.customer_id, session_id=stored.session_id, trace_id="t")
    fake = _FakeLLM(charge)
    rep = agent.run(stored.case, gw, ctx, FakeClock(), sleep=lambda s: None, provider="anthropic", llm_module=fake)
    assert rep["removed_claims"] == 2 and rep["findings"][0]["evidence_ids"] == [charge]
    assert rep["recommendation"] == "escalate"  # invalid recommendation replaced
    assert [h["name"] for h in rep["hypotheses"]] == ["duplicate", "fraud"]
    assert rep["confidence"] == 1.0
    assert "devolver" not in rep["draft_reply"]["text"].lower()  # promise replaced by the neutral draft
    assert any("otro cliente" in q for q in rep["open_questions"])  # refused record noted


def test_budget_is_enforced():
    app = _app({"INVESTIGATOR_MAX_TOOL_CALLS": "3"})
    case_id, rep = _open_case(app, "andres", "Me cobraron dos veces lo mismo, con minutos de diferencia", ["Es este"])
    assert rep is not None and rep["tool_calls"] <= 3


def test_tools_down_gives_no_report():
    from conversation import demo_gateway

    clock = FakeClock()
    stores = Stores(MemorySessionStore(), MemoryCaseStore(), MemoryTraceSink(), "memory")
    app = build_app(env={"STAGE": "local", "INVESTIGATOR_BACKEND": "g2", "LLM_PROVIDER": "mock"}, clock=clock,
                    stores=stores, sleep=lambda s: None, use_env_llm=False, investigation_delay=5)
    conv = Conv(app, "andres")
    r = conv.send("Me cobraron dos veces lo mismo, con minutos de diferencia")
    b = next(b for b in r["buttons"] if b["label"].startswith("Es este"))
    r = conv.send(button_id=b["id"])
    case_id = r["case_card"]["case_id"]
    assert app.stores.reviews.get_report(case_id) is None  # scheduled 5 s later
    demo_gateway.set_tool_failure(None, True)
    clock.advance(seconds=10)
    try:
        app.lifecycle.advance(case_id)
    finally:
        demo_gateway.set_tool_failure(None, False)
    assert app.stores.reviews.get_report(case_id) is None
    case = app.stores.cases.get(case_id).case
    assert "evidence_incomplete" in case.urgency_flags


def test_recommendations_and_hypotheses_lists_match_the_contract():
    from conversation import contract as C
    import typing

    assert set(RECOMMENDATIONS) == set(typing.get_args(C.Recommendation))
    assert set(HYPOTHESES) == set(typing.get_args(C.HypothesisName))
