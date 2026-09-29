"""Chat API contract (INTERFACES.md #1, owner: arturo), the customer-visible case (#3) and the
analyst console contract (docs/contrato_consola.md; the #6 report shape is Andrés's).

Python mirror of ``frontend/src/api/types.ts``, which is the source of truth. Change both
together; ``tests/test_contract.py`` reads the .ts file as text and fails on any drift in
field names or in the string unions.

Requests forbid unknown fields (a request with ``customer_id`` or ``lane`` is a 400).
Responses are built by the server and validated here before they leave, so a response that
does not match the contract is a bug caught as ``internal`` instead of a silent drift.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["es", "pt"]
Lane = Literal["A", "B", "C"]
DemoKey = Literal["lucia", "sofia", "andres", "joao", "martina", "carlos"]
Channel = Literal["web", "app", "whatsapp"]
ButtonKind = Literal["confirm", "deny", "choice", "handoff"]
ProgressStep = Literal["understand", "find", "verify", "case_open", "done"]
ComplaintType = Literal["unrecognized_charge", "wrong_fee", "app", "branch", "service"]
TraceActor = Literal["rule", "model", "tool", "human"]
ReplySource = Literal["template", "model"]
Degradation = Literal["model_timeout", "tool_unavailable"]
InputMode = Literal["free_text", "buttons_only"]
CaseStatus = Literal[
    "open", "investigating", "awaiting_analyst", "notified", "closed", "reopened",
    "resolved_in_contact", "handed_off",
]
ErrorCode = Literal[
    "invalid_request", "session_expired", "not_authorized", "conflict", "session_limit",
    "rate_limited", "tool_unavailable", "model_timeout", "not_implemented", "precondition",
    "internal",
]
LifecycleStep = Literal["open", "investigating", "in_review", "notified", "closed"]
# Analyst console
HypothesisName = Literal[
    "fraud", "forgotten_purchase", "unfamiliar_merchant_name", "duplicate", "fee_error",
    "pending_reversal",
]
Recommendation = Literal[
    "reverse_fee", "open_chargeback", "block_card", "explain_and_close", "request_information",
    "escalate",
]
EvidenceKind = Literal[
    "transaction", "reversal", "fee_schedule", "customer_baseline", "risk_evidence",
    "digital_session", "prior_contact", "merchant_stats", "card",
]
IntentClass = Literal[
    "dispute_charge", "dispute_fee", "complaint_other", "case_status", "account_query",
    "lost_card", "human_request", "out_of_scope", "manipulation",
]
Priority = Literal["high", "normal"]
GateAction = Literal["accept", "ask", "human"]
MatchBand = Literal["confirm", "choose", "ask"]
TxnType = Literal["purchase", "fee", "reversal"]
DecisionAction = Literal["approve", "edit", "reject"]
DecisionNext = Literal["request_information", "escalate"]
ConversationRole = Literal["customer", "system", "analyst"]
TurnSource = Literal["template", "model", "human"]
ContextPart = Literal["cards", "evidence_txns", "prior_contacts"]
# SLA timers (local simulation in lifecycle.py; EventBridge Scheduler in the cloud)
SlaTimerKind = Literal["unassigned", "sla_80", "breached"]
NoticeKind = Literal["sla_80", "escalated"]
Scalar = Union[str, int, float, bool, None]

MAX_MESSAGE_CHARS = 1000
MAX_REPLY_CHARS = 2000  # analyst reply (edit, or reject with request_information)
MAX_REASON_CHARS = 500
MAX_ANALYST_PAGE = 50
DEFAULT_ANALYST_PAGE = 20
ANALYST_QUEUE_POLL_MS = 15000
CASE_POLL_MS = 5000  # poll_after_ms of CaseResponse / ChatResponse while a lane B case moves
# Statuses of a lane B case in which the live card polls (poll_after_ms > 0).
POLLING_STATUSES = ("open", "investigating", "awaiting_analyst")
_CLIENT_ID = dict(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- GET /health

class HealthResponse(_Strict):
    status: Literal["ok"]
    stage: Optional[str]
    deps: Literal["ok", "missing"]
    demo_clock_scale: Optional[float]  # fast_clock factor; None without a local lifecycle


# ---------------------------------------------------------------- POST /session

class SessionRequest(_Strict):
    demo_key: DemoKey
    channel: Channel
    language: Optional[Language] = None


class SessionCustomer(_Strict):
    display_name: str
    language: Language
    locale: str
    country: Literal["MX", "CO", "AR"]


class ChatButton(_Strict):
    id: str
    label: str
    kind: ButtonKind


class ChatTurn(_Strict):
    turn: int
    reply_text: str
    reply_language: Language
    reply_source: ReplySource
    buttons: list[ChatButton]
    input_mode: InputMode


class SessionResponse(_Strict):
    session_token: str
    expires_at: str
    customer: SessionCustomer
    welcome: ChatTurn
    synthetic: Literal[True]


# ---------------------------------------------------------------- POST /chat

class DemoSwitchesPatch(_Strict):
    """Judge-mode failure switches (RF-22). Only the keys present change; see types.ts."""

    tools_down: Optional[bool] = None
    model_slow: Optional[bool] = None
    expire_session: Optional[bool] = None
    fast_clock: Optional[bool] = None

    @model_validator(mode="after")
    def _not_empty(self) -> "DemoSwitchesPatch":
        if (self.tools_down is None and self.model_slow is None and self.expire_session is None
                and self.fast_clock is None):
            raise ValueError("demo_switches needs at least one switch")
        return self


class DemoSwitchState(_Strict):
    tools_down: bool
    model_slow: bool
    fast_clock: bool


class ChatRequest(_Strict):
    client_msg_id: str = Field(**_CLIENT_ID)
    message: Optional[str] = Field(default=None, max_length=MAX_MESSAGE_CHARS)
    button_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    demo_switches: Optional[DemoSwitchesPatch] = None

    @model_validator(mode="after")
    def _one_input(self) -> "ChatRequest":
        has_message = self.message is not None
        has_button = self.button_id is not None
        if has_message and has_button:
            raise ValueError("send at most one of message or button_id")
        if not has_message and not has_button and self.demo_switches is None:
            raise ValueError("send message, button_id or demo_switches")
        if has_message and not self.message.strip():
            raise ValueError("message is empty")
        return self

    @property
    def switches_only(self) -> bool:
        """A request that only changes switches: answered without a new conversation turn."""
        return self.message is None and self.button_id is None and self.demo_switches is not None


class ClaimedSlots(_Strict):
    amount: Optional[str]
    currency: Optional[str]
    date: Optional[str]
    merchant_text: Optional[str]
    card_last4: Optional[str]


class Progress(_Strict):
    step: ProgressStep
    complaint_type: Optional[ComplaintType]
    claimed: ClaimedSlots
    missing: list[str]


class TraceStep(_Strict):
    actor: TraceActor
    name: str
    latency_ms: int
    version: Optional[str]
    error_code: Optional[str]


class TraceSummary(_Strict):
    steps: list[TraceStep]
    latency_ms: int
    cost_usd: float
    model_id: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    rule_id: Optional[str]
    rules_version: Optional[str]


class CaseResolution(_Strict):
    language: Language
    text: str
    sent_at: str
    approved_by_human: Literal[True]


class CustomerCharge(_Strict):
    local_date: str
    amount: str
    currency: str
    merchant_name: str
    card_last4: str
    status: str


class CaseNotice(_Strict):
    """A proactive message of the case clock (80 % of the SLA, escalation after a breach)."""

    kind: NoticeKind
    text: str
    language: Language
    at: str


class CustomerCaseView(_Strict):
    case_id: str
    created_at: str
    status: CaseStatus
    lane: Optional[Lane]
    lane_reason_code: Optional[str]
    subcategory: Optional[str]
    language: Language
    customer_statement: Optional[str]
    charge: Optional[CustomerCharge]
    expected_date: Optional[str]
    first_response_by: Optional[str]
    handoff_queue: Optional[str]
    outcome: Optional[str]
    resolution: Optional[CaseResolution]
    lifecycle_step: Optional[LifecycleStep]
    notices: list[CaseNotice]
    synthetic: Literal[True]


class ChatResponse(ChatTurn):
    progress: Progress
    case_card: Optional[CustomerCaseView]
    lane: Optional[Lane]
    degraded: list[Degradation]
    trace_id: str
    trace_summary: TraceSummary
    poll_after_ms: Optional[int]
    demo_switches: DemoSwitchState
    synthetic: Literal[True]


# ---------------------------------------------------------------- GET /cases/{case_id}

class CaseResponse(_Strict):
    case: CustomerCaseView
    poll_after_ms: Optional[int]


# ================================================================ analyst console
# docs/contrato_consola.md. /analyst/* is behind a Cognito JWT (API Gateway authorizer);
# decided_by comes from the JWT ``sub`` claim, never from a body.

# ---------------------------------------------------------------- #6 investigator report

class ReportFinding(_Strict):
    claim: str
    evidence_ids: list[str]


class ReportHypothesis(_Strict):
    name: HypothesisName
    p: float = Field(ge=0, le=1)


class DraftReply(_Strict):
    language: Language
    text: str


class EvidenceRecord(_Strict):
    kind: EvidenceKind
    summary: str
    fields: dict[str, Scalar]


class InvestigatorReport(_Strict):
    """INTERFACES.md #6 exactly, plus ``evidence_records`` (proposal to Andrés)."""

    report_id: str
    case_id: str
    created_at: str
    findings: list[ReportFinding]
    hypotheses: list[ReportHypothesis]
    recommendation: Recommendation
    confidence: float = Field(ge=0, le=1)
    draft_reply: DraftReply
    open_questions: list[str]
    citations_valid: bool
    removed_claims: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    model_id: str
    prompt_version: str
    evidence_records: dict[str, EvidenceRecord]

    @model_validator(mode="after")
    def _consistent(self) -> "InvestigatorReport":
        names = [h.name for h in self.hypotheses]
        if len(names) != len(set(names)):
            raise ValueError("each hypothesis name appears at most once")
        # citations_valid may never claim more than the stored records show.
        cited = {e for f in self.findings for e in f.evidence_ids}
        if self.citations_valid and not cited <= set(self.evidence_records):
            raise ValueError("citations_valid but a cited id is missing from evidence_records")
        return self


