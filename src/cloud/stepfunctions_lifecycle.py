"""``LIFECYCLE_BACKEND=stepfunctions``: the case lifecycle on the state machine of
``infra/stacks/workflow_stack.py`` (``CaseLifecycle`` of ``conversation/lifecycle.py``).

The API side (``StepFunctionsLifecycle``: ``start``, ``advance``, ``on_decision`` ...) and the steps the
workflow Lambdas run (``register_task_token``, ``mark_incomplete``, ``notify_customer``,
``close_case``, ``fire_sla_timer``, ``run_investigation``) live in one class so they share the
record rules of ``LocalLifecycle`` (it is a subclass: the decision, the SLA timer effects, the
notices and the transitions are the same code as locally) and the trace writer.

State machine contract (execution name ``<case_id>-r<round>``, input
``{"case_id", "money_dispute", "round"}``):

    MoneyDispute? --yes--> Investigate (investigator Lambda; any failure -> MarkInvestigationIncomplete)
         |                      |
         no                     v
         +---------------> AwaitAnalyst  (case-steps register_task_token, .waitForTaskToken,
                                |         payload {case_id, task_token, execution_arn}, output -> $.decision)
                                v
                          NotifyCustomer (case-steps notify_customer -> $.notify {close, close_after_seconds})
                                |
                    $.notify.close? --yes--> Wait close_after_seconds -> CloseCase (case-steps close_case)
                                |
                                no -> done (handed_off, or waiting for the customer's answer)

- ``start`` (API, after the turn that saved a lane B case): StartExecution of round 1 (money cases
  investigate, the rest go straight to the analyst), the case moves to ``investigating`` (money)
  and its SLA timers become one-time EventBridge Scheduler schedules ``<case_id>-<kind>`` in
  ``SLA_SCHEDULE_GROUP`` that invoke case-steps ``{"action": "sla_timer", case_id, kind}``.
  A reopened case (the customer answered or disagreed, see the orchestrator) gets the next round,
  straight to the analyst, without timers (the reopen already sent it to the senior queue).
  The execution name makes ``start`` idempotent; a failure never breaks the chat turn (it is
  traced and the next turn's ``start`` retries).
- ``awaiting_analyst`` is set ONLY by ``register_task_token``, after the token is stored on the
  case item (internal attribute, never in the CaseRecord or a view): a case the analyst can
  decide always has a token to answer. A retried AwaitAnalyst task (new token) on a case already
  decided is answered at once with the stored decision.
- ``on_decision`` (API) = the local conditional write on ``version`` + SendTaskSuccess with the
  stored token (output ``{action, next, status}``) + DeleteSchedule of the pending SLA timers.
  Once the write succeeded the decision stands: a token that AWS rejects (timed out) is traced
  (``task_error``) instead of failing the analyst's request.
- ``advance`` and ``advance_all`` only read: in the cloud the transitions run in the workflow
  Lambdas and the timers in EventBridge Scheduler, never inside a chat or console request.
- Traces: the same #7 events as locally (``trace_id = lc-<case_id>``); ``step`` comes from an
  atomic counter on the case item, so events written by different Lambdas keep one order.
- Demo clock (``fast_clock`` of a judge-mode session): the schedules are created at the scaled
  times (``LOCAL_SLA_SCALE``, 1 day = 1 minute by default) and re-created when the switch changes;
  EventBridge fires them with about a minute of precision (never earlier than 60 s from now).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import ValidationError

from conversation import contract as C
from conversation import demo_gateway as default_gateway
from conversation.case import AnalystDecision, CaseRecord, CaseStatus, Lane, Resolution
from conversation.investigator_stub import ToolBudgetExceeded, ToolCall, ToolsUnavailable, is_money_case
from conversation.lifecycle import LocalLifecycle, sla_scale_from_env
from conversation.store import CaseVersionConflict, StoreConfigError, StoredCase, Stores
from conversation.templates import forbidden_hits

logger = logging.getLogger(__name__)

DEFAULT_CLOSE_AFTER_SECONDS = 30.0
MIN_SCHEDULE_LEAD_SECONDS = 60
WRITE_ATTEMPTS = 5
OPEN_FOR_TEAM = (CaseStatus.open, CaseStatus.investigating, CaseStatus.reopened)
DECIDED = (CaseStatus.notified, CaseStatus.closed, CaseStatus.handed_off)
_ERROR_CODE = re.compile(r"[^a-z0-9_.]+")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def error_code_of(error: Any) -> str:
    """A short trace-safe code for a Step Functions error name (``States.Timeout`` -> ``states.timeout``)."""
    text = _ERROR_CODE.sub("_", str(error or "investigator_failed").strip().lower()).strip("_")
    return (text or "investigator_failed")[:40]


class StepFunctionsLifecycle(LocalLifecycle):
    """CaseLifecycle on Step Functions + EventBridge Scheduler, and the workflow steps."""

    def __init__(self, stores: Stores, *, sfn: Any = None, scheduler: Any = None,
                 state_machine_arn: str | None = None, schedule_group: str | None = None,
                 scheduler_role_arn: str | None = None, timer_target_arn: str | None = None,
                 clock: Callable[[], datetime] | None = None, sla_scale: float | None = None,
                 close_after_seconds: float = DEFAULT_CLOSE_AFTER_SECONDS, gateway: Any = default_gateway,
                 min_lead_seconds: int = MIN_SCHEDULE_LEAD_SECONDS) -> None:
        super().__init__(stores, clock=clock, gateway=gateway, delay_seconds=0.0,
                         close_after_seconds=close_after_seconds, sla_scale=sla_scale)
        self._real_clock = clock or _utc_now
        self._clock_floor: datetime | None = None
        self.clock = self._now  # LocalLifecycle code reads self.clock()
        self._sfn = sfn
        self._scheduler = scheduler
        self.state_machine_arn = state_machine_arn or None
        self.schedule_group = schedule_group or None
        self.scheduler_role_arn = scheduler_role_arn or None
        self.timer_target_arn = timer_target_arn or None
        self.min_lead = timedelta(seconds=max(0, int(min_lead_seconds)))

    @classmethod
    def from_env(cls, env: dict[str, str], stores: Stores, clock: Callable[[], datetime] | None = None,
                 gateway: Any = default_gateway, sla_scale: float | None = None) -> "StepFunctionsLifecycle":
        """From the #9 variables: ``CASE_STATE_MACHINE_ARN`` (needed by ``start``, API only),
        ``SLA_SCHEDULE_GROUP``, ``SLA_SCHEDULER_ROLE_ARN``, ``SLA_TARGET_FUNCTION_ARN`` (SLA timers),
        ``CASE_CLOSE_AFTER_SECONDS`` (30), ``LOCAL_SLA_SCALE`` (demo clock factor)."""
        try:
            close_after = float(env.get("CASE_CLOSE_AFTER_SECONDS") or DEFAULT_CLOSE_AFTER_SECONDS)
        except ValueError:
            close_after = DEFAULT_CLOSE_AFTER_SECONDS
        return cls(stores, state_machine_arn=env.get("CASE_STATE_MACHINE_ARN"),
                   schedule_group=env.get("SLA_SCHEDULE_GROUP"), scheduler_role_arn=env.get("SLA_SCHEDULER_ROLE_ARN"),
                   timer_target_arn=env.get("SLA_TARGET_FUNCTION_ARN"), clock=clock, gateway=gateway,
                   sla_scale=sla_scale_from_env(env) if sla_scale is None else sla_scale,
                   close_after_seconds=max(0.0, close_after))

    # ------------------------------------------------------------ clients and clock

    def _now(self) -> datetime:
        now = self._real_clock()
        if self._clock_floor is not None and self._clock_floor > now:
            return self._clock_floor
        return now

    @property
    def sfn(self) -> Any:
        if self._sfn is None:
            from cloud import aws

            self._sfn = aws.client("stepfunctions")
        return self._sfn

    @property
    def scheduler(self) -> Any:
        if self._scheduler is None:
            from cloud import aws

            self._scheduler = aws.client("scheduler")
        return self._scheduler

    def _internal(self, case_id: str) -> dict[str, Any]:
        fn = getattr(self.stores.cases, "get_internal", None)
        return fn(case_id) if fn is not None else {}

    def _set_internal(self, case_id: str, **attrs: Any) -> bool:
        fn = getattr(self.stores.cases, "set_internal", None)
        return bool(fn(case_id, **attrs)) if fn is not None else False

    def _remove_internal(self, case_id: str, *names: str) -> None:
        fn = getattr(self.stores.cases, "remove_internal", None)
        if fn is not None:
            fn(case_id, *names)

    # ------------------------------------------------------------ CaseLifecycle (API side)

    def start(self, case_id: str, fast_clock: bool | None = None) -> None:
        try:
            with self._case_lock(case_id):
                self._start_round(case_id, fast_clock)
        except Exception as e:  # never breaks the chat turn; the next turn's start retries
            logger.error("lifecycle start of %s failed: %s", case_id, type(e).__name__)
            stored = self._safe_get(case_id)
            if stored is not None:
                self._emit(stored.case, stored.session_id, "rule", "lifecycle.start", inp=f"case:{case_id}",
                           out=None, error=_code(e))

    def advance(self, case_id: str, investigate: bool = True) -> StoredCase | None:
        return self.stores.cases.get(case_id)  # read only: AWS runs the transitions

    def advance_all(self) -> None:
        return None

    def on_decision(self, case_id: str, expected_version: int, decision: AnalystDecision,
                    resolution: Resolution | None, labels: list[str]) -> CaseRecord:
        with self._case_lock(case_id):
            before = self.stores.cases.get(case_id)
            pending = list(before.case.sla_timers) if before is not None else []
            case = super().on_decision(case_id, expected_version, decision, resolution, labels)
            stored = self.stores.cases.get(case_id)
            session_id = stored.session_id if stored is not None else (before.session_id if before else "")
            internal = self._internal(case_id)
            self._set_internal(case_id, decided_round=int(internal.get("execution_round") or 0))
            self._answer_token(case, session_id, internal.get("task_token"), late=False)
            self._delete_schedules(case_id, pending)
            return case

    def on_opened(self, case_id: str, analyst: str) -> StoredCase | None:
        with self._case_lock(case_id):
            before = self.stores.cases.get(case_id)
            had = before is not None and "unassigned" in before.case.sla_timers
            stored = super().on_opened(case_id, analyst)
            if had and stored is not None and "unassigned" not in stored.case.sla_timers:
                self._delete_schedules(case_id, ["unassigned"])
            return stored

    def set_fast_clock(self, case_id: str, on: bool) -> None:
        with self._case_lock(case_id):
            before = self.stores.cases.get(case_id)
            if before is None or not before.case.sla_timers:
                return
            super().set_fast_clock(case_id, on)
            after = self.stores.cases.get(case_id)
            if after is not None and after.case.timer_scale != before.case.timer_scale and self._timers_live(after.case):
                self._schedule(after.case, after.session_id, list(after.case.sla_timers), replace=True)

    # ------------------------------------------------------------ start

    def _start_round(self, case_id: str, fast_clock: bool | None) -> None:
        stored = self.stores.cases.get(case_id)
        if stored is None or stored.case.lane != Lane.B or stored.case.analyst_decision is not None:
            return
        case = stored.case
        internal = self._internal(case_id)
        started = int(internal.get("execution_round") or 0)
        decided = int(internal.get("decided_round") or 0)
        if case.status == CaseStatus.open and started == 0:
            rnd, money = 1, is_money_case(case)
        elif case.status == CaseStatus.reopened and started <= decided:
            rnd, money = started + 1, False
        else:
            return  # an execution for this round already exists
        if not self.state_machine_arn:
            raise StoreConfigError("LIFECYCLE_BACKEND=stepfunctions needs CASE_STATE_MACHINE_ARN (WorkflowStack, #9)")
        arn = self._start_execution(case_id, rnd, money)
        self._set_internal(case_id, condition="attribute_not_exists(#execution_round) OR #execution_round < :rnd",
                           condition_values={":rnd": {"N": str(rnd)}}, execution_round=rnd, execution_arn=arn)
        self._emit(case, stored.session_id, "rule", "lifecycle.start", inp=f"case:{case_id}",
                   out=f"round:{rnd};money_dispute:{str(money).lower()}")
        if rnd == 1:
            self._first_round(case_id, fast_clock, money)

    def _start_execution(self, case_id: str, rnd: int, money: bool) -> str:
        name = f"{case_id}-r{rnd}"
        payload = json.dumps({"case_id": case_id, "money_dispute": money, "round": rnd}, separators=(",", ":"))
        try:
            return self.sfn.start_execution(stateMachineArn=self.state_machine_arn, name=name,
                                            input=payload)["executionArn"]
        except Exception as e:
            if _aws_code(e) != "ExecutionAlreadyExists":
                raise
            # started by an earlier attempt whose bookkeeping failed: same name, so same execution
            return self.state_machine_arn.replace(":stateMachine:", ":execution:") + f":{name}"

    def _first_round(self, case_id: str, fast_clock: bool | None, money: bool) -> None:
        """Timers + ``investigating`` for a money case, with retries on concurrent writes."""
        for _ in range(WRITE_ATTEMPTS):
            stored = self.stores.cases.get(case_id)
            if stored is None:
                return
            case = stored.case
            before = case.status
            kinds: list[str] = []
            if not case.sla_timers and not case.clock_events and case.analyst_decision is None:
                if fast_clock is None:
                    fast_clock = self._session_fast_clock(stored.session_id)
                case.timer_scale = self.sla_scale if fast_clock else 1.0
                kinds = [k for k in ("unassigned", "sla_80", "breached") if self.fire_at(case, k) is not None]
                case.sla_timers = list(kinds)
            if money and case.status == CaseStatus.open:
                case.status = CaseStatus.investigating
            if not kinds and case.status == before:
                return
            try:
                self.stores.cases.put(case, stored.session_id)
            except CaseVersionConflict:
                continue
            if kinds:
                self._emit(case, stored.session_id, "rule", "sla_timer.schedule", inp=f"case:{case_id}",
                           out=f"scale:{case.timer_scale:g};{self._schedule_ref(case)}")
                self._schedule(case, stored.session_id, kinds)
            if case.status != before:
                self._emit(case, stored.session_id, "rule", "lifecycle.transition", inp=f"status:{before.value}",
                           out=f"status:{case.status.value};why:open_money_case")
            return

    # ------------------------------------------------------------ SLA schedules

    def _schedule_ready(self) -> bool:
        return bool(self.schedule_group and self.scheduler_role_arn and self.timer_target_arn)

    def _schedule(self, case: CaseRecord, session_id: str, kinds: list[str], replace: bool = False) -> None:
        if not kinds:
            return
        if not self._schedule_ready():
            self._emit(case, session_id, "rule", "sla_timer.schedule", inp=f"case:{case.case_id}", out=None,
                       error="scheduler_not_configured")
            return
        now = self._real_clock()
        for kind in kinds:
            at = self.fire_at(case, kind)
            if at is None:
                continue
            at = max(at.astimezone(timezone.utc), now + self.min_lead)
            params = {
                "Name": f"{case.case_id}-{kind}", "GroupName": self.schedule_group,
                "ScheduleExpression": f"at({at:%Y-%m-%dT%H:%M:%S})", "ScheduleExpressionTimezone": "UTC",
                "FlexibleTimeWindow": {"Mode": "OFF"}, "ActionAfterCompletion": "DELETE",
                "Target": {"Arn": self.timer_target_arn, "RoleArn": self.scheduler_role_arn,
                           "Input": json.dumps({"action": "sla_timer", "case_id": case.case_id, "kind": kind})},
            }
            try:
                if replace:
                    self._delete_schedule(params["Name"])
                try:
                    self.scheduler.create_schedule(**params)
                except Exception as e:
                    if _aws_code(e) != "ConflictException":
                        raise
                    # a retried start: replace it (the API role may create and delete, not update)
                    self._delete_schedule(params["Name"])
                    self.scheduler.create_schedule(**params)
            except Exception as e:
                self._emit(case, session_id, "rule", "sla_timer.schedule", inp=f"case:{case.case_id};kind:{kind}",
                           out=None, error=_code(e))

    def _delete_schedule(self, name: str) -> None:
        try:
            self.scheduler.delete_schedule(Name=name, GroupName=self.schedule_group)
        except Exception as e:
            if _aws_code(e) != "ResourceNotFoundException":
                raise

    def _delete_schedules(self, case_id: str, kinds: list[str]) -> None:
        if not kinds or not self._schedule_ready():
            return
        for kind in kinds:
            try:
                self._delete_schedule(f"{case_id}-{kind}")
            except Exception as e:  # a leftover schedule only fires into a no-op
                logger.warning("delete schedule %s-%s failed: %s", case_id, kind, _aws_code(e) or type(e).__name__)

    # ------------------------------------------------------------ task token

    def _answer_token(self, case: CaseRecord, session_id: str, token: str | None, late: bool) -> bool:
        """SendTaskSuccess with the decision; traced; never raises."""
        d = case.analyst_decision
        output = {"action": d.action if d else "none", "next": d.next if d else None, "status": case.status.value}
        if late:
            output["late"] = True
        if not token:
            self._emit(case, session_id, "rule", "lifecycle.task_success", inp=f"case:{case.case_id}", out=None,
                       error="no_task_token")
            return False
        try:
            self.sfn.send_task_success(taskToken=token, output=json.dumps(output, separators=(",", ":")))
        except Exception as e:
            code = _aws_code(e) or type(e).__name__
            try:
                self._set_internal(case.case_id, task_error=code)
            except Exception:
                pass
            self._emit(case, session_id, "rule", "lifecycle.task_success", inp=f"case:{case.case_id}", out=None,
                       error=_code(e))
            return False
        try:
            if self._internal(case.case_id).get("task_token") == token:  # never a newer round's token
                self._remove_internal(case.case_id, "task_token")
        except Exception:
            pass
        self._emit(case, session_id, "rule", "lifecycle.task_success", inp=f"case:{case.case_id}",
                   out=f"action:{output['action']};status:{output['status']}" + (";late:true" if late else ""))
        return True

    # ------------------------------------------------------------ workflow steps (case-steps Lambda)

    def register_task_token(self, case_id: str, task_token: str, execution_arn: str | None = None,
                            round_no: int | None = None) -> dict[str, Any]:
        """AwaitAnalyst: store the token, then (and only then) the case is ``awaiting_analyst``."""
        if not task_token:
            raise ValueError("register_task_token needs task_token")
        stored = self.stores.cases.get(case_id)
        if stored is None:
            self._fail_token(task_token, "CaseNotFound", f"case {case_id} does not exist")
            return {"case_id": case_id, "status": "not_found"}
        latest = int(self._internal(case_id).get("execution_round") or 0)
        if round_no is not None and int(round_no) < latest:
            # an older round still waiting (a newer one was started): end it without touching the case
            self._answer_token(stored.case, stored.session_id, task_token, late=True)
            return {"case_id": case_id, "status": stored.case.status.value, "superseded": True}
        self._set_internal(case_id, task_token=task_token, task_token_at=self._real_clock().isoformat(),
                           **({"task_execution_arn": execution_arn} if execution_arn else {}))
        for _ in range(WRITE_ATTEMPTS):
            stored = self.stores.cases.get(case_id)
            case = stored.case
            if case.analyst_decision is not None or case.status in DECIDED:
                self._answer_token(case, stored.session_id, task_token, late=True)
                return {"case_id": case_id, "status": case.status.value, "answered": True}
            if case.status == CaseStatus.awaiting_analyst:
                return {"case_id": case_id, "status": case.status.value}
            if case.status not in OPEN_FOR_TEAM:
                self._answer_token(case, stored.session_id, task_token, late=True)  # nothing to wait for
                return {"case_id": case_id, "status": case.status.value, "answered": True}
            if self._move(stored, CaseStatus.awaiting_analyst, self._why_awaiting(case)):
                return {"case_id": case_id, "status": CaseStatus.awaiting_analyst.value}
        raise CaseVersionConflict(f"case {case_id}: could not move it to awaiting_analyst")

    @staticmethod
    def _why_awaiting(case: CaseRecord) -> str:
        if case.status == CaseStatus.reopened:
            return "reopened_by_customer"
        if case.status == CaseStatus.open:
            return "open_non_money_case_no_investigator"
        if case.investigation_id:
            return "report_ready"
        return "no_report"

    def _fail_token(self, token: str, error: str, cause: str) -> None:
        try:
            self.sfn.send_task_failure(taskToken=token, error=error, cause=cause[:200])
        except Exception as e:
            logger.warning("send_task_failure failed: %s", _aws_code(e) or type(e).__name__)

    def mark_incomplete(self, case_id: str, error: str | None = None,
                        trace_step: tuple[str, str] = ("model", "investigator")) -> dict[str, Any]:
        """The investigator failed (or timed out): the analyst sees it, the report cannot be approved."""
        code = error_code_of(error)
        stored = self._update(case_id, lambda case: _flag_incomplete(case, code))
        if stored is not None:
            actor, name = trace_step
            self._emit(stored.case, stored.session_id, actor, name, inp=f"case:{case_id}", out=None, error=code)
        return {"case_id": case_id, "status": "no_report", "error_code": code}

    def notify_customer(self, case_id: str, decision: dict[str, Any] | None = None) -> dict[str, Any]:
        """The resolution is already in the case (on_decision); the chat shows it. Says whether to close."""
        stored = self.stores.cases.get(case_id)
        if stored is None:
            return {"case_id": case_id, "status": "not_found", "close": False, "close_after_seconds": 0}
        case = stored.case
        close = case.status == CaseStatus.notified and case.resolution is not None and not case.awaiting_customer
        after = int(round(self.close_after.total_seconds())) if close else 0
        action = (decision or {}).get("action") if isinstance(decision, dict) else None
        self._emit(case, stored.session_id, "rule", "lifecycle.notify", inp=f"case:{case_id}",
                   out=f"status:{case.status.value};action:{action or '-'};close_after_s:{after}")
        return {"case_id": case_id, "status": case.status.value, "close": close, "close_after_seconds": after}

    def close_case(self, case_id: str) -> dict[str, Any]:
        """notified -> closed after the wait, unless the customer was asked for information or reopened it."""
        for _ in range(WRITE_ATTEMPTS):
            stored = self.stores.cases.get(case_id)
            if stored is None:
                return {"case_id": case_id, "closed": False, "status": "not_found"}
            case = stored.case
            if not (case.status == CaseStatus.notified and case.resolution is not None and not case.awaiting_customer):
                return {"case_id": case_id, "closed": False, "status": case.status.value}
            if self._move(stored, CaseStatus.closed, "notified_closed_after_timer"):
                return {"case_id": case_id, "closed": True, "status": CaseStatus.closed.value}
        raise CaseVersionConflict(f"case {case_id}: could not close it")

    def fire_sla_timer(self, case_id: str, kind: str) -> dict[str, Any]:
        """An EventBridge schedule fired: apply the timer like the local lifecycle does on read."""
        for _ in range(WRITE_ATTEMPTS):
            stored = self.stores.cases.get(case_id)
            if stored is None or kind not in stored.case.sla_timers:
                return {"case_id": case_id, "kind": kind, "fired": False}
            at = self.fire_at(stored.case, kind)
            self._clock_floor = at  # a schedule a few seconds early still fires its own timer
            try:
                self._fire_due(stored)
            finally:
                self._clock_floor = None
            after = self.stores.cases.get(case_id)
            if after is None or kind not in after.case.sla_timers:
                fired = after is not None and any(e.kind == kind for e in after.case.clock_events)
                return {"case_id": case_id, "kind": kind, "fired": fired}
        raise CaseVersionConflict(f"case {case_id}: could not fire {kind}")

    # ------------------------------------------------------------ investigator Lambda

    def run_investigation(self, case_id: str, investigator: Callable[..., dict], gateway: Any = None,
                          sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        """Investigate: report stored (ReviewStore) and linked, or the case flagged
        ``evidence_incomplete``. The status stays ``investigating``; AwaitAnalyst moves it on."""
        gw = gateway or self.gw
        stored = self.stores.cases.get(case_id)
        if stored is None:
            return {"case_id": case_id, "status": "not_found"}
        case = stored.case
        if case.lane != Lane.B or case.analyst_decision is not None or case.status not in (
                CaseStatus.open, CaseStatus.investigating):
            return {"case_id": case_id, "status": "skipped", "case_status": case.status.value}
        if case.status == CaseStatus.open:
            stored = self._update(case_id, _to_investigating) or stored
            if stored.case.status == CaseStatus.investigating:
                self._emit(stored.case, stored.session_id, "rule", "lifecycle.transition", inp="status:open",
                           out="status:investigating;why:open_money_case")
            case = stored.case
        existing = self.stores.reviews.get_report(case_id) if case.investigation_id else None
        if existing is not None and existing.get("report_id") == case.investigation_id:
            return {"case_id": case_id, "status": "report_ready", "report_id": case.investigation_id, "reused": True}

        ctx = gw.GatewayContext(customer_id=case.customer_id, session_id=stored.session_id, trace_id=f"lc-{case_id}")
        started = time.perf_counter()

        def on_tool(call: ToolCall) -> None:
            self._emit(case, stored.session_id, "tool", call.name, inp=call.input_ref, out=call.output_ref,
                       latency_ms=call.latency_ms, error=call.error_code)

        error, report = None, None
        try:
            raw = investigator(case, gw, ctx, self.clock, sleep=sleep, on_tool=on_tool)
            report = C.InvestigatorReport.model_validate(raw).model_dump()
            if forbidden_hits(report["draft_reply"]["text"]):
                raise ValueError("draft promises a refund or compensation")
            if report["draft_reply"]["language"] != case.language:
                raise ValueError("draft language differs from the case language")
        except ToolsUnavailable:
            error, report = "tool_unavailable", None
        except ToolBudgetExceeded:
            error, report = "tool_budget_exceeded", None
        except (ValidationError, ValueError):
            error, report = "invalid_report", None
        except RuntimeError as e:
            if type(e).__name__ != "InvestigatorError":  # G2: the model did not submit a usable report
                raise
            error, report = "investigator_error", None
        latency = int(round((time.perf_counter() - started) * 1000))
        actor, name = getattr(investigator, "trace_step", ("rule", "investigator_stub"))
        if report is not None:
            self.stores.reviews.put_report(case_id, report)
            report_id = report["report_id"]
            done = self._update(case_id, lambda c: _link_report(c, report_id)) or stored
            self._emit(done.case, done.session_id, actor, name, version=report["prompt_version"], inp=f"case:{case_id}",
                       out=(f"report:{report_id};rec:{report['recommendation']};"
                            f"removed:{report['removed_claims']};tools:{report['tool_calls']}"), latency_ms=latency)
            return {"case_id": case_id, "status": "report_ready", "report_id": report_id}
        done = self._update(case_id, lambda c: _flag_incomplete(c, error)) or stored
        self._emit(done.case, done.session_id, actor, name, version=None, inp=f"case:{case_id}", out=None,
                   latency_ms=latency, error=error)
        return {"case_id": case_id, "status": "no_report", "error_code": error}

    # ------------------------------------------------------------ helpers

    def _update(self, case_id: str, mutate: Callable[[CaseRecord], bool]) -> StoredCase | None:
        """Read-modify-write with a conditional put; retried on a concurrent write (a chat turn)."""
        for _ in range(WRITE_ATTEMPTS):
            stored = self.stores.cases.get(case_id)
            if stored is None:
                return None
            if not mutate(stored.case):
                return stored
            try:
                self.stores.cases.put(stored.case, stored.session_id)
                return stored
            except CaseVersionConflict:
                continue
        raise CaseVersionConflict(f"case {case_id}: too many concurrent writes")

    def _safe_get(self, case_id: str) -> StoredCase | None:
        try:
            return self.stores.cases.get(case_id)
        except Exception:
            return None

    def _emit(self, case: CaseRecord, session_id: str, actor: str, name: str, inp: str | None = None,
              out: str | None = None, version: str | None = None, latency_ms: int = 0,
              error: str | None = None) -> None:
        step = None
        counter = getattr(self.stores.cases, "next_trace_step", None)
        if counter is not None:
            try:
                step = counter(case.case_id)
            except Exception:
                step = None
        if step is None:
            with self._lock:
                step = self._steps.get(case.case_id, 0) + 1
                self._steps[case.case_id] = step
        event = {
            "trace_id": f"lc-{case.case_id}", "session_id": session_id, "case_id": case.case_id, "turn": None,
            "step": step, "actor": actor, "name": name, "input_ref": inp, "output_ref": out,
            "latency_ms": latency_ms, "tokens_in": None, "tokens_out": None, "cost_usd": 0.0,
            "version": version, "error_code": error, "ts": self._real_clock().isoformat(), "source": "demo",
        }
        try:
            self.stores.traces.emit(event)
        except Exception:
            pass  # tracing never breaks the lifecycle


def _aws_code(exc: BaseException) -> str:
    from cloud.aws import error_code

    return error_code(exc)


def _code(exc: BaseException) -> str:
    """#7 ``error_code`` of an AWS failure: the AWS code (or exception type), lower case."""
    return (_aws_code(exc) or type(exc).__name__).lower()[:40]


