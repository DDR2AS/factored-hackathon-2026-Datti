"""Lane B case lifecycle (owner: arturo locally; the cloud version is Andrés's Step Functions).

Plan v2 section 6: open -> investigating (G2) -> awaiting_analyst -> notified -> closed.
The code that opens cases and the analyst console depend only on ``CaseLifecycle``; this file
has the LOCAL implementation, ``LocalLifecycle``, used by ``scripts/local_api.py`` and the
tests. Selection by ``LIFECYCLE_BACKEND`` (``local`` | ``stepfunctions``, proposal to #9).

Transitions (local, lazy: applied when the case is read, with the injected clock, under a
lock PER CASE, so tests are deterministic, nothing runs in the background and a slow
investigation of one case never blocks another case or another chat, H3):

| from | to | when |
|---|---|---|
| open, money case (unrecognized_charge, wrong_fee: duplicates and purchase_amount_disputed included) | investigating | ``start`` |
| open, app/branch/service complaint | awaiting_analyst, no investigator | ``start`` |
| investigating | awaiting_analyst with the report | ``LOCAL_INVESTIGATION_DELAY_SECONDS`` after start (4 by default, 0 in tests) |
| awaiting_analyst | notified with ``resolution`` | analyst approve / edit / reject + request_information |
| awaiting_analyst | handed_off, queue ``senior`` (still lane B) | analyst reject + escalate |
| notified | closed | ``close_after_seconds`` (30) after ``resolution.sent_at``, unless ``awaiting_customer`` |
| notified (``awaiting_customer``) | reopened | the customer answers in the chat (orchestrator) |
| notified / closed | reopened, queue ``senior``, priority high | the customer disagrees in the chat (orchestrator) |
| reopened | awaiting_analyst | next read (the previous decision is in open_questions and the trace) |

``awaiting_customer`` is set when the decision asks the customer for information: reject +
``request_information``, or approve / edit when the report recommends ``request_information``
(its draft says "you can answer here"). Such a case waits for the answer instead of closing
after 30 s (CX-01/H1).

``advance_all`` (the queue listing) runs due investigations within ``advance_budget_seconds``
(2 s); the rest stay ``investigating`` until the next poll, and a case whose lock is busy is
skipped, so the listing never waits for another reader's investigation (H3).

Lanes A and C have no lifecycle: a lane C case is handed off when it opens and shows up in
the analyst queue with its handoff package (read only). Every transition is a #7 trace event
(``trace_id = "lc-<case_id>"``); the analyst decision adds an ``actor: "human"`` audit event.

Mapping to the cloud (Andrés, Step Functions + EventBridge Scheduler):
- ``start`` = StartExecution when the case is saved in lane B (the state machine sets
  ``investigating`` and invokes G2; non-money complaints take the branch that skips it).
- the investigating wait is the G2 Lambda task itself (<= 12 tool calls, <= 90 s), not a delay.
- ``awaiting_analyst`` = a task with ``.waitForTaskToken``; the token is stored in the case
  (internal field, never in a view).
- ``on_decision`` = conditional write of the case on ``version`` + SendTaskSuccess with the
  token; the state machine moves to notified (sends the resolution) or to the senior queue.
- ``notified -> closed`` = a one-time EventBridge schedule. ``advance`` in the cloud only reads.
- ``awaiting_customer`` = another ``.waitForTaskToken`` state; the customer's answer sends the
  token back and the machine returns to ``awaiting_analyst``.

SLA timers (plan v2 sections 6 and 12; LOCAL SIMULATION of Andrés's EventBridge Scheduler):

| timer | fires at (promise.py) | only if | effect |
|---|---|---|---|
| ``unassigned`` | ``clock.assigned_by`` (+24 h) | no analyst took the case | alert in the analyst queue (``sla_alerts``) |
| ``sla_80`` | ``clock.sla_alert_at`` (80 % of the SLA) | | notice to the customer (``sla_notice.sla_80``) |
| ``breached`` | ``clock.breach_at`` | | senior queue, priority high, flag ``sla_breached``, notice ``sla_notice.escalated`` |

- ``start`` schedules the three when a lane B case opens (``case.sla_timers``, internal).
- They fire only while the case is still open for the SLA (open, investigating,
  awaiting_analyst, no decision). ``on_decision`` deletes the pending ones; a case reopened
  later does not get them back (the reopen already sends it to the senior queue).
- "Taken" = an analyst opened the case (``on_opened``, from GET /analyst/cases/{id}) or decided
  it; that deletes the ``unassigned`` timer and hides a fired one from ``sla_alerts``.
- Firing is lazy like the other transitions (on any read of the case, with the injected
  clock); a timer that is due fires once, stamped with the clock at that moment
  (``clock_events``), and each firing is a #7 event ``sla_timer.<kind>`` (actor ``rule``).
  Scheduling, deleting and rescaling are ``sla_timer.schedule|cancel|rescale``.
- Notices go to ``case.notices`` (customer view, one per kind) and to the case conversation
  (analyst), in the case language and the country's register, with the promised date.
- Demo clock (``fast_clock`` switch of a demo session): the fire times are
  ``created_at + (t - created_at) * timer_scale`` with ``timer_scale = LOCAL_SLA_SCALE``
  (1/1440 by default: 1 day = 1 minute; ``local_api.py --sla-scale``). The promise dates stay
  real. There is no fast clock in production: the switch only exists for ``source == "demo"``.

Mapping to the cloud (Andrés): ``start`` = one ``CreateSchedule`` per timer, ``at(...)``
expression, ``ActionAfterCompletion=DELETE``, name ``<case_id>-<kind>``, target the lifecycle
Lambda with ``{case_id, kind}``; the target re-reads the case and does nothing unless it is
still open (the same check as here). ``on_decision`` and ``on_opened`` = ``DeleteSchedule``
(ResourceNotFound ignored). No fast clock in the cloud.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from pydantic import ValidationError

from conversation import contract as C
from conversation import demo_gateway as default_gateway
from conversation.case import (
    SLA_TIMER_CLOCK_FIELD, SLA_TIMER_KINDS, AnalystDecision, CaseRecord, CaseStatus, ClockEvent, Lane, Notice,
    Resolution,
)
from conversation.investigator_stub import (
    ToolBudgetExceeded, ToolCall, ToolsUnavailable, investigate, is_money_case,
)
from conversation.store import CaseVersionConflict, StoreConfigError, StoredCase, Stores
from conversation.templates import forbidden_hits, format_date, local_datetime, render

DEFAULT_INVESTIGATION_DELAY_SECONDS = 4.0
DEFAULT_CLOSE_AFTER_SECONDS = 30.0
DEFAULT_ADVANCE_BUDGET_SECONDS = 2.0
SENIOR_QUEUE = "senior"
DEFAULT_SLA_SCALE = 1 / 1440  # fast_clock: 1 day = 1 minute
# The SLA runs (and its timers may fire) only while the case waits for the team.
TIMER_STATUSES = frozenset({CaseStatus.open, CaseStatus.investigating, CaseStatus.awaiting_analyst})
BREACH_NOTE = "SLA interno vencido: escalado automáticamente a la cola senior con prioridad alta"


class CaseLifecycle(Protocol):
    def start(self, case_id: str, fast_clock: bool | None = None) -> None:
        """A lane B case was saved: open -> investigating (money) or awaiting_analyst, and its
        SLA timers are scheduled. ``fast_clock``: the session's demo clock (None: read it from
        the session store)."""

    def advance(self, case_id: str) -> StoredCase | None:
        """Local: apply the transitions that are due and return the case. Cloud: only read."""

    def advance_all(self) -> None:
        """Local: advance every lane B case that can still move (before listing the queue)."""

    def on_decision(self, case_id: str, expected_version: int, decision: AnalystDecision,
                    resolution: Resolution | None, labels: list[str]) -> CaseRecord:
        """Store the analyst decision with a conditional write on ``expected_version`` and move
        the case (notified or handed_off). Raises ApiFailure ``conflict`` if someone wrote first
        or it is already decided, ``precondition`` if it is not awaiting_analyst. Deletes the
        pending SLA timers."""

    def on_opened(self, case_id: str, analyst: str) -> StoredCase | None:
        """An analyst opened the case in the console: it is taken (the ``unassigned`` timer is
        deleted). Returns the case as it is now."""

    def set_fast_clock(self, case_id: str, on: bool) -> None:
        """Demo only: the session's fast_clock switch changed; rescale the pending timers."""

    def has_pending_timers(self, case_id: str) -> bool:
        """The case still has SLA timers scheduled (the live card keeps polling)."""