def report_reliable(report: "InvestigatorReport | dict | None") -> Optional[bool]:
    """None without report; False if citations are invalid or more than one claim was removed
    (plan section 6: the console then shows "Reporte no confiable")."""
    if report is None:
        return None
    if isinstance(report, InvestigatorReport):
        report = report.model_dump()
    return bool(report.get("citations_valid")) and int(report.get("removed_claims") or 0) <= 1


# ---------------------------------------------------------------- GET /analyst/cases

class AnalystCaseListQuery(_Strict):
    status: Optional[CaseStatus] = None
    lane: Optional[Lane] = None
    language: Optional[Language] = None
    limit: int = Field(default=DEFAULT_ANALYST_PAGE, ge=1, le=MAX_ANALYST_PAGE)
    cursor: Optional[str] = Field(default=None, min_length=1, max_length=512)


class AnalystCaseListItem(_Strict):
    case_id: str
    updated_at: str
    status: CaseStatus
    lane: Optional[Lane]
    lane_rule_id: Optional[str]
    queue: Optional[str]
    priority: Optional[Priority]
    language: Language
    country: Optional[str]
    display_name: str
    intent_class: Optional[IntentClass]
    intent_p: Optional[float]
    subcategory: Optional[str]
    breach_at: Optional[str]
    sla_alerts: list[SlaTimerKind]
    has_report: bool
    report_reliable: Optional[bool]


