"""contract.py must mirror frontend/src/api/types.ts (the source of truth).

Reads the .ts file as text (no Node needed) and compares, per interface, the field names and
which ones are optional, and, per string union, the allowed values. Any drift fails here.
"""

import re
import typing
from pathlib import Path

import pytest

from conversation import contract as C
from conversation.case import CUSTOMER_VIEW_FIELDS, CaseStatus

TYPES_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "types.ts"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _ts():
    if not TYPES_TS.exists():
        pytest.skip(f"{TYPES_TS} not found")
    return _strip_comments(TYPES_TS.read_text(encoding="utf-8"))


def _ts_fields() -> dict[str, dict[str, tuple[bool, str]]]:
    """{interface: {field: (optional, type text)}}, with the fields of `extends` parents."""
    text = _ts()
    raw: dict[str, tuple[str | None, dict[str, tuple[bool, str]]]] = {}
    for m in re.finditer(r"export interface (\w+)(?:\s+extends\s+(\w+))?\s*\{(.*?)\n\}", text, re.S):
        name, parent, body = m.group(1), m.group(2), m.group(3)
        fields = {}
        depth = 0
        for line in body.splitlines():
            if depth == 0:
                fm = re.match(r"\s*(\w+)(\?)?\s*:\s*([^;]*);?", line)
                if fm:
                    fields[fm.group(1)] = (bool(fm.group(2)), fm.group(3).strip())
            depth += line.count("{") - line.count("}")
        raw[name] = (parent, fields)

    def resolve(name):
        parent, fields = raw[name]
        return {**(resolve(parent) if parent else {}), **fields}

    return {name: resolve(name) for name in raw}


def ts_interfaces() -> dict[str, dict[str, bool]]:
    """{interface: {field: optional}}, with the fields of `extends` parents included."""
    return {name: {f: opt for f, (opt, _) in fields.items()} for name, fields in _ts_fields().items()}


def ts_nullable() -> dict[str, dict[str, bool | None]]:
    """{interface: {field: accepts null}}; None when the TS type is `unknown` (not compared)."""
    def top_level(t: str) -> str:  # drop generic arguments: Record<string, X | null> is not nullable
        while re.search(r"<[^<>]*>", t):
            t = re.sub(r"<[^<>]*>", "", t)
        return t

    out = {}
    for name, fields in _ts_fields().items():
        out[name] = {f: (None if t == "unknown" else bool(re.search(r"\|\s*null\b", top_level(t))))
                     for f, (_, t) in fields.items()}
    return out


def ts_unions() -> dict[str, set[str]]:
    """{type alias: {"literal", ...}} for `export type X = "a" | "b"`."""
    out = {}
    for m in re.finditer(r"export type (\w+)\s*=\s*([^;]+);", _ts()):
        values = re.findall(r'"([^"]+)"', m.group(2))
        if values and re.fullmatch(r'[\s|"\w-]+', m.group(2)):
            out[m.group(1)] = set(values)
    return out


def py_fields(model) -> dict[str, bool]:
    """{field (wire name): optional-in-request}: a field with a default counts as optional."""
    return {f.alias or name: not f.is_required() for name, f in model.model_fields.items()}


def _accepts_none(annotation) -> bool:
    if annotation is typing.Any or annotation is type(None):
        return True
    return any(_accepts_none(a) for a in typing.get_args(annotation)) if typing.get_origin(annotation) is typing.Union else False


def py_nullable(model) -> dict[str, bool]:
    return {f.alias or name: _accepts_none(f.annotation) for name, f in model.model_fields.items()}


