"""Lane rules evaluator (owner: arturo).

Pure function: evidence in, lane decision out. The evidence comes from code (gateway results,
completeness checks, session state) and from model outputs used as data (M2 class, G1 flags);
the choice of lane is made only here, from rules/lane_rules.yaml (D2).
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from conversation.case import Lane

RULES_PATH = Path(__file__).parent / "rules" / "lane_rules.yaml"

ComplaintType = Literal["unrecognized_charge", "wrong_fee", "app", "branch", "service"]


class Evidence(BaseModel):
    """Everything the lane rules may look at. Unknown stays None and never matches a condition."""

    model_config = ConfigDict(extra="forbid")

    intent_class: str | None = None
    complaint_type: ComplaintType | None = None
    # Session and failures. Identity must be affirmatively verified.
    identity_verified: bool = False
    tool_failure: bool = False
    turns_without_progress: int = 0
    # Signals from the message (G1 flags plus pattern list) and contact history.
    asks_for_human: bool = False
    mentions_regulator: bool = False
    asks_compensation: bool = False
    complaint_about_person: bool = False
    prior_complaints: int | None = None
    # The disputed charge.
    amount_usd: Decimal | None = None
    fraud_score: float | None = None
    evidence_complete: bool = False
    charge_found: bool | None = None
    charge_confirmed: bool | None = None
    customer_recognizes: bool | None = None
    already_reversed: bool | None = None
    fee_matches_schedule: bool | None = None
    # Whether the confirmed charge is a bank fee (txn_type "fee") or a purchase. Filled by the
    # orchestrator from the gateway record, never from the message.
    charge_is_fee: bool | None = None
    duplicate_found: bool | None = None
    duplicate_reversed: bool | None = None
    # Lane A follow-up: the customer's answer to "¿Quedó resuelto?" (server-issued button).
    customer_accepts_explanation: bool | None = None


class LaneDecision(BaseModel):
    lane: Lane | None
    rule_id: str
    reason: str
    rules_version: str


_OPS = {
    "gt": lambda v, x: v > x,
    "gte": lambda v, x: v >= x,
    "lt": lambda v, x: v < x,
    "lte": lambda v, x: v <= x,
    "in": lambda v, x: v in x,
}


def _holds(value: Any, condition: Any) -> bool:
    if value is None:
        return False
    if isinstance(condition, dict):
        (op, operand), = condition.items()
        return _OPS[op](value, operand)
    return value == condition


def _validate(rules: dict) -> dict:
    """Fail at load time on typos: unknown fields, operators or lanes."""
    fields = set(Evidence.model_fields)
    lanes = {None, "A", "B", "C"}
    for rule in rules["rules"] + [rules["default"]]:
        if rule.get("lane") not in lanes:
            raise ValueError(f"rule {rule['id']}: unknown lane {rule.get('lane')!r}")
        for field, cond in rule.get("when", {}).items():
            if field not in fields:
                raise ValueError(f"rule {rule['id']}: unknown evidence field {field!r}")
            if isinstance(cond, dict) and (len(cond) != 1 or next(iter(cond)) not in _OPS):
                raise ValueError(f"rule {rule['id']}: bad condition {cond!r} on {field!r}")
    return rules


@lru_cache(maxsize=None)
def load_rules(path: Path = RULES_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return _validate(yaml.safe_load(f))


def evaluate(evidence: Evidence, rules: dict | None = None) -> LaneDecision:
    rules = rules or load_rules()
    values = evidence.model_dump()
    for rule in rules["rules"]:
        if all(_holds(values[f], c) for f, c in rule["when"].items()):
            return _decision(rule, rules["version"])
    return _decision(rules["default"], rules["version"])


def _decision(rule: dict, version: str) -> LaneDecision:
    lane = Lane(rule["lane"]) if rule["lane"] else None
    return LaneDecision(lane=lane, rule_id=rule["id"], reason=rule["reason"], rules_version=version)