class AnalystCaseListResponse(_Strict):
    items: list[AnalystCaseListItem]
    next_cursor: Optional[str]
    as_of: str
    poll_after_ms: int = Field(gt=0)


# ---------------------------------------------------------------- GET /analyst/cases/{case_id}

class EvidenceTxn(_Strict):
    txn_id: str
    product_id: str
    card_last4: Optional[str]
    local_date: str
    local_time: str
    amount: str
    currency: str
    amount_usd: Optional[str]
    merchant_id: str
    merchant_name: str
    merchant_category: str
    status: str
    response_code: str
    fraud_score: Optional[float]
    txn_type: TxnType
    reversal_of: Optional[str]


class CaseEvidenceView(_Strict):
    transaction: Optional[EvidenceTxn]
    product: Optional[dict[str, Optional[str]]]
    app_error: Optional[dict[str, Scalar]]


class CasePromise(_Strict):
    expected_date: Optional[str]
    sla_days: Optional[int]
    p90_days: Optional[int]


class CaseAction(_Strict):
    tool: str
    args: dict[str, Any]
    result: Any
    verified_at: Optional[str]


class CaseHandoff(_Strict):
    queue: Optional[str]
    priority: Optional[Priority]
    open_questions: list[str]
    facts_verified: list[str]


class CaseClock(_Strict):
    assigned_by: Optional[str]
    first_response_by: Optional[str]
    sla_alert_at: Optional[str]
    breach_at: Optional[str]