INTERFACES = [
    "HealthResponse", "SessionRequest", "SessionCustomer", "SessionResponse", "ChatRequest",
    "DemoSwitchesPatch", "DemoSwitchState",
    "ChatButton", "ClaimedSlots", "Progress", "TraceStep", "TraceSummary", "ChatTurn",
    "ChatResponse", "CaseResolution", "CustomerCharge", "CustomerCaseView", "CaseResponse",
    # analyst console
    "ReportFinding", "ReportHypothesis", "DraftReply", "EvidenceRecord", "InvestigatorReport",
    "AnalystCaseListQuery", "AnalystCaseListItem", "AnalystCaseListResponse", "EvidenceTxn",
    "CaseEvidenceView", "CasePromise", "CaseAction", "CaseHandoff", "CaseClock", "MatchCandidate",
    "CaseMatch", "CaseIntent", "CaseRiskEvidence", "AnalystDecisionView", "AnalystCaseView",
    "ConversationTurn", "AnalystProfile", "AnalystCard", "PriorContact", "AnalystContext", "AnalystCaseDetail",
    "DecisionReply", "DecisionLabels", "DecisionRequest", "DecisionResponse",
    # SLA timers
    "CaseNotice", "ClockEvent",
]

# Interfaces the client sends: optional fields (`?` / a default) must match. Every other
# interface is a response the server builds: no `?` in TS and no default in Python.
REQUESTS = ["SessionRequest", "ChatRequest", "DemoSwitchesPatch", "AnalystCaseListQuery",
            "DecisionRequest", "DecisionReply", "DecisionLabels"]


def test_every_ts_interface_is_mirrored():
    names = set(ts_interfaces())
    assert names == set(INTERFACES) | {"ApiError"}, names ^ (set(INTERFACES) | {"ApiError"})


@pytest.mark.parametrize("name", INTERFACES)
def test_field_names_match(name):
    ts = ts_interfaces()[name]
    py = py_fields(getattr(C, name))
    assert set(py) == set(ts), f"{name}: only in py {set(py) - set(ts)}, only in ts {set(ts) - set(py)}"


@pytest.mark.parametrize("name", REQUESTS)
def test_optional_request_fields_match(name):
    ts = ts_interfaces()[name]
    py = py_fields(getattr(C, name))
    assert py == ts


@pytest.mark.parametrize("name", [n for n in INTERFACES if n not in REQUESTS])
def test_response_fields_are_all_required(name):
    assert not any(ts_interfaces()[name].values()), f"{name}: optional field in a response"
    assert not any(py_fields(getattr(C, name)).values()), f"{name}: defaulted field in a response"


@pytest.mark.parametrize("name", [n for n in INTERFACES if n not in REQUESTS])
def test_nullable_fields_match(name):
    ts = ts_nullable()[name]
    py = py_nullable(getattr(C, name))
    diff = {f: (py[f], ts[f]) for f in ts if ts[f] is not None and py[f] != ts[f]}
    assert not diff, f"{name}: (py accepts None, ts accepts null) differ for {diff}"


def test_error_shape_matches():
    assert set(ts_interfaces()["ApiError"]) == {"error"}
    assert set(C.ApiErrorBody.model_fields) == {"code", "message", "retryable"}


@pytest.mark.parametrize("alias", [
    "Language", "Lane", "DemoKey", "ButtonKind", "ProgressStep", "TraceActor", "ReplySource",
    "Degradation", "CaseStatus", "ErrorCode", "LifecycleStep", "HypothesisName",
    "Recommendation", "EvidenceKind", "IntentClass", "Priority", "GateAction", "MatchBand",
    "TxnType", "DecisionAction", "DecisionNext", "ConversationRole", "TurnSource", "ContextPart",
    "SlaTimerKind", "NoticeKind",
])
def test_string_unions_match(alias):
    ts = ts_unions()[alias]
    py = set(typing.get_args(getattr(C, alias)))
    assert py == ts, f"{alias}: only in py {py - ts}, only in ts {ts - py}"


def test_inline_unions_match():
    text = _ts()
    channel = re.search(r'channel:\s*([^;]+);', text).group(1)
    assert set(re.findall(r'"(\w+)"', channel)) == set(typing.get_args(C.Channel))
    ctype = re.search(r'complaint_type:\s*([^;]+);', text).group(1)
    assert set(re.findall(r'"(\w+)"', ctype)) == set(typing.get_args(C.ComplaintType))
    mode = re.search(r'input_mode:\s*([^;]+);', text).group(1)
    assert set(re.findall(r'"(\w+)"', mode)) == set(typing.get_args(C.InputMode))


