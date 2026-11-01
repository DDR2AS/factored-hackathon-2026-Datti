"""Workflow Lambda handlers on moto: case_steps (every action), investigator (a stored case), pipeline
(one tiny daily partition through the real run_partition, S3 in and out). Offline, SYNTHETIC data."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from cloud.stepfunctions_lifecycle import StepFunctionsLifecycle
from conversation.investigator_stub import investigate
from conversation import demo_gateway
from handlers import case_steps, investigator, pipeline as pipeline_handler

from _support import Conv, FakeClock, reset_gateway
from test_cloud_support import (  # noqa: F401
    Spy, aws, client, cloud_env, create_cases_table, create_sessions_table, create_workflow, make_cloud_app,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def cloud(aws, monkeypatch):  # noqa: F811
    ddb = client("dynamodb")
    create_sessions_table(ddb)
    create_cases_table(ddb)
    reset_gateway()
    arn = create_workflow()
    for key, value in cloud_env(arn, INVESTIGATOR_BACKEND="stub").items():
        monkeypatch.setenv(key, value)
    case_steps.set_lifecycle(None)
    investigator.set_parts(None)
    yield arn
    case_steps.set_lifecycle(None)
    investigator.set_parts(None)
    reset_gateway()


def open_case(arn: str, app=None):
    """A lane B money case opened through the real chat (lifecycle start included)."""
    app = app or make_cloud_app(cloud_env(arn), FakeClock())
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    case_id = conv.press("deny")["case_card"]["case_id"]
    return app, case_id


def test_case_steps_actions_from_the_environment(cloud, capsys):
    app, case_id = open_case(cloud)
    with pytest.raises(ValueError):
        case_steps.handler({"action": "explode", "case_id": case_id}, None)
    with pytest.raises(ValueError):
        case_steps.handler({"action": "notify_customer"}, None)
    with pytest.raises(ValueError):
        case_steps.handler({"action": "sla_timer", "case_id": case_id, "kind": "weekly"}, None)
    out = case_steps.handler({"action": "mark_incomplete", "case_id": case_id, "error": "States.Timeout"}, None)
    assert out["error_code"] == "states.timeout"
    lc = case_steps.lifecycle()  # built from the environment (DynamoDB stores, no state machine ARN needed)
    assert isinstance(lc, StepFunctionsLifecycle) and lc.stores.backend == "dynamodb"
    lc._sfn = Spy(client("stepfunctions"))
    out = case_steps.handler({"action": "register_task_token", "case_id": case_id, "task_token": "tok-secret-1",
                              "execution_arn": "arn:exec", "round": 1}, None)
    assert out == {"case_id": case_id, "status": "awaiting_analyst"}
    assert "tok-secret-1" not in capsys.readouterr().out  # never logged
    out = case_steps.handler({"action": "sla_timer", "case_id": case_id, "kind": "unassigned"}, None)
    assert out["fired"] is True  # the handler runs on the real clock, a month after the FakeClock opened it
    assert case_steps.handler({"action": "sla_timer", "case_id": case_id, "kind": "unassigned"}, None)["fired"] is False
    detail = app.analyst.get_detail({"sub": "a-1"}, case_id)
    app.analyst.decide({"sub": "a-1"}, case_id, {"client_decision_id": "d-1", "version": detail["version"],
                                                  "action": "reject", "reason": "x", "next": "escalate"})
    out = case_steps.handler({"action": "notify_customer", "case_id": case_id, "decision": {"action": "reject"}}, None)
    assert out["close"] is False and out["status"] == "handed_off"
    assert case_steps.handler({"action": "close_case", "case_id": case_id}, None)["closed"] is False


def test_investigator_handler_stores_the_report_of_a_stored_case(cloud):
    app, case_id = open_case(cloud)
    assert app.stores.cases.get(case_id).case.status.value == "investigating"
    with pytest.raises(ValueError):
        investigator.handler({}, None)
    out = investigator.handler({"case_id": case_id}, None)  # parts built from the environment (stub, demo)
    assert out["status"] == "report_ready"
    report = app.stores.reviews.get_report(case_id)
    assert report["report_id"] == out["report_id"] and report["case_id"] == case_id
    assert app.stores.cases.get(case_id).case.investigation_id == out["report_id"]
    assert investigator.handler({"case_id": case_id}, None)["status"] == "report_ready"  # a retry reuses it
    assert investigator.handler({"case_id": "EV-NOPE0000"}, None)["status"] == "not_found"


def test_investigator_handler_flags_expected_failures_and_raises_the_rest(cloud):
    app, case_id = open_case(cloud)
    demo_gateway.set_tool_failure(None, True)  # the data source is down for everyone
    try:
        investigator.set_parts((app.lifecycle, demo_gateway, investigate))
        out = investigator.handler({"case_id": case_id}, None)
    finally:
        demo_gateway.set_tool_failure(None, False)
    assert out == {"case_id": case_id, "status": "no_report", "error_code": "tool_unavailable"}
    assert "evidence_incomplete" in app.stores.cases.get(case_id).case.urgency_flags

    def broken(*args, **kwargs):
        raise KeyError("bug")

    _, case2 = open_case(cloud, app)  # another session, another case
    investigator.set_parts((app.lifecycle, demo_gateway, broken))
    with pytest.raises(KeyError):  # the state machine's catch runs mark_incomplete
        investigator.handler({"case_id": case2}, None)


# ---------------------------------------------------------------- pipeline

@pytest.fixture
def tiny_raw(tmp_path_factory):
    """A 20-customer synthetic replica of the raw dataset (one week, the organizers' layout)."""
    sys.path.insert(0, str(REPO / "src" / "etl"))
    import synth_dataset

    base = tmp_path_factory.mktemp("tiny_raw")
    synth_dataset.generate(customers=20, seed=3, raw_dir=base / "raw", verbose=False, start=date(2026, 6, 10))
    return base / "raw"


def test_pipeline_handler_loads_dimensions_then_one_partition(aws, tiny_raw, tmp_path, monkeypatch):  # noqa: F811
    from pipeline import config

    s3 = client("s3")
    for bucket in ("expvivo-test-raw", "expvivo-test-lake"):
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
    for path in tiny_raw.rglob("*"):  # the dimensions, the transactions and the replica marker
        if path.is_file() and ("transactions" in path.parts or path.parent == tiny_raw):
            s3.upload_file(str(path), "expvivo-test-raw", "data/" + path.relative_to(tiny_raw).as_posix())
    s3.put_object(Bucket="expvivo-test-lake", Key="bronze/transactions/_process_date=2026-06-12/stale.parquet", Body=b"x")
    original = config.DATA_DIR
    for key, value in {"DATA_DIR": str(tmp_path / "lambda" / "data"), "BUCKET_RAW": "expvivo-test-raw",
                       "BUCKET_LAKE": "expvivo-test-lake", "RAW_PREFIX": "data/"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)  # the handler chdirs next to DATA_DIR; restored after the test
    assert pipeline_handler.silver_inputs("transactions") == ["customers", "products"]
    for bad in ({"table": "transactions"}, {"step": "everything"}, {"table": "customers", "date": "2026-06-12"}):
        with pytest.raises(ValueError):
            pipeline_handler.handler(bad, None)
    try:
        dims = pipeline_handler.handler({"step": "dimensions"}, None)
        out = pipeline_handler.handler({"table": "transactions", "date": "2026-06-12", "refresh_gold": False}, None)
    finally:
        config.set_data_dir(original)
    json.dumps(dims), json.dumps(out)  # Step Functions needs JSON results
    assert dims["status"] == "ok" and dims["silver_rows"]["customers"] == 20 and dims["snapshot_date"] == "2026-06-17"
    assert out["status"] == "ok" and out["raw_files"] == 1 and out["bronze_rows"] > 0 and out["silver_rows"] > 0
    assert out["gold_refreshed"] is False and out["lake"]["bronze"]["deleted"] == 1
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket="expvivo-test-lake")["Contents"]}
    assert {"bronze/customers/data.parquet", "silver/customers/data.parquet", "silver/products/data.parquet",
            "silver/exchange_rates/data.parquet"} <= keys
    assert "bronze/transactions/_process_date=2026-06-12/data_0.parquet" in keys
    assert "bronze/transactions/_process_date=2026-06-12/stale.parquet" not in keys  # the partition is replaced
    assert "silver/transactions/process_date=2026-06-12/data_0.parquet" in keys
    assert not any(k.startswith("gold/") for k in keys)


