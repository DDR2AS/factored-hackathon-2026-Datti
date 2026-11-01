"""Case record (INTERFACES.md #3). Owner: arturo; storage: andres.

One document per case. Money is a decimal string plus an ISO 4217 currency; timestamps are
ISO 8601 UTC; IDs are opaque strings. What the customer may see is decided here, in
``customer_view``, and nowhere else.
"""

import secrets
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Lane(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class CaseStatus(str, Enum):
    open = "open"
    investigating = "investigating"
    awaiting_analyst = "awaiting_analyst"
    notified = "notified"
    closed = "closed"
    reopened = "reopened"
    resolved_in_contact = "resolved_in_contact"  # lane A: explained with the record, customer agreed
    handed_off = "handed_off"  # lane C: structured handoff to a person


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Promise(_Model):
    expected_date: date | None = None
    sla_days: int | None = None
    p90_days: int | None = None


class Action(_Model):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    verified_at: datetime | None = None


class Handoff(_Model):
    queue: str | None = None
    priority: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    facts_verified: list[str] = Field(default_factory=list)


class Clock(_Model):
    assigned_by: datetime | None = None
    first_response_by: datetime | None = None
    sla_alert_at: datetime | None = None
    breach_at: datetime | None = None


class CaseEvidence(_Model):
    transaction: dict[str, Any] | None = None
    product: dict[str, Any] | None = None
    app_error: dict[str, Any] | None = None


class MatchCandidate(_Model):
    txn_id: str
    p: float


class Match(_Model):
    candidates: list[MatchCandidate] = Field(default_factory=list)
    chosen_id: str | None = None
    p_top1: float | None = None
    band: Literal["confirm", "choose", "ask"] | None = None
    model_version: str | None = None


class Intent(_Model):
    class_: str = Field(alias="class")
    p: float
    gate_action: Literal["accept", "ask", "human"]
    model_version: str | None = None


class RiskEvidence(_Model):
    features: dict[str, Any] = Field(default_factory=dict)
    fraud_score: float | None = None
    rule_fired: str | None = None


class AnalystDecision(_Model):
    action: str  # approve | edit | reject
    edited: bool = False
    reason: str | None = None
    decided_at: datetime | None = None
    # v2 console additions (proposal to #3, docs/contrato_consola.md): who decided (JWT sub,
    # never from the body), what next after a reject, and the texts before and after.
    decided_by: str | None = None
    next: Literal["request_information", "escalate"] | None = None
    draft_text: str | None = None
    sent_text: str | None = None


class Resolution(_Model):
    """What the customer received after an analyst decided. Only a person approves it."""

    language: Literal["es", "pt"]
    text: str  # ends with the resolution_stamp template of the language
    sent_at: datetime
    approved_by_human: Literal[True] = True


# SLA timers of a lane B case (plan v2 sections 6 and 12). The kinds, in firing order:
# "unassigned" at clock.assigned_by (nobody took the case), "sla_80" at clock.sla_alert_at,
# "breached" at clock.breach_at. The local simulation is in lifecycle.py; in the cloud they are
# one-time EventBridge Scheduler schedules (Andrés).
SLA_TIMER_KINDS = ("unassigned", "sla_80", "breached")
SLA_TIMER_CLOCK_FIELD = {"unassigned": "assigned_by", "sla_80": "sla_alert_at", "breached": "breach_at"}


class Notice(_Model):
    """A proactive message the customer received because of the case clock (not a reply)."""

    kind: Literal["sla_80", "escalated"]
    text: str
    language: Literal["es", "pt"]
    at: datetime


class ClockEvent(_Model):
    """One SLA timer that fired (the analyst sees the list)."""

    kind: Literal["unassigned", "sla_80", "breached"]
    fired_at: datetime


class CaseRecord(_Model):
    # Base fields (v1.4)
    case_id: str
    created_at: datetime
    channel: Literal["app", "whatsapp", "web"]
    language: Literal["es", "pt"]
    customer_id: str
    segment: str | None = None
    country: str | None = None
    subcategory: str | None = None
    intent_confidence: float | None = None
    urgency_flags: list[str] = Field(default_factory=list)
    evidence: CaseEvidence = Field(default_factory=CaseEvidence)
    customer_statement: str | None = None
    lane: Lane | None = None
    lane_reason: str | None = None
    promise: Promise = Field(default_factory=Promise)
    actions: list[Action] = Field(default_factory=list)
    handoff: Handoff = Field(default_factory=Handoff)
    clock: Clock = Field(default_factory=Clock)
    status: CaseStatus = CaseStatus.open
    trace_id: str | None = None
    # v2 additions
    match: Match | None = None
    intent: Intent | None = None
    risk_evidence: RiskEvidence | None = None
    investigation_id: str | None = None
    analyst_decision: AnalystDecision | None = None
    labels_emitted: list[str] = Field(default_factory=list)
    # Console additions (proposal to #3, docs/contrato_consola.md): optimistic lock for the
    # analyst decision, last write time and the reply sent after a human decision.
    version: int = 1
    updated_at: datetime | None = None
    resolution: Resolution | None = None
    # Version of the lane rules YAML that chose ``lane``/``lane_reason`` (integration 29 sep:
    # the console shows "rule X, version Y" next to the lane).
    rules_version: str | None = None
    # Internal (never in a view): the analyst asked the customer for information (approve or
    # edit of a request_information draft, or reject + request_information). While true the
    # notified case does not close on its timer, and the customer's next message goes into
    # the case, which is reopened for the analyst (CX-01/H1). Proposal to #3.
    awaiting_customer: bool = False
    # SLA timers (proposal to #3, docs/contrato_consola.md). Visible: the notices the customer
    # got (customer view) and the timers that fired (analyst view). Internal (never in a view):
    # the kinds still scheduled, the demo time scale (1.0 = real time; fast_clock of a demo
    # session) and when an analyst took the case (opened it in the console or decided it).
    notices: list[Notice] = Field(default_factory=list)
    clock_events: list[ClockEvent] = Field(default_factory=list)
    sla_timers: list[Literal["unassigned", "sla_80", "breached"]] = Field(default_factory=list)
    timer_scale: float = 1.0
    taken_at: datetime | None = None


def new_case_id() -> str:
    """Opaque, short enough to read aloud: EV-XXXXXXXX."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "EV-" + "".join(secrets.choice(alphabet) for _ in range(8))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Only these transaction fields reach the customer: the chosen charge, never the candidates.
_CUSTOMER_TXN_FIELDS = ("local_date", "amount", "currency", "merchant_name", "card_last4", "status")

# Exactly the fields of CustomerCaseView in frontend/src/api/types.ts (tests compare them).
CUSTOMER_VIEW_FIELDS = (
    "case_id", "created_at", "status", "lane", "lane_reason_code", "subcategory", "language",
    "customer_statement", "charge", "expected_date", "first_response_by", "handoff_queue",
    "outcome", "resolution", "lifecycle_step", "notices", "synthetic",
)

# Customer-facing lifecycle step of a lane B case (LifecycleStep in types.ts).
_LIFECYCLE_STEP = {
    CaseStatus.open: "open",
    CaseStatus.reopened: "open",
    CaseStatus.investigating: "investigating",
    CaseStatus.awaiting_analyst: "in_review",
    CaseStatus.handed_off: "in_review",  # lane B only: the analyst escalated it
    CaseStatus.notified: "notified",
    CaseStatus.closed: "closed",
}


def lifecycle_step(case: CaseRecord) -> str | None:
    """Step of the lane B lifecycle the customer sees; None for lanes A and C."""
    if case.lane != Lane.B:
        return None
    return _LIFECYCLE_STEP.get(case.status)


def _resolution(case: CaseRecord) -> dict[str, Any] | None:
    r = case.resolution
    if r is None:
        return None
    return {"language": r.language, "text": r.text, "sent_at": r.sent_at.isoformat(), "approved_by_human": True}


def _queue_label(queue: str | None, language: str) -> str | None:
    """Lane C queue as the customer reads it ("equipo de Fraudes" / "a equipe de Fraudes"), in the case language.

    The internal queue id stays in ``handoff.queue``; unknown ids fall back to the id itself.
    Imported lazily so this module keeps needing only pydantic.
    """
    if not queue:
        return None
    try:
        from conversation.templates import render

        return render(f"queue.{queue}", language)
    except (KeyError, OSError, ImportError):
        return queue


def customer_view(case: CaseRecord) -> dict[str, Any]:
    """The customer-visible subset of a case (CustomerCaseView in types.ts).

    Every screen and API response uses this. ``lane_reason_code`` is the lane rule id (the UI
    translates it); rule versions, probabilities, scores and internal ids never leave here.
    """
    txn = case.evidence.transaction or {}
    return {
        "case_id": case.case_id,
        "created_at": case.created_at.isoformat(),
        "status": case.status.value,
        "lane": case.lane.value if case.lane else None,
        "lane_reason_code": case.lane_reason,
        "subcategory": case.subcategory,
        "language": case.language,
        "customer_statement": case.customer_statement,
        "charge": {k: str(txn[k]) for k in _CUSTOMER_TXN_FIELDS if txn.get(k) is not None} or None,
        "expected_date": case.promise.expected_date.isoformat() if case.promise.expected_date else None,
        "first_response_by": case.clock.first_response_by.isoformat() if case.clock.first_response_by else None,
        "handoff_queue": _queue_label(case.handoff.queue, case.language) if case.lane == Lane.C else None,
        "outcome": case.analyst_decision.action if case.analyst_decision else None,
        "resolution": _resolution(case),
        "lifecycle_step": lifecycle_step(case),
        "notices": [{"kind": n.kind, "text": n.text, "language": n.language, "at": n.at.isoformat()}
                    for n in case.notices],
        "synthetic": True,
    }


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def analyst_view(case: CaseRecord, conversation: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The analyst's subset of a case (AnalystCaseView in types.ts): every #3 field except
    ``customer_id``. #3 has no document, address or phone; nothing here may add them.
    ``conversation``: the case's redacted turns (ReviewStore.get_conversation), analyst only."""
    d = case.analyst_decision
    return {
        "case_id": case.case_id,
        "created_at": case.created_at.isoformat(),
        "updated_at": _iso(case.updated_at),
        "channel": case.channel,
        "language": case.language,
        "segment": case.segment,
        "country": case.country,
        "subcategory": case.subcategory,
        "intent_confidence": case.intent_confidence,
        "urgency_flags": list(case.urgency_flags),
        "evidence": {
            "transaction": ({k: v for k, v in case.evidence.transaction.items() if k != "synthetic"}
                            if case.evidence.transaction else None),
            "product": dict(case.evidence.product) if case.evidence.product else None,
            "app_error": dict(case.evidence.app_error) if case.evidence.app_error else None,
        },
        "customer_statement": case.customer_statement,
        "lane": case.lane.value if case.lane else None,
        "lane_reason": case.lane_reason,
        "rules_version": case.rules_version,
        "promise": {"expected_date": _iso(case.promise.expected_date), "sla_days": case.promise.sla_days,
                    "p90_days": case.promise.p90_days},
        "actions": [{"tool": a.tool, "args": dict(a.args), "result": a.result, "verified_at": _iso(a.verified_at)}
                    for a in case.actions],
        "handoff": {"queue": case.handoff.queue, "priority": case.handoff.priority,
                    "open_questions": list(case.handoff.open_questions),
                    "facts_verified": list(case.handoff.facts_verified)},
        "clock": {k: _iso(getattr(case.clock, k)) for k in ("assigned_by", "first_response_by",
                                                              "sla_alert_at", "breach_at")},
        "clock_events": [{"kind": e.kind, "fired_at": e.fired_at.isoformat()} for e in case.clock_events],
        "status": case.status.value,
        "trace_id": case.trace_id,
        "match": case.match.model_dump() if case.match else None,
        "intent": case.intent.model_dump(by_alias=True) if case.intent else None,
        "risk_evidence": case.risk_evidence.model_dump() if case.risk_evidence else None,
        "investigation_id": case.investigation_id,
        "analyst_decision": ({"action": d.action, "edited": d.edited, "reason": d.reason,
                              "decided_by": d.decided_by or "", "decided_at": _iso(d.decided_at) or ""}
                             if d else None),
        "labels_emitted": list(case.labels_emitted),
        "resolution": _resolution(case),
        "lifecycle_step": lifecycle_step(case),
        "conversation": [dict(x) for x in (conversation or [])],
        "synthetic": True,
    }
