"""Fourth review round (1 nov 2026): findings of the independent review of the v2 build. Each
test reproduces one finding and failed, or did not exist, before its fix. SYNTHETIC data only; no
AWS, no model.

- 3.6  A real model fails in ways the stub never did (refusal, output cut by max_tokens, HTTP 4xx,
       no submit_report). The agent now falls back to the deterministic policy in the same run and
       the local lifecycle contains anything that still escapes: the case reaches the analyst
       marked incomplete instead of staying in "investigating" for ever.
- 3.7  G1's instructions travel as the system prompt and the customer's text alone in the user
       turn; the trained M2 is the default classifier (the keyword guard alone misses most new
       manipulation attempts).
- M3   Rates in the evaluation report carry Wilson intervals: a percentile bootstrap collapses to
       [0, 0] at 0/n and to [1, 1] at n/n, which hid how little n = 3 proves.
"""

from __future__ import annotations

import pytest

import llm
from _support import Conv, FakeClock, make_app, reset_gateway
from conversation import models_port
from conversation.app import build_app
from conversation.store import MemoryCaseStore, MemorySessionStore, MemoryTraceSink, Stores
from eval.runner import wilson_ci95
from investigator import agent
from llm import prompts as llm_prompts


@pytest.fixture(autouse=True)
def _clean_gateway():
    reset_gateway()
    yield
    reset_gateway()


# ---------------------------------------------------------------- 3.6 the lifecycle contains a crashing investigator

def _open_duplicate_case(app):
    conv = Conv(app, "andres")
    conv.say("Me cobraron dos veces lo mismo en TIENDA TECNO, con minutos de diferencia")
    r = conv.press("confirm")
    assert r["trace_summary"]["rule_id"] == "duplicate_not_reversed"
    return conv, r["case_card"]["case_id"]


@pytest.mark.parametrize("exc, code", [
    (agent.InvestigatorError("stop_reason refusal"), "investigator_error"),
    (RuntimeError("kaboom"), "investigator_crash"),
])
def test_an_investigator_exception_leaves_the_case_with_the_analyst_not_in_investigating(exc, code):
    clock = FakeClock()
    app = make_app(clock=clock, investigation_delay=4)
    conv, case_id = _open_duplicate_case(app)

    def boom(*args, **kwargs):
        raise exc

    app.lifecycle.investigator = boom
    clock.advance(seconds=5)
    assert app.get_case(conv.token, case_id)["case"]["status"] == "awaiting_analyst"
    case = app.stores.cases.get(case_id).case
    assert app.stores.reviews.get_report(case_id) is None
    assert "evidence_incomplete" in case.urgency_flags
    assert any(f"no produjo reporte ({code})" in q for q in case.handoff.open_questions)
    codes = [e["error_code"] for e in app.stores.traces.events if e.get("case_id") == case_id and e.get("error_code")]
    assert codes == [code]


# ---------------------------------------------------------------- 3.6 the agent falls back to the policy instead of raising

class _ScriptedModel:
    """A 'model' through the #8 converse interface that answers from a script (an exception raises)."""

    ProviderUnavailable = type("ProviderUnavailable", (Exception,), {})

    class ModelError(Exception):
        def __init__(self, message, status=None):
            super().__init__(message)
            self.status = status

    def __init__(self, answers):
        self.answers = list(answers)
        self.timeouts: list[float | None] = []

    def provider_name(self):
        return "anthropic"

    def render_parts(self, prompt_id, variables):
        return llm.render_parts(prompt_id, variables)

    def converse(self, system, messages, tools, model_role="investigator", provider=None, timeout=None):
        self.timeouts.append(timeout)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _duplicate_case():
    stores = Stores(MemorySessionStore(), MemoryCaseStore(), MemoryTraceSink(), "memory")
    app = build_app(env={"STAGE": "local", "INVESTIGATOR_BACKEND": "g2", "LLM_PROVIDER": "mock"}, clock=FakeClock(),
                    stores=stores, sleep=lambda s: None, use_env_llm=False, investigation_delay=0)
    conv = Conv(app, "andres")
    r = conv.send("Me cobraron dos veces lo mismo, con minutos de diferencia")
    b = next(b for b in r["buttons"] if b["label"].startswith("Es este"))
    r = conv.send(button_id=b["id"])
    stored = app.stores.cases.get(r["case_card"]["case_id"])
    gw = app.orchestrator.gw
    ctx = gw.GatewayContext(customer_id=stored.case.customer_id, session_id=stored.session_id, trace_id="t")
    return stored.case, gw, ctx


def _text_turn(text):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "model_id": "fake", "usage": {}}


