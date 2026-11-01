"""Analyst console service (owner: arturo). docs/contrato_consola.md; shapes in contract.py.

Behind the three ``/analyst/*`` routes. In the cloud, API Gateway validates the Cognito JWT
before the Lambda runs and passes the claims in ``event.requestContext.authorizer.jwt.claims``;
the handler hands them here. There is NO authentication bypass: without claims or without a
``sub`` every method answers 401 ``session_expired`` ("sign in again" for the console).

- ``list_cases``: lane B and lane C cases (lane A resolved in contact never enter), filters
  status/lane/language, ``limit`` 1..50, opaque keyset cursor. Order: cases still waiting for
  someone first (answered ones, ``notified``/``closed``, go last: their SLA no longer runs),
  then priority ``high`` before the rest (fraud, regulator, a lost card, a reopened case),
  then ``breach_at`` ascending with nulls last, then ``created_at`` ascending (first come,
  first served; a later write such as the investigator report never moves a case up), then
  ``case_id``.
  ``poll_after_ms`` = 15000.
- ``get_detail``: ``AnalystCaseDetail`` (case without customer_id, report, context,
  allowed_actions, version). A case that does not exist and a lane A case are the same 403.
  Opening a lane B case that nobody decided marks it taken (``lifecycle.on_opened``: deletes
  its ``unassigned`` SLA timer; one conditional write, so ``version`` is read after it).
- SLA alerts in the queue (``sla_alerts``): the timers that fired, "unassigned" only while no
  analyst took the case.
- ``decide``: ``DecisionRequest`` -> ``DecisionResponse``. Idempotent by
  ``client_decision_id``; 409 for a stale version or an already decided case; 422 for the
  preconditions. ``decided_by`` is ALWAYS the JWT ``sub``, never the body.

The customer_id to read context is taken from the stored case, never from the request
(proposal ``GatewayContext.for_analyst`` to Andrés). Nothing here moves money: a decision only
changes the case and, when it sends something, the text is stamped as approved by a person.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import ValidationError

from conversation import contract as C
from conversation import demo_gateway as default_gateway
from conversation.case import (
    SLA_TIMER_KINDS, AnalystDecision, CaseRecord, CaseStatus, Lane, Resolution, analyst_view,
)
from conversation.lifecycle import CaseLifecycle
from conversation.store import Stores
from conversation.templates import render

NOT_AUTHORIZED = "not authorized"
_INTENT_CLASSES = set(C.IntentClass.__args__)  # type: ignore[attr-defined]
_B_QUEUE = {"unrecognized_charge": "disputes", "wrong_fee": "fees"}


def analyst_sub(claims: dict[str, Any] | None) -> str:
    """The analyst id from the verified JWT claims; 401 without them."""
    sub = (claims or {}).get("sub") if isinstance(claims, dict) else None
    if not isinstance(sub, str) or not sub.strip():
        raise C.ApiFailure("session_expired", "missing analyst identity (JWT claims)")
    return sub.strip()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _ts(dt: datetime | None) -> float:
    return dt.timestamp() if dt is not None else 0.0


def _stamp(text: str, language: str) -> str:
    """The analyst-approved text as the customer receives it: always ends with the stamp."""
    stamp = render("resolution_stamp", language)
    text = text.strip()
    return text if text.endswith(stamp) else f"{text} {stamp}"


# Answered cases: the customer already got a reply, so they sort after the pending ones.
_ANSWERED = frozenset({"notified", "closed", "resolved_in_contact"})


class AnalystService:
    def __init__(self, stores: Stores, lifecycle: CaseLifecycle, clock: Callable[[], datetime] | None = None,
                 gateway: Any = default_gateway) -> None:
        self.stores = stores
        self.lifecycle = lifecycle
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.gw = gateway
        # One decision at a time per case, from the idempotency check to the stored reply, so a
        # concurrent retry of the same decision gets the same reply instead of 409 (SEC-06).
        # Process-local; in the cloud the reply is written in the same conditional write.
        self._decide_guard = threading.Lock()
        self._decide_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------ GET /analyst/cases

    def list_cases(self, claims: dict[str, Any] | None, query: dict[str, Any] | None) -> dict[str, Any]:
        analyst_sub(claims)
        if query is not None and not isinstance(query, dict):
            raise C.ApiFailure("invalid_request", "query must be key=value pairs")
        try:
            q = C.AnalystCaseListQuery.model_validate(query or {})
        except ValidationError as e:
            fields = sorted({".".join(str(p) for p in err["loc"]) or "query" for err in e.errors()})
            raise C.ApiFailure("invalid_request", "invalid query: " + ", ".join(fields))
        signature = f"{q.status}|{q.lane}|{q.language}"
        after = self._decode_cursor(q.cursor, signature) if q.cursor else None
        self.lifecycle.advance_all()
        rows = []
        for stored in self.stores.cases.all():
            case = stored.case
            if case.lane not in (Lane.B, Lane.C):
                continue
            if q.status and case.status.value != q.status:
                continue
            if q.lane and case.lane.value != q.lane:
                continue
            if q.language and case.language != q.language:
                continue
            rows.append((self._sort_key(case), case))
        rows.sort(key=lambda r: r[0])
        if after is not None:
            rows = [r for r in rows if r[0] > after]
        page = rows[: q.limit]
        next_cursor = self._encode_cursor(page[-1][0], signature) if len(rows) > q.limit else None
        response = {
            "items": [self._item(case) for _, case in page],
            "next_cursor": next_cursor,
            "as_of": self.clock().isoformat(),
            "poll_after_ms": C.ANALYST_QUEUE_POLL_MS,
        }
        return C.AnalystCaseListResponse.model_validate(response).model_dump()

    @staticmethod
    def _sort_key(case: CaseRecord) -> tuple:
        breach = case.clock.breach_at
        answered = 1 if case.status.value in _ANSWERED else 0
        high = 0 if case.handoff.priority == "high" else 1
        return (answered, high, 0 if breach else 1, _ts(breach), _ts(case.created_at), case.case_id)

    @staticmethod
    def _encode_cursor(key: tuple, signature: str) -> str:
        raw = json.dumps({"k": list(key), "f": signature}, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str, signature: str) -> tuple:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            data = json.loads(raw)
            key = data["k"]
            if data["f"] != signature or not isinstance(key, list) or len(key) != 6:
                raise ValueError("cursor of another query")
            numbers = [float(x) for x in key[:5]]
            if not all(math.isfinite(x) for x in numbers):  # 1e999, NaN: not a key we issued
                raise ValueError("cursor with a non-finite number")
            return (int(numbers[0]), int(numbers[1]), int(numbers[2]), numbers[3], numbers[4], str(key[5]))
        except (ValueError, KeyError, TypeError, ArithmeticError, json.JSONDecodeError):
            raise C.ApiFailure("invalid_request", "invalid cursor")

    def _queue(self, case: CaseRecord) -> str | None:
        if case.handoff.queue:
            return case.handoff.queue
        if case.lane == Lane.B:
            return _B_QUEUE.get(case.subcategory or "", "complaints_pt" if case.language == "pt" else "complaints_es")
        return None

    def _display_name(self, case: CaseRecord) -> str:
        profile = self.gw.profile_for_customer_id(case.customer_id)
        return profile.display_name if profile else "Cliente"

    def _item(self, case: CaseRecord) -> dict[str, Any]:
        report = self.stores.reviews.get_report(case.case_id)
        intent_class = case.intent.class_ if case.intent and case.intent.class_ in _INTENT_CLASSES else None
        return {
            "case_id": case.case_id,
            "updated_at": (case.updated_at or case.created_at).isoformat(),
            "status": case.status.value,
            "lane": case.lane.value if case.lane else None,
            "lane_rule_id": case.lane_reason,
            "queue": self._queue(case),
            "priority": case.handoff.priority or ("normal" if case.lane == Lane.B else None),
            "language": case.language,
            "country": case.country,
            "display_name": self._display_name(case),
            "intent_class": intent_class,
            "intent_p": case.intent.p if intent_class else None,
            "subcategory": case.subcategory,
            "breach_at": _iso(case.clock.breach_at),
            "sla_alerts": self._sla_alerts(case),
            "has_report": report is not None,
            "report_reliable": C.report_reliable(report),
        }

    @staticmethod
    def _sla_alerts(case: CaseRecord) -> list[str]:
        fired = {e.kind for e in case.clock_events}
        return [k for k in SLA_TIMER_KINDS if k in fired and not (k == "unassigned" and case.taken_at)]

    # ------------------------------------------------------------ GET /analyst/cases/{case_id}

    def _load(self, case_id: str | None):
        stored = self.lifecycle.advance(case_id) if case_id else None
        if stored is None or stored.case.lane not in (Lane.B, Lane.C):
            raise C.ApiFailure("not_authorized", NOT_AUTHORIZED)  # same 403 for "missing" and "not in the queue"
        return stored

    def get_detail(self, claims: dict[str, Any] | None, case_id: str | None) -> dict[str, Any]:
        sub = analyst_sub(claims)
        stored = self._load(case_id)
        on_opened = getattr(self.lifecycle, "on_opened", None)
        if on_opened is not None:
            stored = on_opened(stored.case.case_id, sub) or stored  # taken: no "unassigned" alert
        case = stored.case
        report = self.stores.reviews.get_report(case.case_id)
        get_conv = getattr(self.stores.reviews, "get_conversation", None)
        conversation = get_conv(case.case_id) if get_conv else []
        detail = {
            "case": analyst_view(case, conversation),
            "report": report,
            "context": self._context(stored),
            "allowed_actions": self._allowed(case, report),
            "version": case.version,
        }
        return C.AnalystCaseDetail.model_validate(detail).model_dump(by_alias=True)

    @staticmethod
    def _allowed(case: CaseRecord, report: dict | None) -> list[str]:
        if case.status != CaseStatus.awaiting_analyst or case.lane != Lane.B or case.analyst_decision:
            return []
        has_draft = bool(report and (report.get("draft_reply") or {}).get("text"))
        return (["approve"] if has_draft else []) + ["edit", "reject"]

    def _context(self, stored) -> dict[str, Any]:
        """Profile, cards, the disputed charge with its duplicates and reversals, prior contacts.
        Read with the case's own customer_id and session (a card blocked in that chat shows as
        blocked) as the ANALYST: the customer's judge switch ``tools_down`` does not empty it
        (SEC-02). A card block recorded in the case (``actions``) always shows as blocked, even
        if the demo state was lost (SEC-01). A tool that fails (process-wide ``--fail tools``)
        leaves its part empty and names it in ``unavailable`` ("cards", "evidence_txns",
        "prior_contacts"), so an empty list never reads as "no cards": the detail never fails
        for it."""
        case = stored.case
        ctx = self.gw.GatewayContext.for_analyst(customer_id=case.customer_id, session_id=stored.session_id,
                                                 trace_id=f"an-{case.case_id}")
        profile = self.gw.profile_for_customer_id(case.customer_id)

        unavailable: list[str] = []

        def safe(fn, default, part):
            try:
                return fn()
            except self.gw.GatewayError:
                if part not in unavailable:
                    unavailable.append(part)
                return default

        cards = safe(lambda: self.gw.get_cards(ctx), [], "cards")
        blocked_ids = {(a.args or {}).get("card_id") for a in case.actions
                       if a.tool == "block_card" and isinstance(a.result, dict) and a.result.get("status") == "blocked"}
        contacts = safe(lambda: self.gw.get_prior_contacts(ctx), [], "prior_contacts")
        txns: list[Any] = []
        txn_id = (case.evidence.transaction or {}).get("txn_id")
        if txn_id:
            main = safe(lambda: self.gw.get_transaction(ctx, txn_id), None, "evidence_txns")
            if main is not None:
                group = [main] + safe(lambda: self.gw.find_duplicates(ctx, txn_id), [], "evidence_txns")
                txns = list(group)
                for m in group:
                    txns += safe(lambda m=m: self.gw.get_reversals(ctx, m.txn_id), [], "evidence_txns")
        seen, evidence_txns = set(), []
        for t in txns:
            if t.txn_id in seen:
                continue
            seen.add(t.txn_id)
            d = t.to_dict()
            d.pop("synthetic", None)
            evidence_txns.append(d)
        return {
            "profile": {"display_name": profile.display_name if profile else "Cliente",
                        "country": profile.country if profile else case.country,
                        "segment": (profile.segment or None) if profile else case.segment,
                        "language": case.language},
            "cards": [{"card_last4": k.card_last4, "status": "blocked" if k.card_id in blocked_ids else k.status}
                      for k in cards],
            "evidence_txns": evidence_txns,
            "prior_contacts": [{"contact_id": c.contact_id, "date": c.date.isoformat(), "channel": c.channel,
                                "contact_type": c.contact_type, "subcategory": c.subcategory, "status": c.status}
                               for c in contacts],
            "risk_evidence": case.risk_evidence.model_dump() if case.risk_evidence else None,
            "unavailable": [p for p in ("cards", "evidence_txns", "prior_contacts") if p in unavailable],
        }

    # ------------------------------------------------------------ POST /analyst/cases/{case_id}/decision

    def decide(self, claims: dict[str, Any] | None, case_id: str | None, body: Any) -> dict[str, Any]:
        sub = analyst_sub(claims)
        if not isinstance(body, dict):
            raise C.ApiFailure("invalid_request", "body must be a JSON object")
        try:
            req = C.DecisionRequest.model_validate(body)
        except ValidationError as e:
            fields = sorted({".".join(str(p) for p in err["loc"]) or "body" for err in e.errors()})
            raise C.ApiFailure("invalid_request", "invalid fields: " + ", ".join(fields))
        body_hash = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        key = f"{case_id}:{req.client_decision_id}"
        with self._decide_guard:
            lock = self._decide_locks.setdefault(str(case_id), threading.Lock())
        with lock:
            return self._decide_locked(sub, case_id, req, key, body_hash)

    def _decide_locked(self, sub: str, case_id: str | None, req: C.DecisionRequest, key: str,
                       body_hash: str) -> dict[str, Any]:
        replay = self._replay(key, body_hash)
        if replay is not None:
            return replay
        stored = self._load(case_id)
        case = stored.case
        if case.analyst_decision is not None:
            raise C.ApiFailure("conflict", "case already decided")
        if req.version != case.version:
            raise C.ApiFailure("conflict", "stale version: reload the case")
        if case.status != CaseStatus.awaiting_analyst or case.lane != Lane.B:
            raise C.ApiFailure("precondition", "case is not awaiting_analyst")
        report = self.stores.reviews.get_report(case.case_id)
        draft = ((report or {}).get("draft_reply") or {}).get("text")
        if req.action == "approve" and not draft:
            raise C.ApiFailure("precondition", "approve needs a report with a draft reply")
        if req.reply is not None and req.reply.language != case.language:
            raise C.ApiFailure("precondition", "reply language differs from the case language")
        if (req.action in ("approve", "edit") and report is not None and C.report_reliable(report) is False
                and req.evidence_reviewed is not True):
            raise C.ApiFailure("precondition", "unreliable report: confirm evidence_reviewed")

        now = self.clock()
        sent: str | None = None
        if req.action == "approve":
            sent = _stamp(draft, case.language)
        elif req.action == "edit":
            sent = _stamp(req.reply.text, case.language)
        elif req.next == "request_information":
            text = req.reply.text if req.reply else render("request_information_default", case.language,
                                                           case.country, case_id=case.case_id)
            sent = _stamp(text, case.language)
        edited = req.action == "edit" or (req.action == "reject" and req.reply is not None)
        decision = AnalystDecision(action=req.action, edited=edited, reason=req.reason, decided_at=now,
                                   decided_by=sub, next=req.next, draft_text=draft, sent_text=sent)
        resolution = (Resolution(language=case.language, text=sent, sent_at=now, approved_by_human=True)
                      if sent is not None else None)
        labels = self._labels(case, req, report)
        try:
            updated = self.lifecycle.on_decision(case.case_id, req.version, decision, resolution, labels)
        except C.ApiFailure as e:
            if e.code == "conflict":  # a concurrent retry of this same decision may have won
                replay = self._replay(key, body_hash)
                if replay is not None:
                    return replay
            raise
        if sent is not None:
            self._log_analyst_turn(case.case_id, sent, case.language, now)
        response = C.DecisionResponse.model_validate({
            "case_id": updated.case_id,
            "status": updated.status.value,
            "analyst_decision": {"action": decision.action, "edited": decision.edited, "reason": decision.reason,
                                 "decided_by": sub, "decided_at": now.isoformat()},
            "labels_emitted": labels,
        }).model_dump()
        self.stores.reviews.put_decision_reply(key, body_hash, response)
        return response

    def _log_analyst_turn(self, case_id: str, text: str, language: str, at: datetime) -> None:
        """What the customer received from the analyst, in the case's conversation (source
        "human": a person approved it). Never breaks the decision."""
        append = getattr(self.stores.reviews, "append_conversation", None)
        if append is None:
            return
        try:
            append(case_id, [{"role": "analyst", "text": text, "language": language, "source": "human",
                              "at": at.isoformat()}])
        except Exception:
            pass

    def _replay(self, key: str, body_hash: str) -> dict[str, Any] | None:
        saved = self.stores.reviews.get_decision_reply(key)
        if saved is None:
            return None
        if saved[0] != body_hash:
            raise C.ApiFailure("conflict", "client_decision_id already used with another body")
        return saved[1]

    @staticmethod
    def _labels(case: CaseRecord, req: C.DecisionRequest, report: dict | None) -> list[str]:
        """``tipo:valor[:detalle]`` (docs/contrato_consola.md): the decision, the recommendation
        accepted/edited/rejected, the lane confirmed or escalated, what was sent, the intent."""
        labels = [f"decision:{req.action}"]
        if report is not None:
            verdict = {"approve": "accepted", "edit": "edited", "reject": "rejected"}[req.action]
            labels.append(f"recommendation:{report['recommendation']}:{verdict}")
        lane = case.lane.value if case.lane else "none"
        escalated = req.action == "reject" and req.next == "escalate"
        labels.append(f"lane:{lane}:{'escalated' if escalated else 'confirmed'}")
        if req.action == "approve":
            labels.append("reply:as_drafted")
        elif req.action == "edit":
            labels.append("reply:edited")
        elif req.next == "request_information":
            labels.append("reply:edited" if req.reply is not None else "reply:template")
        if req.labels is not None:
            predicted = case.intent.class_ if case.intent else None
            if req.labels.intent_class is not None and req.labels.intent_class == predicted:
                labels.append(f"intent:{predicted}:confirmed")
            elif req.labels.intent_class is not None:
                labels.append(f"intent:{predicted or 'none'}:corrected_to:{req.labels.intent_class}")
            elif req.labels.intent_confirmed is True and predicted:
                labels.append(f"intent:{predicted}:confirmed")
        return labels
