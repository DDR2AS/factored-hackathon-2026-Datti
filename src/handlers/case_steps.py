"""Case lifecycle steps, invoked by the case state machine and by the SLA timers (WorkflowStack).

Actions (``event["action"]``), all idempotent (Step Functions and EventBridge may retry them):
  register_task_token  AwaitAnalyst (.waitForTaskToken): store the token on the case, then the case
                       is ``awaiting_analyst``. Event ``{case_id, task_token, execution_arn, round}``.
                       Returns nothing useful: the state waits for SendTaskSuccess (on_decision).
  mark_incomplete      the investigator failed or timed out; the case is flagged for the analyst
                       (``evidence_incomplete``). Event ``{case_id, error}`` (``error`` = States error name).
  notify_customer      after the decision: the resolution is already in the case and the chat shows it;
                       returns ``{close, close_after_seconds}`` for the Wait before ``close_case``.
  close_case           ``notified -> closed`` unless the customer was asked for information or reopened it.
  sla_timer            an EventBridge Scheduler timer fired: ``{case_id, kind}`` (unassigned, sla_80, breached).

The logic is ``cloud.stepfunctions_lifecycle.StepFunctionsLifecycle`` (the same record rules as the
local lifecycle). Never logged: the task token and any text; case ID and action are enough.
"""

from __future__ import annotations

import json
import os

ACTIONS = {"register_task_token", "mark_incomplete", "notify_customer", "close_case", "sla_timer"}
SLA_KINDS = {"unassigned", "sla_80", "breached"}

_LIFECYCLE = None


def lifecycle():
    """The lifecycle for this container, built from the environment once (DynamoDB stores)."""
    global _LIFECYCLE
    if _LIFECYCLE is None:
        from cloud.stepfunctions_lifecycle import StepFunctionsLifecycle
        from conversation.store import make_stores

        _LIFECYCLE = StepFunctionsLifecycle.from_env(os.environ, make_stores(os.environ))
    return _LIFECYCLE


def set_lifecycle(value) -> None:
    """Dependency injection for tests."""
    global _LIFECYCLE
    _LIFECYCLE = value


def _investigator_step() -> tuple[str, str]:
    backend = (os.environ.get("INVESTIGATOR_BACKEND") or "stub").strip().lower()
    return ("model", "investigator") if backend == "g2" else ("rule", "investigator_stub")


def handler(event, context):
    event = event or {}
    action = event.get("action")
    if action not in ACTIONS:
        raise ValueError(f"Unknown case step action: {action!r}")
    case_id = event.get("case_id")
    if not case_id or not isinstance(case_id, str):
        raise ValueError("case step without case_id")
    print(json.dumps({"action": action, "case_id": case_id, "kind": event.get("kind")}))
    lc = lifecycle()
    try:
        if action == "register_task_token":
            round_no = event.get("round")
            return lc.register_task_token(case_id, event.get("task_token") or "", event.get("execution_arn"),
                                          round_no=int(round_no) if round_no is not None else None)
        if action == "mark_incomplete":
            return lc.mark_incomplete(case_id, event.get("error"), trace_step=_investigator_step())
        if action == "notify_customer":
            return lc.notify_customer(case_id, event.get("decision"))
        if action == "close_case":
            return lc.close_case(case_id)
        kind = event.get("kind")
        if kind not in SLA_KINDS:
            raise ValueError(f"sla_timer with unknown kind {kind!r}")
        return lc.fire_sla_timer(case_id, kind)
    finally:
        from cloud.traces import flush

        flush(lc.stores.traces)