@pytest.mark.parametrize("answers, reason", [
    ([_ScriptedModel.ModelError("anthropic: solicitud rechazada (HTTP 400, invalid_request_error): max_tokens", status=400)],
     "ModelError HTTP 400"),
    ([{"content": [{"type": "text", "text": "Voy a"}], "stop_reason": "max_tokens", "model_id": "fake", "usage": {}}],
     "stop_reason max_tokens"),
    ([_text_turn("Voy a revisar el caso."), _text_turn("Listo, revisado.")], "sin submit_report tras el recordatorio"),
])
def test_unusable_model_answers_fall_back_to_the_policy_in_the_same_run(answers, reason):
    case, gw, ctx = _duplicate_case()
    fake = _ScriptedModel(answers)
    rep = agent.run(case, gw, ctx, FakeClock(), sleep=lambda s: None, provider="anthropic", llm_module=fake)
    assert rep["model_id"] == agent.POLICY_MODEL_ID
    assert rep["recommendation"] == "open_chargeback" and rep["citations_valid"] is True
    assert any(reason in q and "política determinista" in q for q in rep["open_questions"])
    # every model call got what was left of the run's time budget as its own timeout
    assert fake.timeouts and all(t is not None and 1.0 <= t <= agent.MAX_SECONDS for t in fake.timeouts)


# ---------------------------------------------------------------- 3.7 G1 roles and the default classifier

def test_g1_prompt_separates_instructions_from_the_customer_text():
    variables = {"text": "mi gato Bartolomeo pagó 450 en el súper", "language": "es", "reference_date": "2026-06-17"}
    version, system, user = llm.render_parts("g1_extract", variables, output_schema={"type": "object"})
    assert version == "g1_extract@v1" and system and "Reglas:" in system and "{{" not in system
    assert "Bartolomeo" not in system and system.rstrip().endswith("sin texto antes ni después.")
    assert user.startswith("Fecha de referencia del cliente: 2026-06-17") and "Reglas:" not in user
    assert "<texto_cliente>\nmi gato Bartolomeo pagó 450 en el súper\n</texto_cliente>" in user
    # the whole prompt (what the mock renders and the fixtures key on) is the concatenation, unchanged
    assert llm_prompts.render_prompt("g1_extract", variables, {"type": "object"})[1] == system + user


def test_the_trained_classifier_is_the_default_backend():
    assert type(models_port.make_classifier({})).__name__ == "ModelClassifier"
    keywords = models_port.make_classifier({"M2_BACKEND": "keywords"})
    assert keywords.classify("hola", "es")["model_version"].startswith("m2-keywords")


# ---------------------------------------------------------------- M3 Wilson intervals

@pytest.mark.parametrize("k, n, lo, hi", [
    (0, 190, 0.0, 0.0198),
    (190, 190, 0.9802, 1.0),
    (0, 3, 0.0, 0.5615),
    (79, 190, 0.3481, 0.4869),
    (0, 0, 0.0, 0.0),
])
def test_wilson_intervals_do_not_collapse_at_the_edges(k, n, lo, hi):
    got = wilson_ci95(k, n)
    assert got[0] == pytest.approx(lo, abs=0.001) and got[1] == pytest.approx(hi, abs=0.001)


# ---------------------------------------------------------------- 3.5 M2's class fires a lane only when the classifier is sure

class _DoubtfulClassifier:
    """M2 stand-in that is never confident: every message is ``human_request`` with gate ``human``
    (what the trained model answers to "quiero una segunda opinión, no estoy de acuerdo", p 0.33)."""

    MODEL_VERSION = "m2-doubtful-test"

    def classify(self, text, language=None):
        return {"intent_class": "human_request", "p": 0.33, "gate_action": "human", "runner_up": "complaint_other",
                "model_version": self.MODEL_VERSION}

    def scores(self, text):
        return {}


def test_a_low_confidence_class_never_fires_a_lane_c_rule_by_itself():
    app = make_app()
    app.orchestrator.classifier = _DoubtfulClassifier()
    conv = Conv(app, "lucia")
    r = conv.say("Hola, me salió un cobro como de 450 en el súper el 12 y no lo ubico")
    assert r["lane"] is None and r["trace_summary"]["rule_id"] != "asks_for_human"
    assert any(b["label"].startswith("Es este") for b in r["buttons"])  # the normal path went on
    # the doubt stays in the trace: the classify step shows the class and its probability
    assert any(s["name"] == "classify" for s in r["trace_summary"]["steps"])


def test_a_regulator_threat_reaches_the_regulator_rule_with_the_trained_classifier():
    """The trained M2 scores "voy a ir a la CONDUSEF" as manipulation with p 0.61 (gate ask). A
    doubt must not block the customer: the regex flag mentions_regulator still escalates to C."""
    app = make_app()  # M2_BACKEND unset = the trained model
    conv = Conv(app, "lucia")
    conv.say("Hola, me salió un cobro como de 450 en el súper el 12 y no lo ubico")
    r = conv.press("confirm")
    r = conv.say("Si no lo resuelven voy a ir a la CONDUSEF")
    assert r["lane"] == "C" and r["trace_summary"]["rule_id"] == "regulator_or_legal"
    assert "guard.manipulation" not in [s["name"] for s in r["trace_summary"]["steps"]]