class ClockEvent(_Strict):
    kind: SlaTimerKind
    fired_at: str


class MatchCandidate(_Strict):
    txn_id: str
    p: float


class CaseMatch(_Strict):
    candidates: list[MatchCandidate]
    chosen_id: Optional[str]
    p_top1: Optional[float]
    band: Optional[MatchBand]
    model_version: Optional[str]


class CaseIntent(_Strict):
    """``class`` is a Python keyword: the field is ``class_`` with alias ``class``. Validate
    and dump with the alias (``model_dump(by_alias=True)``)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_: IntentClass = Field(alias="class")
    p: float
    gate_action: GateAction
    model_version: Optional[str]


class CaseRiskEvidence(_Strict):
    features: dict[str, Any]
    fraud_score: Optional[float]
    rule_fired: Optional[str]


class AnalystDecisionView(_Strict):
    action: DecisionAction
    edited: bool
    reason: Optional[str]
    decided_by: str
    decided_at: str


class AnalystCaseView(_Strict):
    """Every #3 field except ``customer_id``; no lifecycle internals. Dump with by_alias=True."""

    case_id: str
    created_at: str
    updated_at: Optional[str]
    channel: Channel
    language: Language
    segment: Optional[str]
    country: Optional[str]
    subcategory: Optional[str]
    intent_confidence: Optional[float]
    urgency_flags: list[str]
    evidence: CaseEvidenceView
    customer_statement: Optional[str]
    lane: Optional[Lane]
    lane_reason: Optional[str]
    rules_version: Optional[str]
    promise: CasePromise
    actions: list[CaseAction]
    handoff: CaseHandoff
    clock: CaseClock
    clock_events: list[ClockEvent]
    status: CaseStatus
    trace_id: Optional[str]
    match: Optional[CaseMatch]
    intent: Optional[CaseIntent]
    risk_evidence: Optional[CaseRiskEvidence]
    investigation_id: Optional[str]
    analyst_decision: Optional[AnalystDecisionView]
    labels_emitted: list[str]
    resolution: Optional[CaseResolution]
    lifecycle_step: Optional[LifecycleStep]
    conversation: list["ConversationTurn"]
    synthetic: Literal[True]


class ConversationTurn(_Strict):
    """One redacted turn of the case's conversation (analyst only; never in a customer view)."""

    role: ConversationRole
    text: str
    language: Language
    source: TurnSource
    at: str


class AnalystProfile(_Strict):
    display_name: str
    country: Optional[str]
    segment: Optional[str]
    language: Language


class AnalystCard(_Strict):
    card_last4: str
    status: str


class PriorContact(_Strict):
    contact_id: str
    date: str
    channel: str
    contact_type: str
    subcategory: Optional[str]
    status: str


class AnalystContext(_Strict):
    profile: AnalystProfile
    cards: list[AnalystCard]
    evidence_txns: list[EvidenceTxn]
    prior_contacts: list[PriorContact]
    risk_evidence: Optional[CaseRiskEvidence]
    unavailable: list[ContextPart]


class AnalystCaseDetail(_Strict):
    case: AnalystCaseView
    report: Optional[InvestigatorReport]
    context: AnalystContext
    allowed_actions: list[DecisionAction]
    version: int = Field(ge=1)


