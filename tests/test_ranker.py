from datetime import date, timedelta

import pytest

from conversation import demo_gateway as g
from conversation.ranker import (
    CHOOSE_THRESHOLD, CONFIRM_THRESHOLD, MODEL_VERSION, band_for, merchant_similarity, rank_candidates,
)


def candidates(key, days=45):
    c = g.GatewayContext(g.customer_id_for_demo_key(key), "s", "t")
    ref = g.get_customer_profile(c).reference_date
    return g.find_candidate_txns(c, ref - timedelta(days=days), ref)


SLOTS = dict(amount=None, currency=None, date=None, date_is_relative=None, merchant_text=None, card_last4=None)


def slots(**kw):
    return {**SLOTS, **kw}


def test_lucia_confirms_the_charge_of_the_12th():
    r = rank_candidates(slots(amount="450", date="2026-06-12", date_is_relative=True, merchant_text="super"),
                        candidates("lucia"))
    assert r["band"] == "confirm" and r["model_version"] == MODEL_VERSION == "m1-rule-0"
    assert r["ranked"][0]["txn_id"] == "TX-LUC-0001" and r["ranked"][0]["p"] >= CONFIRM_THRESHOLD
    assert r["ranked"][1]["txn_id"] == "TX-LUC-0002"  # SUPERCITO, two days earlier


def test_sofia_gets_choose_with_the_three_delivery_charges_on_top():
    r = rank_candidates(slots(amount="52000", date="2026-06-09"), candidates("sofia"))
    assert r["band"] == "choose"
    assert {x["txn_id"] for x in r["ranked"][:3]} == {"TX-SOF-0001", "TX-SOF-0002", "TX-SOF-0003"}
    assert CHOOSE_THRESHOLD <= r["ranked"][0]["p"] < CONFIRM_THRESHOLD
    r2 = rank_candidates(slots(amount="52000", date="2026-06-09", merchant_text="rappi"), candidates("sofia"))
    assert r2["band"] == "choose"


@pytest.mark.parametrize("key, s, txn_id", [
    ("joao", slots(amount="12500", date="2026-06-01"), "TX-JOA-0001"),
    ("martina", slots(amount="145000", date="2026-06-15", merchant_text="ELECTRO MUNDO"), "TX-MAR-0001"),
    ("carlos", slots(amount="1299", date="2026-06-11", merchant_text="STREAMING PLUS"), "TX-CAR-0001"),
])
def test_other_demo_customers_confirm(key, s, txn_id):
    r = rank_candidates(s, candidates(key))
    assert r["band"] == "confirm" and r["ranked"][0]["txn_id"] == txn_id


def test_identical_duplicates_share_probability():
    r = rank_candidates(slots(amount="189900", date="2026-06-14", merchant_text="tienda tecno"),
                        candidates("andres"))
    top = r["ranked"][:2]
    assert {x["txn_id"] for x in top} == {"TX-AND-0001", "TX-AND-0002"}
    assert top[0]["p"] == top[1]["p"] and r["band"] == "choose"


def test_no_candidates_or_no_slots_asks():
    assert rank_candidates(slots(amount="10"), []) == {"ranked": [], "band": "ask", "model_version": MODEL_VERSION}
    r = rank_candidates({}, candidates("lucia"))
    assert r["band"] == "ask"


def test_nothing_fits_gives_ask():
    r = rank_candidates(slots(amount="99999", date="2026-01-01", merchant_text="zapateria"), candidates("lucia"))
    assert r["band"] == "ask"


def test_probabilities_are_sorted_and_leave_room_for_none():
    r = rank_candidates(slots(amount="450", date="2026-06-12"), candidates("lucia"))
    ps = [x["p"] for x in r["ranked"]]
    assert ps == sorted(ps, reverse=True) and sum(ps) < 1.0
    assert len(r["ranked"]) == len(candidates("lucia"))


def test_accepts_mappings_and_converts_currency():
    cands = [
        {"txn_id": "a", "amount": "449.90", "currency": "MXN", "local_date": "2026-06-12", "merchant_name": "SUPER AHORRO SA"},
        {"txn_id": "b", "amount": "90.00", "currency": "MXN", "local_date": "2026-06-12", "merchant_name": "SUPER AHORRO SA"},
    ]
    r = rank_candidates(slots(amount="25", currency="USD", date="2026-06-12"), cands)
    assert r["ranked"][0]["txn_id"] == "a" and r["band"] == "confirm"


def test_card_last4_mismatch_penalizes():
    cands = [
        {"txn_id": "a", "amount": "100", "currency": "MXN", "local_date": "2026-06-12", "merchant_name": "X", "card_last4": "1111"},
        {"txn_id": "b", "amount": "100", "currency": "MXN", "local_date": "2026-06-12", "merchant_name": "X", "card_last4": "4821"},
    ]
    r = rank_candidates(slots(amount="100", card_last4="4821"), cands)
    assert r["ranked"][0]["txn_id"] == "b"


def test_merchant_similarity():
    assert merchant_similarity("super", "SUPER AHORRO SA") == 1.0
    assert merchant_similarity("súper", "SUPER AHORRO SA") == 1.0
    assert 0.5 < merchant_similarity("super", "SUPERCITO EXPRESS") < 1.0
    assert merchant_similarity("super", "OXXO 1123") == 0.0
    assert merchant_similarity("", "OXXO") == 0.0


def test_band_boundaries():
    assert band_for(0.85) == "confirm"
    assert band_for(0.8499) == "choose"
    assert band_for(0.40) == "choose"
    assert band_for(0.3999) == "ask"
    assert band_for(None) == "ask"


def test_deterministic():
    s = slots(amount="450", date=date(2026, 6, 12), merchant_text="super")
    assert rank_candidates(s, candidates("lucia")) == rank_candidates(s, candidates("lucia"))