def test_case_status_enum_and_customer_view_follow_the_contract():
    assert {s.value for s in CaseStatus} == ts_unions()["CaseStatus"]
    assert list(CUSTOMER_VIEW_FIELDS) == list(ts_interfaces()["CustomerCaseView"])


def test_every_error_code_has_an_http_status():
    assert set(C.ERROR_HTTP) == ts_unions()["ErrorCode"] == set(C.ERROR_RETRYABLE)
    assert C.ERROR_HTTP["session_limit"] == 409 and C.ERROR_RETRYABLE["session_limit"] is False


def test_chat_request_needs_exactly_one_input():
    ok = C.ChatRequest(client_msg_id="a-1", message="hola")
    assert ok.button_id is None
    for bad in (dict(client_msg_id="a"), dict(client_msg_id="a", message="x", button_id="b_1"),
                dict(client_msg_id="a", message="   "), dict(client_msg_id="a", message="x" * 1001),
                dict(client_msg_id="a b", message="x"),
                dict(client_msg_id="a", message="x", session_token="t"),
                dict(client_msg_id="a", message="x", lane="A")):
        with pytest.raises(ValueError):
            C.ChatRequest(**bad)


def test_session_request_rejects_customer_id():
    with pytest.raises(ValueError):
        C.SessionRequest(demo_key="lucia", channel="web", customer_id="DEMO-C-0001")
    with pytest.raises(ValueError):
        C.SessionRequest(demo_key="mallory", channel="web")


# ---------------------------------------------------------------- demo switches (RF-22)

def test_chat_request_accepts_switches_alone_or_with_one_input():
    r = C.ChatRequest(client_msg_id="s-1", demo_switches={"tools_down": True})
    assert r.switches_only and r.demo_switches.tools_down is True and r.demo_switches.model_slow is None
    r = C.ChatRequest(client_msg_id="s-2", message="hola", demo_switches={"model_slow": True})
    assert not r.switches_only
    assert C.ChatRequest(client_msg_id="s-3", demo_switches={"expire_session": True}).switches_only
    for bad in (dict(client_msg_id="a", demo_switches={}),
                dict(client_msg_id="a", demo_switches={"lane": "A"}),
                dict(client_msg_id="a", demo_switches={"tools_down": "yes please"}),
                dict(client_msg_id="a", message="x", button_id="b_1", demo_switches={"tools_down": True})):
        with pytest.raises(ValueError):
            C.ChatRequest(**bad)


def test_switch_state_is_complete():
    assert set(C.DemoSwitchState.model_fields) == {"tools_down", "model_slow", "fast_clock"}
    with pytest.raises(ValueError):
        C.DemoSwitchState(tools_down=True)
    with pytest.raises(ValueError):
        C.DemoSwitchState(tools_down=True, model_slow=False)  # fast_clock is required


def test_fast_clock_is_a_switch_on_its_own():
    r = C.ChatRequest(client_msg_id="fc-1", demo_switches={"fast_clock": True})
    assert r.switches_only and r.demo_switches.fast_clock is True
    with pytest.raises(ValueError):
        C.ChatRequest(client_msg_id="fc-2", demo_switches={"fast_clock": "yes please"})


