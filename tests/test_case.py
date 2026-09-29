from datetime import date, datetime, timezone

import pytest

from conversation.case import (
    CaseEvidence,
    CaseRecord,
    Intent,
    Lane,
    Match,
    MatchCandidate,
    Promise,
    RiskEvidence,
    customer_view,
    new_case_id,
)


def make_case(**overrides):
    base = dict(
        case_id=new_case_id(),
        created_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc),
        channel="app",
        language="es",
        customer_id="CUST-SECRET-1",
        subcategory="Cargo no reconocido",
        lane=Lane.B,
        lane_reason="unrecognized_low_risk",
        evidence=CaseEvidence(
            transaction={
                "txn_id": "TXN-9",
                "local_date": "2026-06-12",
                "amount": "449.90",
                "currency": "MXN",
                "merchant_name": "SUPER AHORRO SA",
                "card_last4": "1234",
                "status": "Approved",
                "fraud_score": 3,
                "merchant_id": "M-77",
            }
        ),
        promise=Promise(expected_date=date(2026, 10, 15), sla_days=15, p90_days=28),
        match=Match(
            candidates=[MatchCandidate(txn_id="TXN-9", p=0.91), MatchCandidate(txn_id="TXN-3", p=0.06)],
            chosen_id="TXN-9",
            p_top1=0.91,
            band="confirm",
            model_version="m1-test",
        ),
        intent=Intent(**{"class": "dispute_charge", "p": 0.93, "gate_action": "accept"}),
        risk_evidence=RiskEvidence(fraud_score=3, rule_fired=None),
    )
    return CaseRecord(**{**base, **overrides})


def test_case_id_is_opaque_and_readable():
    cid = new_case_id()
    assert cid.startswith("EV-") and len(cid) == 11
    assert new_case_id() != cid


def test_intent_serializes_with_the_interface_field_name():
    dumped = make_case().model_dump(mode="json", by_alias=True)
    assert dumped["intent"]["class"] == "dispute_charge"
    assert CaseRecord.model_validate(dumped) == make_case(case_id=dumped["case_id"])


def test_customer_view_hides_internal_fields():
    view = customer_view(make_case())
    flat = repr(view)
    for hidden in ("CUST-SECRET-1", "TXN-3", "fraud_score", "M-77", "dispute_charge", "0.91", "m1-test"):
        assert hidden not in flat, hidden
    assert view["charge"] == {
        "local_date": "2026-06-12",
        "amount": "449.90",
        "currency": "MXN",
        "merchant_name": "SUPER AHORRO SA",
        "card_last4": "1234",
        "status": "Approved",
    }
    assert view["expected_date"] == "2026-10-15" and view["lane"] == "B"


def test_customer_view_without_a_charge():
    view = customer_view(make_case(evidence=CaseEvidence()))
    assert view["charge"] is None


def test_unknown_fields_are_rejected():
    with pytest.raises(ValueError):
        make_case(approved_refund=True)


def test_customer_view_has_exactly_the_contract_fields():
    from conversation.case import CUSTOMER_VIEW_FIELDS

    view = customer_view(make_case())
    assert tuple(view) == CUSTOMER_VIEW_FIELDS
    assert view["synthetic"] is True
    assert view["language"] == "es"
    assert view["lane_reason_code"] == "unrecognized_low_risk"
    assert view["handoff_queue"] is None  # only lane C has a queue for the customer


def test_lane_a_and_lane_c_statuses():
    from conversation.case import CaseStatus, Handoff

    a = customer_view(make_case(lane=Lane.A, lane_reason="customer_recognizes",
                                status=CaseStatus.resolved_in_contact))
    assert a["status"] == "resolved_in_contact" and a["lane"] == "A"
    c = customer_view(make_case(lane=Lane.C, lane_reason="high_fraud_score", language="pt",
                                status=CaseStatus.handed_off,
                                handoff=Handoff(queue="fraud", priority="high",
                                                facts_verified=["txn TXN-9"], open_questions=["q"])))
    assert c["status"] == "handed_off"
    assert c["handoff_queue"] == "equipe de Fraudes"  # label in the case language, not the id (no article)
    flat = repr(c)
    for hidden in ("TXN-9", "'high'", "priority", "facts_verified", "CUST-SECRET-1"):
        assert hidden not in flat, hidden