def delay_from_env(env: dict[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = env.get("LOCAL_INVESTIGATION_DELAY_SECONDS")
    try:
        value = float(raw) if raw not in (None, "") else DEFAULT_INVESTIGATION_DELAY_SECONDS
    except ValueError:
        value = DEFAULT_INVESTIGATION_DELAY_SECONDS
    return max(0.0, value)


def sla_scale_from_env(env: dict[str, str] | None = None) -> float:
    """``LOCAL_SLA_SCALE``: the factor fast_clock applies to the timers (0 < x <= 1)."""
    env = os.environ if env is None else env
    raw = env.get("LOCAL_SLA_SCALE")
    try:
        value = float(raw) if raw not in (None, "") else DEFAULT_SLA_SCALE
    except ValueError:
        value = DEFAULT_SLA_SCALE
    return value if 0 < value <= 1 else DEFAULT_SLA_SCALE


def short_hash(text: str | None) -> str:
    """First 16 hex of sha256, or "-" for no text. Traces carry hashes, never the text."""
    if not text:
        return "-"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class LocalLifecycle:
    """In-process lifecycle for STAGE=local and tests. Thread safe (a re-entrant lock per case)."""

    def __init__(self, stores: Stores, clock: Callable[[], datetime] | None = None,
                 gateway: Any = default_gateway, delay_seconds: float | None = None,
                 close_after_seconds: float = DEFAULT_CLOSE_AFTER_SECONDS,
                 investigator: Callable[..., dict] = investigate,
                 sleep: Callable[[float], None] = time.sleep, sla_scale: float | None = None) -> None:
        self.stores = stores
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.gw = gateway
        self.delay = timedelta(seconds=delay_from_env() if delay_seconds is None else max(0.0, delay_seconds))
        self.close_after = timedelta(seconds=close_after_seconds)
        self.investigator = investigator
        self.sleep = sleep
        self.advance_budget_seconds = DEFAULT_ADVANCE_BUDGET_SECONDS
        self.sla_scale = sla_scale_from_env() if sla_scale is None else sla_scale
        self._lock = threading.RLock()  # guards the dicts below only; never held while working
        self._case_locks: dict[str, threading.RLock] = {}
        self._due: dict[str, datetime] = {}  # case_id -> when the investigation is due
        self._steps: dict[str, int] = {}  # case_id -> last trace step number

    def _case_lock(self, case_id: str) -> threading.RLock:
        with self._lock:
            lock = self._case_locks.get(case_id)
            if lock is None:
                lock = self._case_locks[case_id] = threading.RLock()
            return lock

    # ------------------------------------------------------------ protocol

    def start(self, case_id: str, fast_clock: bool | None = None) -> None:
        with self._case_lock(case_id):
            stored = self.stores.cases.get(case_id)
            if stored is not None:
                self._start(stored, fast_clock)

    def advance(self, case_id: str, investigate: bool = True) -> StoredCase | None:
        with self._case_lock(case_id):
            stored = self.stores.cases.get(case_id)
            if stored is None:
                return None
            if stored.case.lane != Lane.B:
                with self._lock:
                    self._due.pop(case_id, None)
                return stored
            if stored.case.status == CaseStatus.open:
                self._start(stored)
                stored = self.stores.cases.get(case_id)
            if stored.case.status == CaseStatus.reopened:
                self._move(stored, CaseStatus.awaiting_analyst, "reopened_by_customer")
                stored = self.stores.cases.get(case_id)
            if stored.case.status == CaseStatus.investigating and investigate:
                with self._lock:
                    due = self._due.get(case_id)
                due = due or stored.case.updated_at or self.clock()
                if self.clock() >= due:
                    self._investigate(stored)
                    stored = self.stores.cases.get(case_id)
            if stored.case.sla_timers:
                self._fire_due(stored)
                stored = self.stores.cases.get(case_id)
            case = stored.case
            if (case.status == CaseStatus.notified and case.resolution is not None and not case.awaiting_customer
                    and self.clock() >= case.resolution.sent_at + self.close_after):
                self._move(stored, CaseStatus.closed, "notified_closed_after_timer")
                stored = self.stores.cases.get(case_id)
            return stored

    def advance_all(self) -> None:
        all_cases = getattr(self.stores.cases, "all", None)
        if all_cases is None:
            return
        started = time.perf_counter()
        for stored in all_cases():
            if stored.case.lane == Lane.B and (stored.case.sla_timers or stored.case.status in (
                    CaseStatus.open, CaseStatus.investigating, CaseStatus.notified, CaseStatus.reopened)):
                lock = self._case_lock(stored.case.case_id)
                if not lock.acquire(blocking=False):
                    continue  # someone is working on it (an investigation): the next poll shows it
                try:
                    within = time.perf_counter() - started < self.advance_budget_seconds
                    self.advance(stored.case.case_id, investigate=within)
                finally:
                    lock.release()

    def on_decision(self, case_id: str, expected_version: int, decision: AnalystDecision,
                    resolution: Resolution | None, labels: list[str]) -> CaseRecord:
        with self._case_lock(case_id):
            stored = self.stores.cases.get(case_id)
            if stored is None:
                raise C.ApiFailure("not_authorized", "not authorized")
            case = stored.case
            if case.analyst_decision is not None:
                raise C.ApiFailure("conflict", "case already decided")
            if case.version != expected_version:
                raise C.ApiFailure("conflict", "stale version: reload the case")
            if case.status != CaseStatus.awaiting_analyst or case.lane != Lane.B:
                raise C.ApiFailure("precondition", "case is not awaiting_analyst")
            before = case.status
            cancelled = list(case.sla_timers)
            case.sla_timers = []
            case.taken_at = case.taken_at or self.clock()
            case.analyst_decision = decision
            case.labels_emitted = list(case.labels_emitted) + [x for x in labels if x not in case.labels_emitted]
            if decision.action == "reject" and decision.next == "escalate":
                case.status = CaseStatus.handed_off
                case.handoff.queue = SENIOR_QUEUE
                case.handoff.priority = case.handoff.priority or "normal"
                note = "Escalado por un analista a la cola senior (motivo en analyst_decision)"
                if note not in case.handoff.open_questions:
                    case.handoff.open_questions.append(note)
            else:
                case.status = CaseStatus.notified
                case.resolution = resolution
                case.awaiting_customer = self._asks_customer(case_id, decision)
            try:
                self.stores.cases.put(case, stored.session_id)
            except CaseVersionConflict:
                raise C.ApiFailure("conflict", "stale version: reload the case")
            self._emit(case, stored.session_id, "human", "analyst_decision",
                       inp=f"case:{case_id}:v{expected_version}:by:{decision.decided_by}",
                       out=(f"action:{decision.action};next:{decision.next or '-'};edited:{decision.edited};"
                            f"reason_sha256:{short_hash(decision.reason)};"
                            f"reason_len:{len(decision.reason or '')};"
                            f"draft_sha256:{short_hash(decision.draft_text)};"
                            f"sent_sha256:{short_hash(decision.sent_text)};"
                            f"labels:{','.join(labels)}"))
            self._emit(case, stored.session_id, "rule", "lifecycle.transition",
                       inp=f"status:{before.value}", out=f"status:{case.status.value}")
            if cancelled:
                self._emit(case, stored.session_id, "rule", "sla_timer.cancel", inp=f"case:{case_id}",
                           out=f"kinds:{','.join(cancelled)};why:decided")
            return case

    def on_opened(self, case_id: str, analyst: str) -> StoredCase | None:
        with self._case_lock(case_id):
            stored = self.stores.cases.get(case_id)
            if stored is None:
                return None
            case = stored.case
            if case.lane != Lane.B or case.taken_at is not None or case.analyst_decision is not None:
                return stored
            case.taken_at = self.clock()
            cancelled = [k for k in case.sla_timers if k == "unassigned"]
            case.sla_timers = [k for k in case.sla_timers if k != "unassigned"]
            try:
                self.stores.cases.put(case, stored.session_id)
            except CaseVersionConflict:
                return self.stores.cases.get(case_id)  # someone wrote first; the next open retries
            self._emit(case, stored.session_id, "human", "case_taken", inp=f"case:{case_id}",
                       out=f"by_sha256:{short_hash(analyst)}")
            if cancelled:
                self._emit(case, stored.session_id, "rule", "sla_timer.cancel", inp=f"case:{case_id}",
                           out="kinds:unassigned;why:taken")
            return self.stores.cases.get(case_id)

    def set_fast_clock(self, case_id: str, on: bool) -> None:
        with self._case_lock(case_id):
            stored = self.stores.cases.get(case_id)
            if stored is None or not stored.case.sla_timers:
                return
            case = stored.case
            scale = self.sla_scale if on else 1.0
            if case.timer_scale == scale:
                return
            case.timer_scale = scale
            try:
                self.stores.cases.put(case, stored.session_id)
            except CaseVersionConflict:
                return
            self._emit(case, stored.session_id, "rule", "sla_timer.rescale", inp=f"case:{case_id}",
                       out=f"scale:{scale:g};{self._schedule_ref(case)}")

    def has_pending_timers(self, case_id: str) -> bool:
        stored = self.stores.cases.get(case_id)
        return bool(stored and stored.case.sla_timers and self._timers_live(stored.case))

    @staticmethod
    def _timers_live(case: CaseRecord) -> bool:
        """The SLA timers of the case may still fire: lane B, open for the SLA, not decided."""
        return case.lane == Lane.B and case.status in TIMER_STATUSES and case.analyst_decision is None

    def fire_at(self, case: CaseRecord, kind: str) -> datetime | None:
        """When a timer fires: the promise.py time, scaled from created_at by timer_scale."""
        at = getattr(case.clock, SLA_TIMER_CLOCK_FIELD[kind])
        if at is None:
            return None
        return case.created_at + (at - case.created_at) * case.timer_scale

    # ------------------------------------------------------------ internals

    def _asks_customer(self, case_id: str, decision: AnalystDecision) -> bool:
        """The sent text asks the customer for information: reject + request_information, or
        approve / edit of a report that recommends request_information."""
        if decision.action == "reject":
            return decision.next == "request_information"
        report = self.stores.reviews.get_report(case_id) or {}
        return report.get("recommendation") == "request_information"

    def _start(self, stored: StoredCase, fast_clock: bool | None = None) -> None:
        case = stored.case
        if case.lane != Lane.B or case.status != CaseStatus.open:
            return
        if fast_clock is None:
            fast_clock = self._session_fast_clock(stored.session_id)
        case.timer_scale = self.sla_scale if fast_clock else 1.0
        case.sla_timers = [k for k in SLA_TIMER_KINDS if getattr(case.clock, SLA_TIMER_CLOCK_FIELD[k]) is not None]
        if case.sla_timers:
            self._emit(case, stored.session_id, "rule", "sla_timer.schedule", inp=f"case:{case.case_id}",
                       out=f"scale:{case.timer_scale:g};{self._schedule_ref(case)}")
        if is_money_case(case):
            with self._lock:
                self._due[case.case_id] = self.clock() + self.delay
            self._move(stored, CaseStatus.investigating, "open_money_case")
        else:
            self._move(stored, CaseStatus.awaiting_analyst, "open_non_money_case_no_investigator")

    def _session_fast_clock(self, session_id: str) -> bool:
        """The demo clock switch of the session that opened the case (False if it is gone)."""
        try:
            data = self.stores.sessions.get(session_id) or {}
        except Exception:
            return False
        return bool(data.get("fast_clock"))

    def _schedule_ref(self, case: CaseRecord) -> str:
        return ",".join(f"{k}@{self.fire_at(case, k).isoformat()}" for k in case.sla_timers
                        if self.fire_at(case, k) is not None)

    def _fire_due(self, stored: StoredCase) -> None:
        """Fire the SLA timers that are due, in order, with one conditional write."""
        case = stored.case
        now = self.clock()
        if not self._timers_live(case):
            # Not open for the SLA: nothing fires. A schedule left behind is deleted, like an
            # EventBridge one-time schedule whose target found the case answered (the decision
            # normally deleted them already); otherwise the live card would poll forever.
            left = list(case.sla_timers)
            case.sla_timers = []
            try:
                self.stores.cases.put(case, stored.session_id)
            except CaseVersionConflict:
                return
            self._emit(case, stored.session_id, "rule", "sla_timer.cancel", inp=f"case:{case.case_id}",
                       out=f"kinds:{','.join(left)};why:not_open:{case.status.value}")
            return
        pending = sorted(case.sla_timers, key=SLA_TIMER_KINDS.index)
        due = [k for k in pending if (self.fire_at(case, k) or now) <= now]
        if not due:
            return
        fired: list[tuple[str, datetime, str]] = []
        notices: list[Notice] = []
        for kind in due:
            at = self.fire_at(case, kind) or now
            case.sla_timers = [k for k in case.sla_timers if k != kind]
            if kind == "unassigned" and case.taken_at is not None:
                continue  # taken meanwhile (its schedule was already deleted)
            if kind == "sla_80":
                notices += self._notice(case, "sla_80", now)
                effect = "notice:sla_80"
            elif kind == "breached":
                case.handoff.queue = SENIOR_QUEUE
                case.handoff.priority = "high"
                if BREACH_NOTE not in case.handoff.open_questions:
                    case.handoff.open_questions.append(BREACH_NOTE)
                if "sla_breached" not in case.urgency_flags:
                    case.urgency_flags.append("sla_breached")
                notices += self._notice(case, "escalated", now)
                effect = f"queue:{SENIOR_QUEUE};priority:high;notice:escalated"
            else:
                effect = "alert:unassigned"
            case.clock_events.append(ClockEvent(kind=kind, fired_at=now))
            fired.append((kind, at, effect))
        try:
            self.stores.cases.put(case, stored.session_id)
        except CaseVersionConflict:
            return  # someone wrote first; the next read fires them
        for kind, at, effect in fired:
            self._emit(case, stored.session_id, "rule", f"sla_timer.{kind}",
                       inp=f"case:{case.case_id};due:{at.isoformat()};scale:{case.timer_scale:g}",
                       out=f"status:{case.status.value};{effect}")
        append = getattr(self.stores.reviews, "append_conversation", None)
        if notices and append is not None:
            try:
                append(case.case_id, [{"role": "system", "text": n.text, "language": n.language,
                                       "source": "template", "at": n.at.isoformat()} for n in notices])
            except Exception:
                pass

    def _notice(self, case: CaseRecord, kind: str, now: datetime) -> list[Notice]:
        """The proactive notice of ``kind`` (once per case), with the promised date. When that
        date is before the day the timer stands for (its planned time in the customer's zone:
        with the demo clock the story is at the planned time, not at the wall clock), the
        ``_date_passed`` wording says the date passed instead of "sigue siendo el <fecha>"."""
        if any(n.kind == kind for n in case.notices):
            return []
        lang = case.language
        expected = case.promise.expected_date
        planned = case.clock.breach_at if kind == "escalated" else case.clock.sla_alert_at
        story_day = local_datetime(planned or now, case.country).date()
        if expected is not None and expected < story_day:
            text = render(f"sla_notice.{kind}_date_passed", lang, case.country, case_id=case.case_id)
        else:
            date = format_date(expected, lang) if expected else render("investigator.no_date", lang)
            text = render(f"sla_notice.{kind}", lang, case.country, case_id=case.case_id, expected_date=date)
        if forbidden_hits(text):  # a template never promises money; belt and braces
            return []
        notice = Notice(kind=kind, text=text, language=lang, at=now)
        case.notices.append(notice)
        return [notice]

    def _move(self, stored: StoredCase, to: CaseStatus, why: str) -> bool:
        case = stored.case
        before = case.status
        case.status = to
        try:
            self.stores.cases.put(case, stored.session_id)
        except CaseVersionConflict:
            return False  # someone wrote first; the next read retries
        self._emit(case, stored.session_id, "rule", "lifecycle.transition", inp=f"status:{before.value}",
                   out=f"status:{to.value};why:{why}")
        return True

    def _investigate(self, stored: StoredCase) -> None:
        case = stored.case
        ctx = self.gw.GatewayContext(customer_id=case.customer_id, session_id=stored.session_id,
                                     trace_id=f"lc-{case.case_id}")
        started = time.perf_counter()

        def on_tool(call: ToolCall) -> None:
            self._emit(case, stored.session_id, "tool", call.name, inp=call.input_ref, out=call.output_ref,
                       latency_ms=call.latency_ms, error=call.error_code)

        error, report = None, None
        try:
            raw = self.investigator(case, self.gw, ctx, self.clock, sleep=self.sleep, on_tool=on_tool)
            report = C.InvestigatorReport.model_validate(raw).model_dump()
            if forbidden_hits(report["draft_reply"]["text"]):
                raise ValueError("draft promises a refund or compensation")
            if report["draft_reply"]["language"] != case.language:
                raise ValueError("draft language differs from the case language")
        except ToolsUnavailable:
            error = "tool_unavailable"
        except ToolBudgetExceeded:
            error = "tool_budget_exceeded"
        except (ValidationError, ValueError):
            error = "invalid_report"
        latency = int(round((time.perf_counter() - started) * 1000))
        with self._lock:
            self._due.pop(case.case_id, None)
        if report is not None:
            self.stores.reviews.put_report(case.case_id, report)
            case.investigation_id = report["report_id"]
            self._emit(case, stored.session_id, "rule", "investigator_stub", version=report["prompt_version"],
                       inp=f"case:{case.case_id}",
                       out=(f"report:{report['report_id']};rec:{report['recommendation']};"
                            f"removed:{report['removed_claims']};tools:{report['tool_calls']}"),
                       latency_ms=latency)
        else:
            note = f"El investigador no produjo reporte ({error}): revisar la evidencia a mano"
            if note not in case.handoff.open_questions:
                case.handoff.open_questions.append(note)
            if "evidence_incomplete" not in case.urgency_flags:
                case.urgency_flags.append("evidence_incomplete")
            self._emit(case, stored.session_id, "rule", "investigator_stub", version=None,
                       inp=f"case:{case.case_id}", out=None, latency_ms=latency, error=error)
        self._move(stored, CaseStatus.awaiting_analyst, "report_ready" if report else f"no_report:{error}")

    def _emit(self, case: CaseRecord, session_id: str, actor: str, name: str, inp: str | None = None,
              out: str | None = None, version: str | None = None, latency_ms: int = 0,
              error: str | None = None) -> None:
        with self._lock:
            step = self._steps.get(case.case_id, 0) + 1
            self._steps[case.case_id] = step
        event = {
            "trace_id": f"lc-{case.case_id}", "session_id": session_id, "case_id": case.case_id, "turn": None,
            "step": step, "actor": actor, "name": name, "input_ref": inp, "output_ref": out,
            "latency_ms": latency_ms, "tokens_in": None, "tokens_out": None, "cost_usd": 0.0,
            "version": version, "error_code": error, "ts": self.clock().isoformat(), "source": "demo",
        }
        try:
            self.stores.traces.emit(event)
        except Exception:
            pass  # tracing never breaks the lifecycle


def make_lifecycle(env: dict[str, str], stores: Stores, clock: Callable[[], datetime] | None = None,
                   delay_seconds: float | None = None, sleep: Callable[[float], None] = time.sleep,
                   sla_scale: float | None = None) -> CaseLifecycle:
    """``LIFECYCLE_BACKEND``: ``local`` (default with STAGE=local or STORE_BACKEND=memory) or
    ``stepfunctions`` (Andrés; not in this repo yet, so it fails loudly instead of pretending)."""
    backend = (env.get("LIFECYCLE_BACKEND") or "").strip().lower() or "local"
    if backend == "local":
        if delay_seconds is None:
            delay_seconds = delay_from_env(env)
        if sla_scale is None:
            sla_scale = sla_scale_from_env(env)
        return LocalLifecycle(stores, clock=clock, delay_seconds=delay_seconds, sleep=sleep, sla_scale=sla_scale)
    if backend == "stepfunctions":
        raise StoreConfigError("LIFECYCLE_BACKEND=stepfunctions is Andrés's (Step Functions); not in this repo yet")
    raise StoreConfigError(f"unknown LIFECYCLE_BACKEND={backend!r}; use 'local' or 'stepfunctions'")
