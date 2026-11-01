"""DynamoDB stores (STORE_BACKEND=dynamodb, src/cloud/dynamodb_store.py) against moto. Offline,
SYNTHETIC data. Same behaviour as the memory stores of conversation/store.py, plus the DynamoDB
specifics: numeric TTL attribute, token pointer, conditional write on version, review items."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from cloud import ddb
from cloud.dynamodb_store import (
    DynamoCaseStore, DynamoReviewStore, DynamoSessionStore, make_dynamodb_stores,
)
from conversation.case import CaseRecord, CaseStatus, Lane
from conversation.store import CaseVersionConflict, MemoryCaseStore, StoreConfigError, make_stores

from test_cloud_support import CASES, SESSIONS, aws, client, create_cases_table, create_sessions_table  # noqa: F401

T0 = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def tables(aws):  # noqa: F811
    ddb_client = client("dynamodb")
    create_sessions_table(ddb_client)
    create_cases_table(ddb_client)
    return ddb_client


def session(sid="S-0001", token_hash="h1", expires="2026-09-29T15:15:00Z", **extra):
    return {"session_id": sid, "token_hash": token_hash, "demo_key": "lucia", "customer_id": "DEMO-LUCIA",
            "channel": "web", "language": "es", "created_at": "2026-09-29T15:00:00Z", "expires_at": expires,
            "turns": 0, "conversation": {"state": [1, 2.5, "ñ"]}, "replies": {}, **extra}


def case(case_id="EV-TESTCASE", lane=Lane.B, **extra) -> CaseRecord:
    return CaseRecord(case_id=case_id, created_at=T0, channel="web", language="es", customer_id="DEMO-LUCIA",
                      lane=lane, subcategory="unrecognized_charge", intent_confidence=0.8731,
                      evidence={"transaction": {"txn_id": "TXN-1", "amount": "450000.00", "fraud_score": 12.5},
                                "product": None, "app_error": None},
                      risk_evidence={"features": {"amount_vs_median": 5.62, "first_time_merchant": False, "n": 3},
                                     "fraud_score": 12.5, "rule_fired": None}, **extra)


# ---------------------------------------------------------------- sessions

def test_session_round_trip_and_token_lookup(tables):
    store = DynamoSessionStore(SESSIONS, client=tables)
    store.put(session())
    got = store.get("S-0001")
    assert got == session()
    got["conversation"]["state"].append(9)  # copies, like the memory store
    assert store.get("S-0001")["conversation"]["state"] == [1, 2.5, "ñ"]
    assert store.get_by_token_hash("h1")["session_id"] == "S-0001"
    assert store.get_by_token_hash("nope") is None
    assert store.get("TOKEN#h1") is None  # the pointer is not a session
    with pytest.raises(TypeError):
        store.put(session(bad=object()))  # JSON-ready only


def test_ttl_attribute_is_a_number_in_epoch_seconds(tables):
    DynamoSessionStore(SESSIONS, client=tables).put(session(expires="2026-09-29T15:15:00Z"))
    item = tables.get_item(TableName=SESSIONS, Key={"session_id": {"S": "S-0001"}})["Item"]
    expected = int(datetime(2026, 9, 29, 15, 15, tzinfo=timezone.utc).timestamp())
    assert item["expires_at_epoch"] == {"N": str(expected)}
    assert item["expires_at"] == {"S": "2026-09-29T15:15:00Z"}
    pointer = tables.get_item(TableName=SESSIONS, Key={"session_id": {"S": "TOKEN#h1"}})["Item"]
    assert pointer["expires_at_epoch"] == {"N": str(expected)} and pointer["target"] == {"S": "S-0001"}
    ttl = tables.describe_time_to_live(TableName=SESSIONS)["TimeToLiveDescription"]
    assert ttl["AttributeName"] == "expires_at_epoch"


def test_token_rotation_and_delete_drop_the_pointer(tables):
    store = DynamoSessionStore(SESSIONS, client=tables)
    store.put(session(token_hash="h1"))
    store.put(session(token_hash="h2"))
    assert store.get_by_token_hash("h1") is None
    assert store.get_by_token_hash("h2")["token_hash"] == "h2"
    assert "Item" not in tables.get_item(TableName=SESSIONS, Key={"session_id": {"S": "TOKEN#h1"}})
    store.delete("S-0001")
    assert store.get("S-0001") is None and store.get_by_token_hash("h2") is None
    assert "Item" not in tables.get_item(TableName=SESSIONS, Key={"session_id": {"S": "TOKEN#h2"}})
    store.delete("S-0001")  # idempotent


def test_a_long_session_is_gzipped_and_round_trips(tables):
    replies = {f"m{i}": {"reply_text": "texto sintético " * 120, "turn": i} for i in range(30)}
    data = session(replies=replies)
    store = DynamoSessionStore(SESSIONS, client=tables)
    store.put(data)
    item = tables.get_item(TableName=SESSIONS, Key={"session_id": {"S": "S-0001"}})["Item"]
    assert "doc_gz" in item and "doc" not in item
    assert store.get_by_token_hash("h1") == data
    store.put(session())  # back to a small one: the gzip copy is replaced, not kept
    item = tables.get_item(TableName=SESSIONS, Key={"session_id": {"S": "S-0001"}})["Item"]
    assert "doc" in item and "doc_gz" not in item


# ---------------------------------------------------------------- cases

def test_case_versions_follow_the_memory_store(tables):
    clock = lambda: T0 + timedelta(minutes=5)  # noqa: E731
    dyn, mem = DynamoCaseStore(CASES, client=tables, clock=clock), MemoryCaseStore(clock=clock)
    for store in (dyn, mem):
        c = case()
        store.put(c, "S-0001")
        assert c.version == 1 and c.updated_at == clock()
        c.status = CaseStatus.investigating
        store.put(c, "S-0001")
        assert c.version == 2
    stored = dyn.get("EV-TESTCASE")
    assert stored.session_id == "S-0001"
    assert stored.case == mem.get("EV-TESTCASE").case  # floats, dicts and datetimes round trip exactly
    assert stored.case.risk_evidence.features == {"amount_vs_median": 5.62, "first_time_merchant": False, "n": 3}
    assert isinstance(stored.case.risk_evidence.features["n"], int)


def test_stale_version_is_a_conflict_and_leaves_the_object_alone(tables):
    store = DynamoCaseStore(CASES, client=tables)
    first = case()
    store.put(first, "S-0001")
    a, b = store.get("EV-TESTCASE").case, store.get("EV-TESTCASE").case
    a.status = CaseStatus.investigating
    store.put(a, "S-0001")
    b.status = CaseStatus.awaiting_analyst
    with pytest.raises(CaseVersionConflict):
        store.put(b, "S-0001")
    assert b.version == 1  # unchanged on conflict
    assert store.get("EV-TESTCASE").case.status == CaseStatus.investigating
    # a brand-new record with the id of an existing case and version 1 is stale too
    with pytest.raises(CaseVersionConflict):
        store.put(case(), "S-0002")


def test_index_attributes_and_the_queue_only_lists_lanes_b_and_c(tables):
    store = DynamoCaseStore(CASES, client=tables)
    for cid, lane in (("EV-AAAAAAAA", Lane.A), ("EV-BBBBBBBB", Lane.B), ("EV-CCCCCCCC", Lane.C)):
        store.put(case(cid, lane=lane), "S-0001")
    item = tables.get_item(TableName=CASES, Key={"case_id": {"S": "EV-BBBBBBBB"}})["Item"]
    assert item["status"] == {"S": "open"} and item["customer_id"] == {"S": "DEMO-LUCIA"}
    assert item["queue_lane"] == {"S": "B"} and item["version"] == {"N": "1"} and item["kind"] == {"S": "case"}
    a_item = tables.get_item(TableName=CASES, Key={"case_id": {"S": "EV-AAAAAAAA"}})["Item"]
    assert "queue_lane" not in a_item  # sparse index: lane A never reaches the analyst queue
    assert [s.case.case_id for s in store.all()] == ["EV-BBBBBBBB", "EV-CCCCCCCC"]
    moved = store.get("EV-BBBBBBBB").case
    moved.lane = Lane.A
    store.put(moved, "S-0001")
    assert [s.case.case_id for s in store.all()] == ["EV-CCCCCCCC"]


def test_internal_lifecycle_attributes_survive_puts_and_never_reach_the_record(tables):
    store = DynamoCaseStore(CASES, client=tables)
    assert store.set_internal("EV-MISSING1", task_token="t") is False  # no stray items
    c = case()
    store.put(c, "S-0001")
    assert store.set_internal("EV-TESTCASE", task_token="tok-1", execution_round=1, execution_arn="arn:x")
    c.status = CaseStatus.awaiting_analyst
    store.put(c, "S-0001")
    assert store.get_internal("EV-TESTCASE") == {"task_token": "tok-1", "execution_round": 1, "execution_arn": "arn:x"}
    assert "task_token" not in store.get("EV-TESTCASE").case.model_dump()
    assert [store.next_trace_step("EV-TESTCASE") for _ in range(3)] == [1, 2, 3]
    assert store.get("EV-TESTCASE").case.version == 2  # the counter does not bump the version
    store.remove_internal("EV-TESTCASE", "task_token")
    assert "task_token" not in store.get_internal("EV-TESTCASE")
    assert not store.set_internal("EV-TESTCASE", condition="#execution_round > :n",
                                  condition_values={":n": ddb.n(5)}, execution_round=6)
    with pytest.raises(ValueError):
        store.set_internal("EV-TESTCASE", status="closed")


def test_review_items_are_not_cases(tables):
    reviews, cases = DynamoReviewStore(CASES, client=tables), DynamoCaseStore(CASES, client=tables)
    report = {"report_id": "IR-TESTCASE", "hypotheses": [{"name": "fraud", "p": 0.62}], "confidence": 0.7,
              "evidence_records": {"TXN-1": {"kind": "transaction", "fields": {"amount": "450000.00"}}}}
    reviews.put_report("EV-TESTCASE", report)
    assert reviews.get_report("EV-TESTCASE") == report
    assert reviews.get_report("EV-OTHER123") is None
    assert cases.get("REPORT#EV-TESTCASE") is None  # a review item is never read as a case
    reviews.put_decision_reply("EV-TESTCASE:d1", "hash-a", {"status": "notified"})
    reviews.put_decision_reply("EV-TESTCASE:d1", "hash-b", {"status": "other"})  # first one wins
    assert reviews.get_decision_reply("EV-TESTCASE:d1") == ("hash-a", {"status": "notified"})
    assert reviews.get_decision_reply("EV-TESTCASE:d2") is None
    assert cases.all() == []


def test_conversation_keeps_the_last_200_turns_in_order(tables):
    reviews = DynamoReviewStore(CASES, client=tables)
    turn = lambda i: {"role": "customer", "text": f"mensaje {i}", "language": "es", "source": "human",  # noqa: E731
                      "at": "2026-09-29T15:00:00+00:00"}
    reviews.append_conversation("EV-TESTCASE", [turn(i) for i in range(150)])
    reviews.append_conversation("EV-TESTCASE", [turn(i) for i in range(150, 260)])
    reviews.append_conversation("EV-TESTCASE", [])
    conv = reviews.get_conversation("EV-TESTCASE")
    assert len(conv) == 200 and conv[0]["text"] == "mensaje 60" and conv[-1]["text"] == "mensaje 259"
    assert reviews.get_conversation("EV-NOTHING1") == []


def test_conversation_append_retries_on_a_concurrent_writer(tables):
    reviews = DynamoReviewStore(CASES, client=tables)
    reviews.append_conversation("EV-TESTCASE", [{"role": "system", "text": "hola"}])
    real_get = reviews._get
    raced = []

    def racing_get(key):
        item = real_get(key)
        if not raced:  # another Lambda appends between our read and our write
            raced.append(1)
            DynamoReviewStore(CASES, client=tables).append_conversation("EV-TESTCASE", [{"role": "x", "text": "otro"}])
        return item

    reviews._get = racing_get
    reviews.append_conversation("EV-TESTCASE", [{"role": "customer", "text": "mío"}])
    assert [t["text"] for t in reviews.get_conversation("EV-TESTCASE")] == ["hola", "otro", "mío"]


# ---------------------------------------------------------------- selection

def test_make_stores_builds_the_dynamodb_backend(tables, monkeypatch):
    env = {"STAGE": "dev", "STORE_BACKEND": "dynamodb", "TABLE_SESSIONS": SESSIONS, "TABLE_CASES": CASES}
    stores = make_stores(env)
    assert stores.backend == "dynamodb"
    assert isinstance(stores.sessions, DynamoSessionStore) and isinstance(stores.cases, DynamoCaseStore)
    assert isinstance(stores.reviews, DynamoReviewStore) and stores.reviews.table == CASES
    assert type(stores.traces).__name__ == "StdoutTraceSink"
    with pytest.raises(StoreConfigError, match="TABLE_CASES"):
        make_dynamodb_stores({"TABLE_SESSIONS": SESSIONS})
    with pytest.raises(StoreConfigError, match="TRACE_SINK"):
        make_stores({**env, "TRACE_SINK": "kafka"})
    assert make_stores({"STAGE": "local"}).backend == "memory"  # the memory branch is unchanged


def test_the_chat_api_runs_on_dynamodb(tables):
    """The real app (sessions, a lane B case, the analyst decision) with the DynamoDB stores."""
    from conversation.app import build_app
    from conversation.sessions import token_hash

    from _support import Conv, FakeClock, reset_gateway

    reset_gateway()
    clock = FakeClock()
    env = {"STAGE": "dev", "STORE_BACKEND": "dynamodb", "TABLE_SESSIONS": SESSIONS, "TABLE_CASES": CASES,
           "LIFECYCLE_BACKEND": "local", "TRACE_SINK": "stdout"}
    app = build_app(env=env, clock=clock, use_env_llm=False, investigation_delay=0, sleep=lambda s: None)
    assert app.stores.cases.clock is clock  # updated_at follows the app clock
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    r = conv.press("deny")
    assert r["lane"] == "B"
    case_id = r["case_card"]["case_id"]
    record = app.stores.sessions.get_by_token_hash(token_hash(conv.token))
    assert record["turns"] == 3 and record["conversation"]["case_id"] == case_id
    view = app.get_case(conv.token, case_id)["case"]
    assert view["status"] == "awaiting_analyst"  # the local stub investigated on read
    claims = {"sub": "analista-1"}
    listed = app.analyst.list_cases(claims, {})["items"]
    assert [i["case_id"] for i in listed] == [case_id]
    detail = app.analyst.get_detail(claims, case_id)
    assert detail["report"]["report_id"] == "IR-" + case_id.removeprefix("EV-")
    assert len(detail["case"]["conversation"]) >= 6
    body = {"client_decision_id": uuid.uuid4().hex, "version": detail["version"], "action": "approve"}
    out = app.analyst.decide(claims, case_id, body)
    assert out["status"] == "notified"
    assert app.analyst.decide(claims, case_id, dict(body)) == out  # same id and body: the stored reply
    stale = {**body, "client_decision_id": uuid.uuid4().hex}
    from conversation.contract import ApiFailure

    with pytest.raises(ApiFailure) as e:
        app.analyst.decide(claims, case_id, stale)
    assert e.value.code == "conflict"
    seen = app.get_case(conv.token, case_id)["case"]
    assert seen["status"] == "notified" and seen["resolution"]["approved_by_human"] is True
    assert "aprobada por una persona" in seen["resolution"]["text"]
    reset_gateway()
