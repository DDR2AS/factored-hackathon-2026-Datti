from decimal import Decimal

import pytest

from conversation.case import Lane
from conversation.lane_rules import Evidence, _validate, evaluate, load_rules

# A verified customer with a confirmed, complete money dispute and nothing alarming.
BASE = dict(
    identity_verified=True,
    complaint_type="unrecognized_charge",
    intent_class="dispute_charge",
    evidence_complete=True,
    charge_found=True,
    charge_confirmed=True,
    amount_usd=Decimal("22.50"),
    fraud_score=5.0,
    prior_complaints=0,
)


def decide(**overrides):
    return evaluate(Evidence(**{**BASE, **overrides}))


def test_rules_file_loads_and_is_valid():
    rules = load_rules()
    assert rules["version"]
    ids = [r["id"] for r in rules["rules"]]
    assert len(ids) == len(set(ids)), "rule ids must be unique (they are the lane_reason)"


@pytest.mark.parametrize(
    "overrides, rule_id",
    [
        (dict(identity_verified=False), "identity_not_verified"),
        (dict(tool_failure=True), "tool_failure"),
        (dict(intent_class="lost_card"), "lost_card"),
        (dict(asks_for_human=True), "asks_for_human"),
        (dict(mentions_regulator=True), "regulator_or_legal"),
        (dict(prior_complaints=2), "repeat_complainer"),
        (dict(asks_compensation=True), "compensation_requested"),
        (dict(amount_usd=Decimal("500.01")), "high_amount"),
        (dict(fraud_score=31.0), "high_fraud_score"),
        (dict(complaint_about_person=True), "complaint_about_person"),
        (dict(turns_without_progress=3), "no_progress"),
    ],
)
def test_every_lane_c_trigger(overrides, rule_id):
    d = decide(**overrides)
    assert d.lane == Lane.C and d.rule_id == rule_id


def test_lane_c_boundaries_do_not_fire():
    assert decide(amount_usd=Decimal("500")).lane == Lane.B
    assert decide(fraud_score=30.0).lane == Lane.B
    assert decide(prior_complaints=1).lane == Lane.B
    assert decide(turns_without_progress=2).lane == Lane.B


def test_lane_c_wins_over_lane_a():
    d = decide(customer_recognizes=True, mentions_regulator=True)
    assert d.lane == Lane.C and d.rule_id == "regulator_or_legal"


def test_lane_c_fires_while_evidence_is_incomplete():
    d = decide(evidence_complete=False, charge_confirmed=None, mentions_regulator=True)
    assert d.lane == Lane.C


def test_missing_evidence_asks_instead_of_deciding():
    d = decide(evidence_complete=False, charge_confirmed=None)
    assert d.lane is None and d.rule_id == "missing_evidence"


def test_unverified_identity_is_the_default():
    assert evaluate(Evidence()).rule_id == "identity_not_verified"


@pytest.mark.parametrize(
    "overrides, lane, rule_id",
    [
        (dict(customer_recognizes=True), Lane.A, "customer_recognizes"),
        (dict(already_reversed=True), Lane.A, "already_reversed"),
        (dict(complaint_type="wrong_fee", fee_matches_schedule=True), Lane.A, "fee_matches_schedule"),
        (dict(complaint_type="wrong_fee", fee_matches_schedule=False), Lane.B, "fee_does_not_match"),
        (dict(duplicate_found=True, duplicate_reversed=True), Lane.A, "duplicate_already_reversed"),
        (dict(duplicate_found=True, duplicate_reversed=False), Lane.B, "duplicate_not_reversed"),
        (dict(), Lane.B, "unrecognized_low_risk"),
        (dict(complaint_type="app", charge_confirmed=None), Lane.B, "other_complaint_complete"),
    ],
)
def test_lanes_a_and_b(overrides, lane, rule_id):
    d = decide(**overrides)
    assert (d.lane, d.rule_id) == (lane, rule_id)


def test_unreversed_duplicate_beats_fee_matching_schedule():
    d = decide(complaint_type="wrong_fee", fee_matches_schedule=True, duplicate_found=True, duplicate_reversed=False)
    assert d.rule_id == "duplicate_not_reversed"


def test_unknown_values_never_match_so_nothing_is_closed_by_accident():
    # Charge disputed but never confirmed by the customer: no lane A, safe default.
    d = decide(charge_confirmed=None, customer_recognizes=True)
    assert d.lane == Lane.C and d.rule_id == "no_rule_matched"


def test_decision_carries_rules_version():
    assert decide().rules_version == load_rules()["version"]