def test_pipeline_gold_step_publishes_gold_and_the_serving_import_file(aws, tmp_path, monkeypatch):  # noqa: F811
    from pipeline import config, run

    s3 = client("s3")
    s3.create_bucket(Bucket="expvivo-test-lake", CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
    s3.put_object(Bucket="expvivo-test-lake", Key="silver/transactions/process_date=2026-06-12/data_0.parquet",
                  Body=b"PAR1")
    s3.put_object(Bucket="expvivo-test-lake", Key="gold/old_table/data.parquet", Body=b"old")
    seen = {}

    def fake_build_all(steps, serving, verbose):  # the real one is covered by tests/test_pipeline_gateway.py
        seen.update(steps=steps, silver=sorted(p.name for p in config.SILVER_DIR.rglob("*.parquet")))
        (config.GOLD_DIR / "customer_profile").mkdir(parents=True)
        (config.GOLD_DIR / "customer_profile" / "data.parquet").write_bytes(b"PAR1")
        config.SERVING_DIR.mkdir(parents=True)
        path = config.SERVING_DIR / "serving-20261030120000.jsonl.gz"
        path.write_bytes(b"gz")
        return {"snapshot_date": "2026-06-17", "seconds": 0.1, "serving": {"path": str(path), "items": 7}}

    monkeypatch.setattr(run, "build_all", fake_build_all)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BUCKET_LAKE", "expvivo-test-lake")
    monkeypatch.delenv("BUCKET_RAW", raising=False)
    monkeypatch.chdir(tmp_path)
    original = config.DATA_DIR
    try:
        out = pipeline_handler.handler({"step": "gold"}, None)
    finally:
        config.set_data_dir(original)
    assert seen == {"steps": ("gold",), "silver": ["data_0.parquet"]}
    assert out["serving"] == {"import_prefix": "serving/20261030120000/",
                              "key": "serving/20261030120000/serving-20261030120000.jsonl.gz", "items": 7}
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket="expvivo-test-lake")["Contents"]}
    assert "gold/customer_profile/data.parquet" in keys and "gold/old_table/data.parquet" not in keys