def test_sla_timer_kinds_follow_the_case_record():
    from conversation.case import SLA_TIMER_CLOCK_FIELD, SLA_TIMER_KINDS, ClockEvent, Notice

    assert set(SLA_TIMER_KINDS) == set(typing.get_args(C.SlaTimerKind)) == set(SLA_TIMER_CLOCK_FIELD)
    assert set(typing.get_args(Notice.model_fields["kind"].annotation)) == set(typing.get_args(C.NoticeKind))
    assert set(typing.get_args(ClockEvent.model_fields["kind"].annotation)) == set(SLA_TIMER_KINDS)
    assert set(SLA_TIMER_CLOCK_FIELD.values()) <= set(C.CaseClock.model_fields)
    ok = {"kind": "sla_80", "text": "Seguimos trabajando en tu caso (sintético).", "language": "es",
          "at": "2026-10-09T15:00:00+00:00"}
    assert C.CaseNotice.model_validate(ok).kind == "sla_80"
    for bad in ({**ok, "kind": "refund"}, {**ok, "language": "en"}, {k: v for k, v in ok.items() if k != "at"}):
        with pytest.raises(ValueError):
            C.CaseNotice.model_validate(bad)
    with pytest.raises(ValueError):
        C.ClockEvent.model_validate({"kind": "sla_50", "fired_at": "2026-10-09T15:00:00+00:00"})


# ---------------------------------------------------------------- customer view additions

def test_resolution_is_always_approved_by_a_person():
    ok = dict(language="es", text="Revisamos tu caso. Respuesta aprobada por una persona del equipo.",
              sent_at="2026-09-30T15:00:00+00:00", approved_by_human=True)
    assert C.CaseResolution(**ok).approved_by_human is True
    with pytest.raises(ValueError):
        C.CaseResolution(**{**ok, "approved_by_human": False})


def test_lifecycle_steps_follow_the_case_statuses():
    from conversation.case import _LIFECYCLE_STEP

    assert set(_LIFECYCLE_STEP.values()) == ts_unions()["LifecycleStep"]
    assert {s.value for s in CaseStatus} - {s.value for s in _LIFECYCLE_STEP} == {"resolved_in_contact"}


# ---------------------------------------------------------------- analyst console

def report(**over):
    base = dict(
        report_id="R-1", case_id="EV-ABCDEFGH", created_at="2026-09-30T15:00:00+00:00",
        findings=[{"claim": "Dos cargos idénticos con 3 minutos de diferencia",
                   "evidence_ids": ["TX-AND-0001", "TX-AND-0002"]}],
        hypotheses=[{"name": "duplicate", "p": 0.86}, {"name": "fraud", "p": 0.05}],
        recommendation="open_chargeback", confidence=0.8,
        draft_reply={"language": "es", "text": "Encontramos el cargo duplicado (sintético)."},
        open_questions=[], citations_valid=True, removed_claims=0, tool_calls=4, latency_ms=12,
        model_id="stub-g2-deterministic", prompt_version="stub-provisional",
        evidence_records={
            "TX-AND-0001": {"kind": "transaction", "summary": "2026-06-14 10:02 · TIENDA TECNO SAS",
                            "fields": {"amount": "189900.00", "fraud_score": 3.0, "reversed": False}},
            "TX-AND-0002": {"kind": "transaction", "summary": "2026-06-14 10:05 · TIENDA TECNO SAS",
                            "fields": {"amount": "189900.00", "fraud_score": 3.0, "reversed": False}},
        },
    )
    return {**base, **over}


def test_report_has_the_six_hypotheses_and_recommendations_of_interface_6():
    assert set(typing.get_args(C.HypothesisName)) == {
        "fraud", "forgotten_purchase", "unfamiliar_merchant_name", "duplicate", "fee_error", "pending_reversal"}
    assert set(typing.get_args(C.Recommendation)) == {
        "reverse_fee", "open_chargeback", "block_card", "explain_and_close", "request_information", "escalate"}
    # #6 fields exactly, plus the evidence_records proposal.
    assert set(C.InvestigatorReport.model_fields) == {
        "report_id", "case_id", "created_at", "findings", "hypotheses", "recommendation", "confidence",
        "draft_reply", "open_questions", "citations_valid", "removed_claims", "tool_calls", "latency_ms",
        "model_id", "prompt_version", "evidence_records"}
    assert C.InvestigatorReport.model_validate(report()).recommendation == "open_chargeback"