@pytest.mark.parametrize(
    "bad_rule",
    [
        {"id": "x", "lane": "C", "when": {"amount_usdd": {"gt": 1}}, "reason": "typo"},
        {"id": "x", "lane": "C", "when": {"amount_usd": {"greater": 1}}, "reason": "bad op"},
        {"id": "x", "lane": "D", "when": {}, "reason": "bad lane"},
    ],
)
def test_typos_in_rules_fail_at_load(bad_rule):
    rules = {"version": "t", "rules": [bad_rule], "default": {"id": "d", "lane": "C", "reason": "d"}}
    with pytest.raises(ValueError):
        _validate(rules)


def test_evidence_rejects_unknown_fields():
    with pytest.raises(ValueError):
        Evidence(lane="A")


# The six prepared customers from plan v2 section 12, as evidence the orchestrator would build.
DEMO = {
    "lucia_recognizes_charge": (dict(customer_recognizes=True, amount_usd=Decimal("26.47")), Lane.A),
    "sofia_still_choosing": (dict(evidence_complete=False, charge_confirmed=None), None),
    "andres_unreversed_duplicate": (dict(duplicate_found=True, duplicate_reversed=False), Lane.B),
    "joao_fee_off_schedule": (dict(complaint_type="wrong_fee", intent_class="dispute_fee", fee_matches_schedule=False), Lane.B),
    "martina_fraud_score_82": (dict(fraud_score=82.0), Lane.C),
    "carlos_repeat_and_regulator": (dict(prior_complaints=3, mentions_regulator=True), Lane.C),
}


@pytest.mark.parametrize("name", DEMO)
def test_demo_customers_land_in_the_planned_lane(name):
    overrides, lane = DEMO[name]
    assert decide(**overrides).lane == lane


def test_explanation_not_accepted_opens_a_case_and_acceptance_changes_nothing():
    # After lane A ("¿Quedó resuelto?"): "No" gives B; "Sí" keeps the A rule that fired.
    assert decide(customer_recognizes=True, customer_accepts_explanation=False).rule_id == "explanation_not_accepted"
    assert decide(customer_recognizes=True, customer_accepts_explanation=False).lane == Lane.B
    assert decide(customer_recognizes=True, customer_accepts_explanation=True).rule_id == "customer_recognizes"
    # C triggers still win over it.
    assert decide(customer_accepts_explanation=False, asks_for_human=True).lane == Lane.C


# ---------------------------------------------------------------- purchase_amount_disputed (29 sep)

WRONG_FEE = dict(complaint_type="wrong_fee", intent_class="dispute_fee")


def test_overcharge_on_a_purchase_opens_a_case_instead_of_the_safe_default():
    d = decide(**WRONG_FEE, charge_is_fee=False)
    assert d.lane == Lane.B and d.rule_id == "purchase_amount_disputed"
    assert d.rules_version == load_rules()["version"] == "2026-09-29.2"


def test_fee_rules_still_win_for_fees_and_unknown_type_stays_the_default():
    assert decide(**WRONG_FEE, charge_is_fee=True, fee_matches_schedule=True).rule_id == "fee_matches_schedule"
    assert decide(**WRONG_FEE, charge_is_fee=True, fee_matches_schedule=False).rule_id == "fee_does_not_match"
    # A fee with no schedule entry is not a purchase: still the safe default.
    assert decide(**WRONG_FEE, charge_is_fee=True).rule_id == "no_rule_matched"
    # Unknown type (null) never matches: the safe default, as before.
    assert decide(**WRONG_FEE).rule_id == "no_rule_matched"
    # Not confirmed yet: no case.
    assert decide(**WRONG_FEE, charge_is_fee=False, charge_confirmed=None).rule_id != "purchase_amount_disputed"


def test_overcharge_on_a_purchase_keeps_the_c_triggers_and_duplicates_first():
    assert decide(**WRONG_FEE, charge_is_fee=False, fraud_score=82.0).rule_id == "high_fraud_score"
    assert decide(**WRONG_FEE, charge_is_fee=False, duplicate_found=True,
                  duplicate_reversed=False).rule_id == "duplicate_not_reversed"
    assert decide(**WRONG_FEE, charge_is_fee=False, already_reversed=True).rule_id == "already_reversed"
    # Only wrong_fee: an unrecognized purchase keeps its own rule.
    assert decide(charge_is_fee=False).rule_id == "unrecognized_low_risk"


def test_purchase_rule_sits_right_after_the_fee_rules():
    ids = [r["id"] for r in load_rules()["rules"]]
    assert ids.index("purchase_amount_disputed") == ids.index("fee_does_not_match") + 1
    assert ids.index("fee_matches_schedule") < ids.index("purchase_amount_disputed")
