"""LIFECYCLE_BACKEND=stepfunctions (src/cloud/stepfunctions_lifecycle.py) against moto: Step Functions
(StartExecution, SendTaskSuccess), EventBridge Scheduler and the DynamoDB stores. moto does not run
the state machine, so the tests call the steps its Lambdas run (investigate, register_task_token,
notify_customer, close_case, sla_timer) in the order the machine would. Offline, SYNTHETIC data."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from cloud.dynamodb_store import DynamoCaseStore, DynamoReviewStore, DynamoSessionStore
from cloud.stepfunctions_lifecycle import StepFunctionsLifecycle, error_code_of
from conversation.case import AnalystDecision, CaseRecord, CaseStatus, Clock, Lane, Promise, Resolution
from conversation.contract import ApiFailure
from conversation.investigator_stub import investigate
from conversation.lifecycle import make_lifecycle
from conversation.store import MemoryTraceSink, StoreConfigError, Stores

from _support import Conv, FakeClock, reset_gateway
from test_cloud_support import (  # noqa: F401
    CASES, GROUP, ROLE, SESSIONS, TIMER_FN, Spy, aws, client, client_error, cloud_env, create_cases_table,
    create_sessions_table, create_workflow, make_cloud_app,
)

T0 = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
CLAIMS = {"sub": "analista-1"}


@pytest.fixture
def cloud(aws):  # noqa: F811
    ddb = client("dynamodb")
    create_sessions_table(ddb)
    create_cases_table(ddb)
    reset_gateway()
    yield {"arn": create_workflow(), "ddb": ddb}
    reset_gateway()


@pytest.fixture
def lc(cloud):
    stores = Stores(sessions=DynamoSessionStore(SESSIONS, client=cloud["ddb"]),
                    cases=DynamoCaseStore(CASES, client=cloud["ddb"]), traces=MemoryTraceSink(),
                    backend="dynamodb", reviews=DynamoReviewStore(CASES, client=cloud["ddb"]))
    clock = FakeClock(T0)
    return StepFunctionsLifecycle(stores, sfn=Spy(client("stepfunctions")), scheduler=Spy(client("scheduler")),
                                  state_machine_arn=cloud["arn"], schedule_group=GROUP, scheduler_role_arn=ROLE,
                                  timer_target_arn=TIMER_FN, clock=clock, sla_scale=1 / 1440)


def lane_b_case(case_id="EV-MONEY001", subcategory="unrecognized_charge") -> CaseRecord:
    return CaseRecord(case_id=case_id, created_at=T0, channel="web", language="es", country="CO",
                      customer_id="DEMO-LUCIA", lane=Lane.B, subcategory=subcategory,
                      promise=Promise(expected_date=date(2026, 10, 6), sla_days=5),
                      clock=Clock(assigned_by=T0 + timedelta(hours=24), first_response_by=T0 + timedelta(hours=24),
                                  sla_alert_at=T0 + timedelta(days=4), breach_at=T0 + timedelta(days=5)))


def save(lc, case: CaseRecord) -> CaseRecord:
    lc.stores.cases.put(case, "S-0001")
    return case


def schedule(name: str) -> dict:
    return client("scheduler").get_schedule(Name=name, GroupName=GROUP)


def no_schedule(name: str) -> bool:
    try:
        schedule(name)
    except Exception as e:
        return "ResourceNotFound" in type(e).__name__ or "ResourceNotFound" in str(e)
    return False


def decide(lc, case_id: str, action: str = "approve", nxt: str | None = None) -> CaseRecord:
    stored = lc.stores.cases.get(case_id)
    text = "Revisamos tu caso. Respuesta aprobada por una persona del equipo."
    d = AnalystDecision(action=action, edited=False, reason="r" if action == "reject" else None, decided_at=T0,
                        decided_by="analista-1", next=nxt, draft_text=text, sent_text=None if nxt == "escalate" else text)
    res = None if nxt == "escalate" else Resolution(language="es", text=text, sent_at=lc.clock())
    return lc.on_decision(case_id, stored.case.version, d, res, [f"decision:{action}"])


# ---------------------------------------------------------------- start

def test_start_runs_round_one_schedules_the_timers_and_is_idempotent(lc, cloud):
    save(lc, lane_b_case())
    lc.start("EV-MONEY001")
    calls = lc.sfn.named("start_execution")
    assert len(calls) == 1 and calls[0]["name"] == "EV-MONEY001-r1" and calls[0]["stateMachineArn"] == cloud["arn"]
    assert json.loads(calls[0]["input"]) == {"case_id": "EV-MONEY001", "money_dispute": True, "round": 1}
    case = lc.stores.cases.get("EV-MONEY001").case
    assert case.status == CaseStatus.investigating and case.sla_timers == ["unassigned", "sla_80", "breached"]
    internal = lc.stores.cases.get_internal("EV-MONEY001")
    assert internal["execution_round"] == 1 and internal["execution_arn"].endswith(":EV-MONEY001-r1")
    s = schedule("EV-MONEY001-sla_80")
    assert s["ScheduleExpression"] == "at(2026-10-03T15:00:00)" and s["ScheduleExpressionTimezone"] == "UTC"
    assert s["Target"]["Arn"] == TIMER_FN and s["Target"]["RoleArn"] == ROLE
    assert json.loads(s["Target"]["Input"]) == {"action": "sla_timer", "case_id": "EV-MONEY001", "kind": "sla_80"}
    assert s["ActionAfterCompletion"] == "DELETE"
    lc.start("EV-MONEY001")  # the next chat turn calls start again: nothing new
    assert len(lc.sfn.named("start_execution")) == 1
    events = [e for e in lc.stores.traces.events if e["trace_id"] == "lc-EV-MONEY001"]
    assert [e["name"] for e in events] == ["lifecycle.start", "sla_timer.schedule", "lifecycle.transition"]
    assert [e["step"] for e in events] == [1, 2, 3]  # one counter on the case item, shared by every Lambda
    assert lc.advance("EV-MONEY001").case.status == CaseStatus.investigating and lc.advance_all() is None


def test_lane_a_and_c_have_no_execution_and_a_failed_start_never_breaks_the_turn(lc):
    save(lc, lane_b_case("EV-LANEA001").model_copy(update={"lane": Lane.A}))
    lc.start("EV-LANEA001")
    assert lc.sfn.named("start_execution") == []
    save(lc, lane_b_case())
    lc.sfn.fail["start_execution"] = client_error("ThrottlingException", "StartExecution")
    lc.start("EV-MONEY001")  # traced, not raised
    assert lc.stores.cases.get("EV-MONEY001").case.status == CaseStatus.open
    assert lc.stores.traces.events[-1]["error_code"] == "throttlingexception"
    del lc.sfn.fail["start_execution"]
    lc.start("EV-MONEY001")  # the next turn retries
    assert lc.stores.cases.get("EV-MONEY001").case.status == CaseStatus.investigating


def test_make_lifecycle_needs_the_state_machine(cloud):
    stores = Stores(sessions=DynamoSessionStore(SESSIONS, client=cloud["ddb"]),
                    cases=DynamoCaseStore(CASES, client=cloud["ddb"]), traces=MemoryTraceSink(), backend="dynamodb",
                    reviews=DynamoReviewStore(CASES, client=cloud["ddb"]))
    with pytest.raises(StoreConfigError, match="CASE_STATE_MACHINE_ARN"):
        make_lifecycle({"LIFECYCLE_BACKEND": "stepfunctions"}, stores)
    lc = make_lifecycle(cloud_env(cloud["arn"], LOCAL_SLA_SCALE="0.001", CASE_CLOSE_AFTER_SECONDS="5"), stores)
    assert isinstance(lc, StepFunctionsLifecycle) and lc.sla_scale == 0.001
    assert lc.close_after == timedelta(seconds=5) and lc.schedule_group == GROUP


# ---------------------------------------------------------------- task token and decision

def test_await_analyst_needs_the_token_and_the_decision_answers_it(lc):
    save(lc, lane_b_case("EV-APPCASE1", subcategory="app"))
    lc.start("EV-APPCASE1")
    assert json.loads(lc.sfn.named("start_execution")[0]["input"])["money_dispute"] is False
    assert lc.stores.cases.get("EV-APPCASE1").case.status == CaseStatus.open  # no token yet: not decidable
    out = lc.register_task_token("EV-APPCASE1", "tok-1", "arn:exec:1", round_no=1)
    assert out["status"] == "awaiting_analyst"
    assert lc.register_task_token("EV-APPCASE1", "tok-1", "arn:exec:1", round_no=1)["status"] == "awaiting_analyst"
    assert lc.stores.cases.get_internal("EV-APPCASE1")["task_token"] == "tok-1"
    case = decide(lc, "EV-APPCASE1")
    assert case.status == CaseStatus.notified
    sent = lc.sfn.named("send_task_success")
    assert [s["taskToken"] for s in sent] == ["tok-1"]
    assert json.loads(sent[0]["output"]) == {"action": "approve", "next": None, "status": "notified"}
    internal = lc.stores.cases.get_internal("EV-APPCASE1")
    assert "task_token" not in internal and internal["decided_round"] == 1
    assert all(no_schedule(f"EV-APPCASE1-{k}") for k in ("unassigned", "sla_80", "breached"))
    assert lc.notify_customer("EV-APPCASE1", {"action": "approve"}) == {
        "case_id": "EV-APPCASE1", "status": "notified", "close": True, "close_after_seconds": 30}
    assert lc.close_case("EV-APPCASE1") == {"case_id": "EV-APPCASE1", "closed": True, "status": "closed"}
    assert lc.close_case("EV-APPCASE1")["closed"] is False
    with pytest.raises(ApiFailure) as e:
        decide(lc, "EV-APPCASE1")
    assert e.value.code == "conflict"  # already decided


def test_a_decision_stands_when_aws_rejects_the_token(lc):
    save(lc, lane_b_case("EV-APPCASE2", subcategory="app"))
    lc.start("EV-APPCASE2")
    lc.register_task_token("EV-APPCASE2", "tok-old", round_no=1)
    lc.sfn.fail["send_task_success"] = client_error("TaskTimedOut", "SendTaskSuccess")
    assert decide(lc, "EV-APPCASE2").status == CaseStatus.notified
    assert lc.stores.cases.get_internal("EV-APPCASE2")["task_error"] == "TaskTimedOut"
    answered = [e for e in lc.stores.traces.events if e["name"] == "lifecycle.task_success"]
    assert answered[-1]["error_code"] == "tasktimedout"


def test_a_retried_wait_on_a_decided_case_is_answered_at_once(lc):
    save(lc, lane_b_case("EV-APPCASE3", subcategory="app"))
    lc.start("EV-APPCASE3")
    lc.register_task_token("EV-APPCASE3", "tok-1", round_no=1)
    decide(lc, "EV-APPCASE3", "reject", "escalate")
    assert lc.stores.cases.get("EV-APPCASE3").case.status == CaseStatus.handed_off
    out = lc.register_task_token("EV-APPCASE3", "tok-retry", round_no=1)
    assert out["answered"] is True
    late = json.loads(lc.sfn.named("send_task_success")[-1]["output"])
    assert late == {"action": "reject", "next": "escalate", "status": "handed_off", "late": True}
    assert lc.notify_customer("EV-APPCASE3")["close"] is False  # escalated: nothing to close


def test_mark_incomplete_flags_the_case_for_the_analyst(lc):
    save(lc, lane_b_case())
    out = lc.mark_incomplete("EV-MONEY001", "States.Timeout")
    assert out == {"case_id": "EV-MONEY001", "status": "no_report", "error_code": "states.timeout"}
    case = lc.stores.cases.get("EV-MONEY001").case
    assert "evidence_incomplete" in case.urgency_flags
    assert any("states.timeout" in q for q in case.handoff.open_questions)
    lc.mark_incomplete("EV-MONEY001", "States.Timeout")  # a retry adds nothing
    assert lc.stores.cases.get("EV-MONEY001").case.urgency_flags.count("evidence_incomplete") == 1
    assert error_code_of(None) == "investigator_failed" and error_code_of("Lambda.Unknown") == "lambda.unknown"


# ---------------------------------------------------------------- SLA timers

def test_sla_timers_fire_like_the_local_lifecycle(lc):
    save(lc, lane_b_case())
    lc.start("EV-MONEY001")
    lc.register_task_token("EV-MONEY001", "tok-1", round_no=1)
    lc._real_clock.now = T0 + timedelta(hours=2)
    lc.on_opened("EV-MONEY001", "analista-1")  # taken: the unassigned timer and its schedule go
    assert no_schedule("EV-MONEY001-unassigned") and not no_schedule("EV-MONEY001-sla_80")
    assert lc.fire_sla_timer("EV-MONEY001", "unassigned")["fired"] is False  # a leftover delivery: no-op
    lc._real_clock.now = T0 + timedelta(days=4)  # the scheduler fires sla_80
    assert lc.fire_sla_timer("EV-MONEY001", "sla_80") == {"case_id": "EV-MONEY001", "kind": "sla_80", "fired": True}
    case = lc.stores.cases.get("EV-MONEY001").case
    assert [n.kind for n in case.notices] == ["sla_80"] and case.sla_timers == ["breached"]
    assert [e.kind for e in case.clock_events] == ["sla_80"]
    assert lc.stores.reviews.get_conversation("EV-MONEY001")[-1]["text"] == case.notices[0].text
    assert lc.fire_sla_timer("EV-MONEY001", "sla_80")["fired"] is False  # a duplicate delivery
    lc._real_clock.now = T0 + timedelta(days=5) - timedelta(seconds=20)  # 20 s early still fires its timer
    assert lc.fire_sla_timer("EV-MONEY001", "breached")["fired"] is True
    case = lc.stores.cases.get("EV-MONEY001").case
    assert case.handoff.queue == "senior" and case.handoff.priority == "high" and "sla_breached" in case.urgency_flags
    assert lc.fire_sla_timer("EV-NOPE0001", "breached")["fired"] is False


def test_fast_clock_schedules_at_the_demo_times_and_rescales(lc):
    lc.stores.sessions.put({"session_id": "S-0001", "token_hash": "h", "fast_clock": True,
                            "expires_at": "2026-09-29T15:15:00Z"})
    save(lc, lane_b_case())
    lc.start("EV-MONEY001")  # fast_clock read from the session
    case = lc.stores.cases.get("EV-MONEY001").case
    assert case.timer_scale == pytest.approx(1 / 1440)
    assert schedule("EV-MONEY001-unassigned")["ScheduleExpression"] == "at(2026-09-29T15:01:00)"  # >= 60 s ahead
    assert schedule("EV-MONEY001-breached")["ScheduleExpression"] == "at(2026-09-29T15:05:00)"  # 5 days -> 5 min
    lc.set_fast_clock("EV-MONEY001", False)
    assert schedule("EV-MONEY001-breached")["ScheduleExpression"] == "at(2026-10-04T15:00:00)"
    assert lc.has_pending_timers("EV-MONEY001")


# ---------------------------------------------------------------- the real app on the cloud lifecycle

def test_the_app_runs_a_case_through_the_state_machine_steps_and_a_reopen(cloud):
    clock = FakeClock(T0)
    app = make_cloud_app(cloud_env(cloud["arn"]), clock)
    lc = app.lifecycle
    assert isinstance(lc, StepFunctionsLifecycle) and app.analyst.lifecycle is lc
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    r = conv.press("deny")
    case_id = r["case_card"]["case_id"]
    assert r["case_card"]["status"] == "investigating"  # start ran inside the turn
    assert [c["name"] for c in lc.sfn.named("start_execution")] == [f"{case_id}-r1"]
    # Investigate (investigator Lambda) -> AwaitAnalyst (case-steps register_task_token)
    out = lc.run_investigation(case_id, investigator=investigate)
    assert out["status"] == "report_ready" and app.stores.reviews.get_report(case_id)["report_id"] == out["report_id"]
    assert lc.run_investigation(case_id, investigator=investigate)["status"] in ("report_ready", "skipped")
    lc.register_task_token(case_id, "tok-1", round_no=1)
    assert app.get_case(conv.token, case_id)["case"]["status"] == "awaiting_analyst"
    detail = app.analyst.get_detail(CLAIMS, case_id)  # on_opened deletes the unassigned schedule
    assert no_schedule(f"{case_id}-unassigned")
    body = {"client_decision_id": uuid.uuid4().hex, "version": detail["version"], "action": "approve"}
    assert app.analyst.decide(CLAIMS, case_id, body)["status"] == "notified"
    assert [s["taskToken"] for s in lc.sfn.named("send_task_success")] == ["tok-1"]
    # the stub report asked the customer for information: the case waits for the answer, no close
    assert lc.notify_customer(case_id)["close"] is False
    # the customer answers in the chat: reopened, and that turn starts round 2 straight to the analyst
    conv.say("gracias, aquí va la información que pidieron")
    names = [c["name"] for c in lc.sfn.named("start_execution")]
    assert names == [f"{case_id}-r1", f"{case_id}-r2"]
    assert json.loads(lc.sfn.named("start_execution")[-1]["input"])["money_dispute"] is False
    conv.say("¿hay novedades?")
    assert len(lc.sfn.named("start_execution")) == 2  # same round: no duplicate execution
    # round 2 takes the case; an older round's late wait is ended without touching it or its token
    assert lc.stores.cases.get(case_id).case.status == CaseStatus.reopened
    assert lc.register_task_token(case_id, "tok-2", round_no=2)["status"] == "awaiting_analyst"
    assert lc.register_task_token(case_id, "tok-old", round_no=1)["superseded"] is True
    assert lc.sfn.named("send_task_success")[-1]["taskToken"] == "tok-old"
    assert lc.stores.cases.get_internal(case_id)["task_token"] == "tok-2"
    assert lc.stores.cases.get(case_id).case.status == CaseStatus.awaiting_analyst
    detail = app.analyst.get_detail(CLAIMS, case_id)
    body = {"client_decision_id": uuid.uuid4().hex, "version": detail["version"], "action": "reject",
            "reason": "revisión senior", "next": "escalate"}
    assert app.analyst.decide(CLAIMS, case_id, body)["status"] == "handed_off"
    assert lc.sfn.named("send_task_success")[-1]["taskToken"] == "tok-2"
    names = [e["name"] for e in app.stores.traces.events if e["trace_id"] == f"lc-{case_id}"]
    assert names.count("lifecycle.start") == 2 and "analyst_decision" in names
    steps = [e["step"] for e in app.stores.traces.events if e["trace_id"] == f"lc-{case_id}"]
    assert steps == sorted(steps) and len(set(steps)) == len(steps)
