from datetime import date, timedelta
from decimal import Decimal

import pytest

from conversation import demo_gateway as g
from conversation.fx import to_usd

KEYS = ("lucia", "sofia", "andres", "joao", "martina", "carlos")


@pytest.fixture(autouse=True)
def clean_state():
    g.reset_demo_state()
    yield
    g.reset_demo_state()


def ctx(key, session="s1"):
    return g.GatewayContext(customer_id=g.customer_id_for_demo_key(key), session_id=session, trace_id="t1")


def all_txns(c):
    return g.find_candidate_txns(c, date(2020, 1, 1), date(2030, 1, 1), limit=1000)


# ---------------------------------------------------------------- data set

def test_allow_list_has_exactly_the_six_demo_customers():
    assert set(g.DEMO_KEYS) == set(KEYS)
    ids = {g.customer_id_for_demo_key(k) for k in KEYS}
    assert len(ids) == 6
    with pytest.raises(g.NotFound):
        g.customer_id_for_demo_key("DEMO-C-0001")  # a customer_id is not a demo_key
    with pytest.raises(g.NotFound):
        g.customer_id_for_demo_key("pedro")


@pytest.mark.parametrize("key", KEYS)
def test_each_customer_has_plausible_synthetic_history(key):
    c = ctx(key)
    history = g.get_txn_history(c, 3650)
    assert 20 <= len(history) <= 30
    assert all(t.synthetic for t in history)
    prof = g.get_customer_profile(c)
    assert prof.synthetic and prof.reference_date <= date(2026, 6, 17)
    assert prof.reference_date == max(t.local_date for t in history)
    assert " " not in prof.display_name  # first name only
    for t in history:
        assert t.amount_usd == to_usd(t.amount, t.currency)
        assert t.card_last4 and len(t.card_last4) == 4


def test_generation_is_deterministic():
    before = [(t.txn_id, t.local_date, t.amount, t.merchant_name) for t in all_txns(ctx("lucia"))]
    g._load.cache_clear()
    after = [(t.txn_id, t.local_date, t.amount, t.merchant_name) for t in all_txns(ctx("lucia"))]
    assert before == after


def test_demo_file_is_marked_synthetic_and_has_no_pii_fields():
    text = g.DATA_PATH.read_text(encoding="utf-8")
    assert "synthetic: true" in text
    low = text.lower()
    for word in ("document", "address", "phone", "email", "dni", "cpf", "curp", "direccion", "telefono"):
        assert f"{word}:" not in low
    assert "@" not in text


def test_background_never_competes_with_the_scenario_charge():
    for t in all_txns(ctx("lucia")):
        if t.txn_id.startswith("TX-LUC-1"):
            assert "SUPER" not in t.merchant_name
            near_day = abs((t.local_date - date(2026, 6, 12)).days) <= 3
            assert not (near_day and abs(t.amount - Decimal("449.90")) <= Decimal("67.5"))


# ---------------------------------------------------------------- scenarios

def test_lucia_scenario():
    c = ctx("lucia")
    t = g.get_transaction(c, "TX-LUC-0001")
    assert (t.local_date, t.merchant_name, t.amount, t.currency, t.status, t.fraud_score) == (
        date(2026, 6, 12), "SUPER AHORRO SA", Decimal("449.90"), "MXN", "approved", 4.0)
    assert t.card_last4 == "4821" and t.amount_usd == Decimal("24.99")
    near = g.find_candidate_txns(c, date(2026, 6, 1), date(2026, 6, 17), amount_hint="450")
    assert {"TX-LUC-0001", "TX-LUC-0002", "TX-LUC-0003", "TX-LUC-0004"} <= {x.txn_id for x in near}
    assert abs(near[0].amount - 450) <= 1
    assert g.get_prior_contacts(c) == []
    assert g.find_duplicates(c, "TX-LUC-0001") == []  # the 5 Jun charge is a week earlier


def test_joao_fee_above_schedule():
    c = ctx("joao")
    prof = g.get_customer_profile(c)
    assert (prof.language, prof.locale, prof.country, prof.currency) == ("pt", "pt-BR", "AR", "ARS")
    fee = g.get_transaction(c, "TX-JOA-0001")
    assert fee.txn_type == "fee" and fee.amount == Decimal("12500.00") and fee.local_date == date(2026, 6, 1)
    rule = g.get_fee_schedule(c, fee.product_id)
    item = g.fee_item_for_txn(rule, fee)
    assert item.amount == Decimal("8900.00") and item.amount != fee.amount
    may = g.get_transaction(c, "TX-JOA-0002")
    assert g.fee_item_for_txn(rule, may).amount == may.amount


def test_andres_duplicate_not_reversed():
    c = ctx("andres")
    dups = g.find_duplicates(c, "TX-AND-0001")
    assert [d.txn_id for d in dups] == ["TX-AND-0002"]
    assert [d.txn_id for d in g.find_duplicates(c, "TX-AND-0002")] == ["TX-AND-0001"]
    assert g.get_reversals(c, "TX-AND-0001") == [] and g.get_reversals(c, "TX-AND-0002") == []


def test_sofia_three_similar_charges_and_a_reversal():
    c = ctx("sofia")
    rows = g.find_candidate_txns(c, date(2026, 6, 8), date(2026, 6, 9))
    assert {"TX-SOF-0001", "TX-SOF-0002", "TX-SOF-0003"} <= {t.txn_id for t in rows}
    rev = g.get_reversals(c, "TX-SOF-0004")
    assert [r.txn_id for r in rev] == ["TX-SOF-0005"]
    # reversals are not candidates
    assert all(t.txn_type != "reversal" for t in all_txns(c))


