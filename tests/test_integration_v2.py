"""The six judge-mode customers with the FULL v2 configuration (trained M1 and M2, composite gateway,
G2 investigator, mock G1): same front-end suggestions and expected lanes as the M1 build.
SYNTHETIC data only."""

from __future__ import annotations

import re

import pytest

from _support import FakeClock, reset_gateway
from conversation.app import build_app
from conversation.llm_port import MockLLMClient
from conversation.store import MemoryCaseStore, MemorySessionStore, MemoryTraceSink, Stores
from conversation.templates import forbidden_hits
from test_integration_m1 import SCRIPTS, front_customers

V2_ENV = {"STAGE": "local", "GATEWAY_BACKEND": "composite", "M1_BACKEND": "model", "M2_BACKEND": "model",
          "INVESTIGATOR_BACKEND": "g2", "LLM_PROVIDER": "mock"}


@pytest.fixture(autouse=True)
def _clean_gateway():
    reset_gateway()
    yield
    reset_gateway()


def v2_app():
    stores = Stores(MemorySessionStore(), MemoryCaseStore(), MemoryTraceSink(), "memory")
    return build_app(env=V2_ENV, clock=FakeClock(), stores=stores, sleep=lambda s: None, llm=MockLLMClient(),
                     use_env_llm=False, investigation_delay=0)


@pytest.mark.parametrize("key", list(SCRIPTS))
def test_demo_customers_reach_their_lane_with_v2(key):
    front = front_customers()[key]
    app = v2_app()
    conv, final = SCRIPTS[key](app, front["suggestions"])
    card = final["case_card"]
    assert card is not None and re.fullmatch(r"EV-[A-Z0-9]{8}", card["case_id"])
    assert final["lane"] == front["expectedLane"] == card["lane"], (key, final["lane"])
    assert card["lane_reason_code"] == front["expectedRule"], (key, card["lane_reason_code"])
    for r in [conv.session["welcome"]] + conv.replies:
        assert forbidden_hits(r["reply_text"]) == [], r["reply_text"]
    # the trace says which models answered
    versions = {s.get("version") for r in conv.replies for s in r["trace_summary"]["steps"] if s["actor"] == "model"}
    assert any(v and v.startswith("m1-") for v in versions) or key in ("carlos",)


def test_lane_b_money_case_gets_a_g2_report_with_valid_citations():
    app = v2_app()
    front = front_customers()["andres"]
    conv, final = SCRIPTS["andres"](app, front["suggestions"])
    case_id = final["case_card"]["case_id"]
    app.lifecycle.advance(case_id)
    rep = app.stores.reviews.get_report(case_id)
    assert rep is not None and rep["citations_valid"] and rep["recommendation"] == "open_chargeback"
    assert rep["model_id"].startswith("policy-") or not rep["model_id"].startswith("stub-")