def _to_investigating(case: CaseRecord) -> bool:
    if case.status != CaseStatus.open:
        return False
    case.status = CaseStatus.investigating
    return True


def _link_report(case: CaseRecord, report_id: str) -> bool:
    if case.investigation_id == report_id:
        return False
    case.investigation_id = report_id
    return True


def _flag_incomplete(case: CaseRecord, error: str | None) -> bool:
    """The same note and flag LocalLifecycle writes when the investigator gives no report."""
    changed = False
    note = f"El investigador no produjo reporte ({error}): revisar la evidencia a mano"
    if note not in case.handoff.open_questions:
        case.handoff.open_questions.append(note)
        changed = True
    if "evidence_incomplete" not in case.urgency_flags:
        case.urgency_flags.append("evidence_incomplete")
        changed = True
    return changed


def make_stepfunctions_lifecycle(env: dict[str, str], stores: Stores, clock: Callable[[], datetime] | None = None,
                                 gateway: Any = default_gateway, sla_scale: float | None = None) -> StepFunctionsLifecycle:
    """For ``conversation.lifecycle.make_lifecycle`` (API): needs ``CASE_STATE_MACHINE_ARN``."""
    if not (env.get("CASE_STATE_MACHINE_ARN") or "").strip():
        raise StoreConfigError("LIFECYCLE_BACKEND=stepfunctions needs CASE_STATE_MACHINE_ARN (WorkflowStack, #9)")
    return StepFunctionsLifecycle.from_env(env, stores, clock=clock, gateway=gateway, sla_scale=sla_scale)


__all__ = ["StepFunctionsLifecycle", "make_stepfunctions_lifecycle", "error_code_of"]