def test_martina_high_fraud_score():
    t = g.get_transaction(ctx("martina"), "TX-MAR-0001")
    assert t.fraud_score == 82.0 and t.amount == Decimal("145000.00") and t.currency == "ARS"
    assert t.amount_usd == Decimal("414.29")  # below the 500 USD rule


def test_carlos_prior_complaints():
    contacts = g.get_prior_contacts(ctx("carlos"))
    assert sum(1 for x in contacts if x.contact_type == "complaint") == 3
    assert contacts[0].date >= contacts[-1].date


def test_history_window_and_baseline():
    c = ctx("martina")
    ref = g.get_customer_profile(c).reference_date
    hist = g.get_txn_history(c, 7)
    assert hist and all(ref - timedelta(days=7) < t.local_date <= ref for t in hist)
    assert hist == sorted(hist, key=lambda t: (t.local_date, t.local_time, t.txn_id), reverse=True)
    base = g.get_customer_baseline(c)
    assert base.window_to == ref - timedelta(days=14) and base.n_txns > 5
    assert base.p90_amount < Decimal("145000")  # the disputed charge is far above normal
    assert base.synthetic


def test_candidate_filters():
    c = ctx("lucia")
    assert g.find_candidate_txns(c, date(2026, 6, 12), date(2026, 6, 12), currency="COP") == []
    assert len(g.find_candidate_txns(c, date(2026, 5, 1), date(2026, 6, 17), limit=3)) == 3
    same_day = g.find_candidate_txns(c, "2026-06-12", "2026-06-12")
    assert all(t.local_date == date(2026, 6, 12) for t in same_day)


# ---------------------------------------------------------------- ownership

def test_only_own_records_are_returned():
    for key in KEYS:
        c = ctx(key)
        cid = c.customer_id
        owned = {t.txn_id for t in g._load().customers[cid].txns}
        assert {t.txn_id for t in g.get_txn_history(c, 3650)} <= owned
        assert {t.txn_id for t in all_txns(c)} <= owned


def test_other_customers_records_raise_not_owned():
    c = ctx("lucia")
    with pytest.raises(g.NotOwned):
        g.get_transaction(c, "TX-JOA-0001")
    with pytest.raises(g.NotOwned):
        g.find_duplicates(c, "TX-AND-0001")
    with pytest.raises(g.NotOwned):
        g.get_reversals(c, "TX-SOF-0004")
    with pytest.raises(g.NotOwned):
        g.get_fee_schedule(c, "DEMO-P-JOA-CA")
    with pytest.raises(g.NotOwned):
        g.block_card(c, "DEMO-K-MAR-01", confirmed_by_customer=True)
    with pytest.raises(g.NotFound):
        g.get_transaction(c, "TX-NOPE-0001")
    with pytest.raises(g.NotFound):
        g.block_card(c, "DEMO-K-NOPE", confirmed_by_customer=True)


def test_unknown_customer_in_context():
    with pytest.raises(g.NotFound):
        g.get_cards(g.GatewayContext("DEMO-C-9999", "s", "t"))


# ---------------------------------------------------------------- card block

def test_block_card_requires_explicit_yes_and_reads_back():
    c = ctx("martina")
    for not_yes in (False, None, "sí", 1):
        with pytest.raises(g.NotConfirmed):
            g.block_card(c, "DEMO-K-MAR-01", confirmed_by_customer=not_yes)
    assert g.get_cards(c)[0].status == "active"
    status = g.block_card(c, "DEMO-K-MAR-01", confirmed_by_customer=True)
    assert (status.status, status.card_last4) == ("blocked", "7730") and status.changed_at is not None
    assert g.get_cards(c)[0].status == "blocked"
    again = g.block_card(c, "DEMO-K-MAR-01", confirmed_by_customer=True)
    assert again.changed_at == status.changed_at  # idempotent


def test_block_state_is_per_session():
    g.block_card(ctx("martina", "judge-1"), "DEMO-K-MAR-01", confirmed_by_customer=True)
    assert g.get_cards(ctx("martina", "judge-2"))[0].status == "active"
    g.reset_demo_state("judge-1")
    assert g.get_cards(ctx("martina", "judge-1"))[0].status == "active"


# ---------------------------------------------------------------- failure switch

def test_failure_switch_per_session_and_global():
    g.set_tool_failure("s-bad", True)
    with pytest.raises(g.ToolUnavailable):
        g.get_cards(ctx("lucia", "s-bad"))
    with pytest.raises(g.ToolUnavailable):
        g.find_candidate_txns(ctx("lucia", "s-bad"), date(2026, 6, 1), date(2026, 6, 17))
    assert g.get_cards(ctx("lucia", "s-ok"))  # other sessions unaffected
    g.set_tool_failure("s-bad", False)
    assert g.get_cards(ctx("lucia", "s-bad"))
    g.set_tool_failure(None, True)
    for key in KEYS:
        with pytest.raises(g.ToolUnavailable):
            g.get_prior_contacts(ctx(key, "any"))
    g.set_tool_failure(None, False)
    assert g.get_prior_contacts(ctx("carlos"))


def test_txn_to_dict_is_json_ready():
    d = g.get_transaction(ctx("lucia"), "TX-LUC-0001").to_dict()
    assert d["local_date"] == "2026-06-12" and d["amount"] == "449.90" and d["amount_usd"] == "24.99"
    assert d["synthetic"] is True
    for field in ("txn_id", "product_id", "card_last4", "local_time", "currency", "merchant_id",
                  "merchant_name", "merchant_category", "status", "response_code", "fraud_score"):
        assert field in d