def test_report_rejects_unknown_names_duplicates_and_uncited_validity():
    for bad in (report(recommendation="refund"),
                report(hypotheses=[{"name": "fraud", "p": 0.5}, {"name": "fraud", "p": 0.4}]),
                report(hypotheses=[{"name": "aliens", "p": 0.5}]),
                report(confidence=1.2),
                report(evidence_records={}),  # citations_valid but nothing to open
                report(extra_field=1)):
        with pytest.raises(ValueError):
            C.InvestigatorReport.model_validate(bad)
    # An invalid report may cite ids it does not have: that is what citations_valid=false says.
    assert C.InvestigatorReport.model_validate(report(evidence_records={}, citations_valid=False))


def test_report_reliability_rule():
    assert C.report_reliable(None) is None
    assert C.report_reliable(report()) is True
    assert C.report_reliable(report(removed_claims=1)) is True
    assert C.report_reliable(report(removed_claims=2)) is False
    assert C.report_reliable(C.InvestigatorReport.model_validate(report(citations_valid=False))) is False


def test_intent_classes_are_the_nine_of_interface_4():
    from conversation.classifier import CLASSES

    assert set(typing.get_args(C.IntentClass)) == set(CLASSES)


def test_list_query_limits():
    assert C.AnalystCaseListQuery().limit == 20
    assert C.AnalystCaseListQuery.model_validate({"limit": "50", "status": "awaiting_analyst"}).limit == 50
    for bad in ({"limit": 0}, {"limit": 51}, {"lane": "D"}, {"customer_id": "DEMO-C-0001"}):
        with pytest.raises(ValueError):
            C.AnalystCaseListQuery.model_validate(bad)


def decision(**over):
    return {"client_decision_id": "d-1", "version": 3, **over}


@pytest.mark.parametrize("body", [
    decision(action="approve"),
    decision(action="approve", evidence_reviewed=True, labels={"intent_confirmed": True}),
    decision(action="edit", reply={"language": "pt", "text": "Olá (sintético)"}, reason="tono"),
    decision(action="reject", reason="falta evidencia", next="request_information"),
    decision(action="reject", reason="falta evidencia", next="request_information",
             reply={"language": "es", "text": "¿Nos confirmas la fecha? (sintético)"}),
    decision(action="reject", reason="posible fraude", next="escalate", labels={"intent_class": "lost_card"}),
])
def test_decision_request_valid(body):
    assert C.DecisionRequest.model_validate(body).action == body["action"]


@pytest.mark.parametrize("body", [
    decision(action="approve", reply={"language": "es", "text": "x"}),  # approve sends the draft as is
    decision(action="approve", next="escalate"),
    decision(action="edit", reply={"language": "es", "text": "x"}),  # edit needs a reason
    decision(action="edit", reason="tono"),  # edit needs a reply
    decision(action="edit", reply={"language": "es", "text": "   "}, reason="tono"),
    decision(action="edit", reply={"language": "es", "text": "x"}, reason="tono", next="escalate"),
    decision(action="reject", reason="no"),  # reject needs next
    decision(action="reject", next="escalate"),  # reject needs a reason
    decision(action="reject", reason="   ", next="escalate"),
    decision(action="reject", reason="no", next="escalate", reply={"language": "es", "text": "x"}),
    decision(action="refund", reason="no"),
    decision(action="approve", labels={"intent_class": "dispute_fee", "intent_confirmed": True}),
    decision(action="approve", labels={"intent_class": "loan"}),
    decision(action="approve", decided_by="someone-else"),  # never from the body
    decision(action="approve", version=0),
    {"client_decision_id": "d 1", "version": 1, "action": "approve"},
    decision(action="edit", reply={"language": "es", "text": "x" * 2001}, reason="tono"),
])
def test_decision_request_invalid(body):
    with pytest.raises(ValueError):
        C.DecisionRequest.model_validate(body)


def test_precondition_is_a_422_that_does_not_retry():
    e = C.ApiFailure("precondition", "case is not awaiting_analyst")
    assert e.http_status == 422 and e.retryable is False
    assert e.body() == {"error": {"code": "precondition", "message": "case is not awaiting_analyst",
                                  "retryable": False}}
