"""Gateway backend ``dynamodb`` (src/cloud/dynamodb_gateway.py) against moto, over items built with the
real serving encoder (pipeline/serving.py). Offline, SYNTHETIC data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import gateway
from cloud.dynamodb_gateway import DynamoDBGateway
from gateway import GatewayContext, NotConfirmed, NotFound, NotOwned, ToolUnavailable
from gateway import state as gw_state

from test_cloud_support import SERVING, aws, client, create_serving_table, load_serving  # noqa: F401


@pytest.fixture
def serving(aws):  # noqa: F811
    ddb = client("dynamodb")
    create_serving_table(ddb)
    load_serving(ddb)
    gw_state.reset()
    yield ddb
    gw_state.reset()


@pytest.fixture
def gw(serving):
    return DynamoDBGateway(SERVING, client=serving)


CTX = GatewayContext(customer_id="CUS-1", session_id="S-1", trace_id="tr-1")


def ids(txns):
    return [t.txn_id for t in txns]


def test_profile_products_and_reference_date(gw):
    p = gw.get_customer_profile(CTX)
    assert (p.display_name, p.country, p.currency, p.reference_date) == ("Lucía", "CO", "COP", date(2026, 6, 17))
    assert [x["product_id"] for x in p.products] == ["PRD-1", "PRD-ACC1"]
    assert p.products[0]["last4"] == "4242" and p.synthetic is True
    assert gw.profile_for_customer_id("CUS-1").display_name == "Lucía"
    assert gw.profile_for_customer_id("CUS-NOPE") is None
    with pytest.raises(NotFound):
        gw.get_customer_profile(GatewayContext(customer_id="CUS-NOPE", session_id="S-1", trace_id="t"))
    with pytest.raises(NotFound):
        gw.customer_id_for_demo_key("lucia")  # judge-mode customers live in the demo backend only
    assert gw.data_version() == f"serving:{SERVING}"


def test_candidates_leave_out_declined_and_reversals_and_rank_by_amount(gw):
    found = gw.find_candidate_txns(CTX, date(2026, 6, 1), date(2026, 6, 17))
    assert ids(found) == ["TXN-2", "TXN-1", "TXN-3", "FEE-1"]  # newest first; TXN-4 declined, REV-TXN-3 out
    near = gw.find_candidate_txns(CTX, "2026-06-01", "2026-06-17", amount_hint="52000")
    assert near[0].txn_id == "TXN-3"
    assert gw.find_candidate_txns(CTX, date(2026, 6, 1), date(2026, 6, 17), currency="usd") == []
    t = gw.get_transaction(CTX, "TXN-1")
    assert (t.amount, t.amount_usd, t.local_date, t.local_time) == (Decimal("450000.00"), Decimal("112.50"),
                                                                   date(2026, 6, 12), "18:20")
    assert isinstance(t.fraud_score, float) and t.txn_type == "purchase"
    assert ids(gw.get_txn_history(CTX, 30)) == ["TXN-2", "TXN-1", "TXN-4", "TXN-3", "REV-TXN-3", "FEE-1"]


def test_duplicates_reversals_fees_baseline_contacts_merchant(gw):
    assert ids(gw.find_duplicates(CTX, "TXN-1")) == ["TXN-2"]
    rev = gw.get_reversals(CTX, "TXN-3")
    assert ids(rev) == ["REV-TXN-3"] and rev[0].amount == Decimal("-52000.00") and rev[0].reversal_of == "TXN-3"
    assert gw.get_reversals(CTX, "TXN-1") == []
    fee = gw.get_fee_schedule(CTX, "PRD-ACC1")
    assert fee.fees[0].amount == Decimal("9900.00") and fee.fees[0].merchant_id == "M-COMISION"
    with pytest.raises(NotFound):
        gw.get_fee_schedule(CTX, "PRD-9")  # another customer's product
    b = gw.get_customer_baseline(CTX)
    assert b.n_txns == 40 and b.usual_hours == (8, 21) and b.median_amount == Decimal("80000.00")
    assert b.category_stats == (("groceries", 30, 20.0, 95.0),) and b.known_merchant_ids == ("M-SUPER AHORRO",)
    assert [c.contact_id for c in gw.get_prior_contacts(CTX)] == ["CMP-2", "CMP-1"]  # 365 days, newest first
    m = gw.get_merchant_stats(CTX, "M-SUPER AHORRO")
    assert (m.n_txns, m.fraud_rate, m.dispute_rate) == (1852, 0.006, None)
    with pytest.raises(NotFound):
        gw.get_merchant_stats(CTX, "M-NOPE")
    assert gw.get_digital_sessions(CTX, "2026-06-12T18:00:00", 30) == []  # not in the serving export


def test_another_customers_transaction_is_not_owned_and_never_read(gw, serving):
    with pytest.raises(NotOwned):
        gw.get_transaction(CTX, "TXN-9")
    for tool in (gw.find_duplicates, gw.get_reversals, gw.get_risk_evidence):
        with pytest.raises(NotOwned):
            tool(CTX, "TXN-9")
    with pytest.raises(NotFound):
        gw.get_transaction(CTX, "TXN-NOPE")
    other = GatewayContext(customer_id="CUS-2", session_id="S-2", trace_id="t")
    with pytest.raises(NotOwned):
        gw.get_transaction(other, "TXN-1")
    assert gw.get_transaction(other, "TXN-9").merchant_name == "OTRA TIENDA"
    # without the keys-only index the answer is NotFound (the API answers both with the same 403)
    no_index = DynamoDBGateway(SERVING, client=serving, txn_index=None)
    with pytest.raises(NotFound) as e:
        no_index.get_transaction(CTX, "TXN-9")
    assert not isinstance(e.value, NotOwned)


def test_risk_evidence_uses_the_customers_own_history(gw):
    seen = {}

    def risk_fn(txn, baseline, **kw):
        seen.update(txn=txn.txn_id, n=baseline.n_txns, recent=[t.txn_id for t in kw["recent_txns"]], **{
            k: kw[k] for k in ("channel", "txn_country", "customer_country")})
        return {"features": {"velocity_24h": len(kw["recent_txns"])}, "lift_score": None, "model_version": "m3-test"}

    g = DynamoDBGateway(SERVING, client=gw._ddb(), risk_fn=risk_fn)
    ev = g.get_risk_evidence(CTX, "TXN-2")
    assert ev.features == {"velocity_24h": 1} and ev.model_version == "m3-test" and ev.rule_fired is False
    assert seen == {"txn": "TXN-2", "n": 40, "recent": ["TXN-1"], "channel": "POS", "txn_country": "CO",
                    "customer_country": "CO"}
    default = gw.get_risk_evidence(CTX, "TXN-1")  # the packaged M3 (pure Python) or rule-only evidence
    assert default.txn_id == "TXN-1" and isinstance(default.features, dict)


def test_cards_block_and_failure_switch(gw):
    assert [(c.card_id, c.status) for c in gw.get_cards(CTX)] == [("CARD-1", "active")]
    with pytest.raises(NotConfirmed):
        gw.block_card(CTX, "CARD-1", confirmed_by_customer=False)
    with pytest.raises(NotFound):
        gw.block_card(CTX, "CARD-9", confirmed_by_customer=True)  # another customer's card
    status = gw.block_card(CTX, "CARD-1", confirmed_by_customer=True)
    assert status.status == "blocked" and status.changed_at is not None
    assert gw.get_cards(GatewayContext(customer_id="CUS-1", session_id="S-other", trace_id="t"))[0].status == "active"
    gw.set_tool_failure("S-1", True)
    with pytest.raises(ToolUnavailable):
        gw.get_cards(CTX)
    analyst = GatewayContext.for_analyst("CUS-1", "S-1", "lc-x")
    assert gw.get_cards(analyst)  # a session switch never empties what the analyst sees
    gw.set_tool_failure("S-1", False)


def test_one_query_per_customer_while_cached_and_aws_errors_are_tool_failures(serving):
    g = DynamoDBGateway(SERVING, client=serving)
    g.get_customer_profile(CTX)
    g.find_candidate_txns(CTX, date(2026, 6, 1), date(2026, 6, 17))
    g.get_cards(CTX)
    assert g.queries == 1
    g.clear_cache()
    g.get_cards(CTX)
    assert g.queries == 2
    uncached = DynamoDBGateway(SERVING, client=serving, cache_seconds=0)
    uncached.get_cards(CTX)
    uncached.get_cards(CTX)
    assert uncached.queries == 2
    broken = DynamoDBGateway("expvivo-test-missing-table", client=serving)
    with pytest.raises(ToolUnavailable):
        broken.get_cards(CTX)


def test_make_gateway_selects_dynamodb_and_composite_with_dynamodb(serving, tmp_path):
    env = {"GATEWAY_BACKEND": "dynamodb", "TABLE_SERVING": SERVING}
    g = gateway.make_gateway(env)
    assert isinstance(g, DynamoDBGateway) and g.table == SERVING
    assert gateway.make_gateway(env) is g  # cached per process
    comp = gateway.make_gateway({"GATEWAY_BACKEND": "composite", "GATEWAY_DATA_BACKEND": "dynamodb",
                                 "TABLE_SERVING": SERVING})
    assert type(comp).__name__ == "CompositeGateway" and isinstance(comp.duck, DynamoDBGateway)
    assert comp.get_transaction(CTX, "TXN-1").merchant_name == "SUPER AHORRO"  # dataset customer -> DynamoDB
    demo_ctx = GatewayContext(customer_id=comp.customer_id_for_demo_key("lucia"), session_id="S-d", trace_id="t")
    assert comp.get_customer_profile(demo_ctx).display_name  # DEMO-* -> the packaged demo store
    assert comp.data_version().endswith(f"serving:{SERVING}")
    duck = gateway.make_gateway({"GATEWAY_BACKEND": "composite", "DATA_DIR": str(tmp_path)})
    assert type(duck.duck).__name__ == "DuckDBGateway"  # unchanged default
    with pytest.raises(ValueError):
        gateway.make_gateway({"GATEWAY_BACKEND": "composite", "GATEWAY_DATA_BACKEND": "bigquery"})
    with pytest.raises(ValueError):
        gateway.make_gateway({"GATEWAY_BACKEND": "dynamodb"})  # TABLE_SERVING missing
    for key in [k for k in gateway._cache if str(k[1]).startswith("dynamodb:") or str(tmp_path) in str(k[1])]:
        gateway._cache.pop(key, None)  # only what this test added