# ---------------------------------------------------------------- POST /analyst/cases/{id}/decision

class DecisionReply(_Strict):
    language: Language
    text: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)

    @model_validator(mode="after")
    def _not_blank(self) -> "DecisionReply":
        if not self.text.strip():
            raise ValueError("reply text is empty")
        return self


class DecisionLabels(_Strict):
    intent_class: Optional[IntentClass] = None
    intent_confirmed: Optional[bool] = None

    @model_validator(mode="after")
    def _coherent(self) -> "DecisionLabels":
        if self.intent_class is not None and self.intent_confirmed is True:
            raise ValueError("a corrected intent_class cannot also be confirmed")
        return self


class DecisionRequest(_Strict):
    """Shape rules only. State rules are the server's: 409 conflict for a stale version or an
    already decided case; 422 precondition for the rest (see types.ts)."""

    client_decision_id: str = Field(**_CLIENT_ID)
    version: int = Field(ge=1)
    action: DecisionAction
    reply: Optional[DecisionReply] = None
    reason: Optional[str] = Field(default=None, max_length=MAX_REASON_CHARS)
    next: Optional[DecisionNext] = None
    labels: Optional[DecisionLabels] = None
    evidence_reviewed: Optional[bool] = None

    @model_validator(mode="after")
    def _rules(self) -> "DecisionRequest":
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason is empty")
        has_reason = self.reason is not None
        if self.action == "approve":
            if self.reply is not None:
                raise ValueError("approve sends the draft as is; to change it use edit")
            if self.next is not None:
                raise ValueError("next only goes with reject")
        elif self.action == "edit":
            if self.reply is None or not has_reason:
                raise ValueError("edit needs reply and reason")
            if self.next is not None:
                raise ValueError("next only goes with reject")
        else:  # reject
            if not has_reason or self.next is None:
                raise ValueError("reject needs reason and next")
            if self.next == "escalate" and self.reply is not None:
                raise ValueError("escalate sends nothing to the customer; reply only with request_information")
        return self


class DecisionResponse(_Strict):
    case_id: str
    status: CaseStatus
    analyst_decision: AnalystDecisionView
    labels_emitted: list[str]


# ---------------------------------------------------------------- errors

class ApiErrorBody(_Strict):
    code: ErrorCode
    message: str
    retryable: bool


class ApiError(_Strict):
    error: ApiErrorBody


# HTTP status per error code (docs/interfaz_chat_requerimientos.md section 7).
ERROR_HTTP: dict[str, int] = {
    "invalid_request": 400,
    "session_expired": 401,
    "not_authorized": 403,
    "conflict": 409,
    "session_limit": 409,
    "rate_limited": 429,
    "tool_unavailable": 503,
    "model_timeout": 504,  # never returned by /chat (it answers 200 with a template)
    "not_implemented": 501,
    "precondition": 422,  # well formed, but the case state does not allow it (analyst decision)
    "internal": 500,
}

# Default retryable flag per code; a caller may override it (e.g. conflict while in flight).
ERROR_RETRYABLE: dict[str, bool] = {
    "invalid_request": False,
    "session_expired": False,
    "not_authorized": False,
    "conflict": False,
    "session_limit": False,
    "rate_limited": True,
    "tool_unavailable": True,
    "model_timeout": True,
    "not_implemented": False,
    "precondition": False,
    "internal": True,
}


class ApiFailure(Exception):
    """Raised anywhere in the request path; the handler turns it into ``{error: {...}}``.

    ``message`` is for developers and logs; it never carries customer data or whether a
    case exists (403 is identical for "not yours" and "does not exist").
    """

    def __init__(self, code: str, message: str, retryable: bool | None = None):
        if code not in ERROR_HTTP:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = ERROR_RETRYABLE[code] if retryable is None else retryable
        self.http_status = ERROR_HTTP[code]

    def body(self) -> dict:
        return ApiError(error=ApiErrorBody(code=self.code, message=self.message,
                                           retryable=self.retryable)).model_dump()


AnalystCaseView.model_rebuild()
