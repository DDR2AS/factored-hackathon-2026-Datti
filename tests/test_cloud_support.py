"""Shared helpers of the tests/test_cloud_*.py modules (moto, offline, SYNTHETIC data). No tests here.

The tables mirror infra/stacks/data_stack.py (keys, GSIs, TTL attribute); the serving items are built
with ``pipeline.serving._item``, the encoder of the real DynamoDB import file.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

REGION = "us-east-2"
SESSIONS = "expvivo-test-sessions"
CASES = "expvivo-test-cases"
SERVING = "expvivo-test-serving"
ACCOUNT = "123456789012"


@pytest.fixture
def aws(monkeypatch):
    """moto for every AWS service, fake credentials, fresh cached clients."""
    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                       "AWS_SECURITY_TOKEN": "testing", "AWS_SESSION_TOKEN": "testing",
                       "AWS_DEFAULT_REGION": REGION, "AWS_REGION": REGION}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    from moto import mock_aws

    from cloud import aws as cloud_aws

    with mock_aws():
        cloud_aws.reset_clients()
        yield
    cloud_aws.reset_clients()


def client(service: str) -> Any:
    import boto3

    return boto3.client(service, region_name=REGION)


def _wait(ddb: Any, name: str) -> None:
    ddb.get_waiter("table_exists").wait(TableName=name, WaiterConfig={"Delay": 0, "MaxAttempts": 5})


def create_sessions_table(ddb: Any, name: str = SESSIONS) -> str:
    ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",
                     AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}])
    ddb.update_time_to_live(TableName=name, TimeToLiveSpecification={"Enabled": True,
                                                                      "AttributeName": "expires_at_epoch"})
    return name


def create_cases_table(ddb: Any, name: str = CASES) -> str:
    def gsi(index: str, pk: str, sk: str, projection: str = "ALL") -> dict:
        return {"IndexName": index, "Projection": {"ProjectionType": projection},
                "KeySchema": [{"AttributeName": pk, "KeyType": "HASH"}, {"AttributeName": sk, "KeyType": "RANGE"}]}

    ddb.create_table(
        TableName=name, BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"}
                              for a in ("case_id", "customer_id", "created_at", "status", "queue_lane")],
        KeySchema=[{"AttributeName": "case_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[gsi("by_customer", "customer_id", "created_at"),
                                gsi("by_status", "status", "created_at"),
                                gsi("by_queue", "queue_lane", "created_at", "KEYS_ONLY")])
    return name


def create_serving_table(ddb: Any, name: str = SERVING, with_txn_index: bool = True) -> str:
    extra: dict[str, Any] = {}
    attrs = [{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}]
    if with_txn_index:
        attrs.append({"AttributeName": "txn_id", "AttributeType": "S"})
        extra["GlobalSecondaryIndexes"] = [{"IndexName": "by_txn", "Projection": {"ProjectionType": "KEYS_ONLY"},
                                            "KeySchema": [{"AttributeName": "txn_id", "KeyType": "HASH"}]}]
    ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST", AttributeDefinitions=attrs,
                     KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                     **extra)
    return name


# ---------------------------------------------------------------- serving items (SYNTHETIC)

def txn_record(customer_id: str, txn_id: str, day: str, time: str, amount: str, merchant: str, *,
               product_id: str = "PRD-1", status: str = "approved", txn_type: str = "purchase",
               fraud_score: float = 5.0, currency: str = "COP", category: str = "groceries") -> dict:
    return {"customer_id": customer_id, "txn_id": txn_id, "product_id": product_id, "card_last4": "4242",
            "local_date": date.fromisoformat(day), "local_time": time, "amount": Decimal(amount), "currency": currency,
            "amount_usd": (Decimal(amount) / 4000).quantize(Decimal("0.01")), "merchant_id": f"M-{merchant}",
            "merchant_name": merchant, "merchant_category": category, "status": status, "response_code": "00",
            "fraud_score": fraud_score, "txn_type": txn_type, "reversal_of": None, "channel": "POS",
            "txn_country": "CO", "is_foreign": False, "origin": "dataset"}


def serving_lines(customer_id: str = "CUS-1", other: str = "CUS-2") -> list[str]:
    """DynamoDB-JSON lines exactly as pipeline/serving.py writes them, for two customers."""
    from pipeline.serving import _item

    lines = []
    for cid, name in ((customer_id, "Lucía"), (other, "Pedro")):
        pk = f"CUST#{cid}"
        lines.append(_item(pk, "PROFILE", {
            "customer_id": cid, "display_name": name, "country": "CO", "language": "es", "locale": "es-CO",
            "time_zone": "America/Bogota", "currency": "COP", "segment": "Basic", "customer_status": "Active",
            "reference_date": date(2026, 6, 17), "n_products": 2, "n_active_products": 2, "n_cards": 1, "n_txns": 4}))
        prd, card = ("PRD-1", "CARD-1") if cid == customer_id else ("PRD-9", "CARD-9")
        lines.append(_item(pk, f"PRODUCT#{prd}", {"product_id": prd, "customer_id": cid, "kind": "credit_card",
                                                  "name": "Tarjeta Crédito •••• 4242", "currency": "COP",
                                                  "card_last4": "4242", "card_id": card, "status": "active",
                                                  "is_card": True}))
        lines.append(_item(pk, "PRODUCT#PRD-ACC" + cid[-1], {"product_id": "PRD-ACC" + cid[-1], "customer_id": cid,
                                                            "kind": "savings_account", "name": "Cuenta Ahorro",
                                                            "currency": "COP", "card_last4": None, "card_id": None,
                                                            "status": "active", "is_card": False}))
    txns = [
        txn_record(customer_id, "TXN-1", "2026-06-12", "18:20", "450000.00", "SUPER AHORRO"),
        txn_record(customer_id, "TXN-2", "2026-06-12", "18:25", "450000.00", "SUPER AHORRO"),  # duplicate
        txn_record(customer_id, "TXN-3", "2026-06-10", "09:00", "52000.00", "DIDI FOOD", status="reversed",
                   category="restaurants"),
        txn_record(customer_id, "TXN-4", "2026-06-11", "11:00", "99000.00", "TIENDA TECNO", status="declined"),
        txn_record(customer_id, "FEE-1", "2026-06-01", "03:00", "12500.00", "COMISION", txn_type="fee",
                   product_id="PRD-ACC1", category="bank_fee"),
        txn_record(other, "TXN-9", "2026-06-12", "10:00", "70000.00", "OTRA TIENDA", product_id="PRD-9"),
    ]
    for t in txns:
        lines.append(_item(f"CUST#{t['customer_id']}", f"TXN#{t['local_date']}#{t['txn_id']}", t))
    lines.append(_item(f"CUST#{customer_id}", "BASELINE", {
        "customer_id": customer_id, "window_from": date(2025, 6, 1), "window_to": date(2026, 6, 3), "n_txns": 40,
        "currency": "COP", "median_amount": Decimal("80000.00"), "p90_amount": Decimal("300000.00"),
        "median_amount_usd": 20.0, "p95_amount_usd": 90.5, "top_categories": ["groceries", "transport"],
        "top_merchants": ["SUPER AHORRO"], "known_merchant_ids": ["M-SUPER AHORRO"], "usual_hour_from": 8,
        "usual_hour_to": 21, "usual_channels": ["POS"], "usual_countries": ["CO"]}))
    lines.append(_item(f"CUST#{customer_id}", "CATBASE#groceries", {
        "customer_id": customer_id, "merchant_category": "groceries", "n": 30, "median_usd": 20.0, "p95_usd": 95.0}))
    lines.append(_item(f"CUST#{customer_id}", "FEE#PRD-ACC1#MAINT", {
        "product_id": "PRD-ACC1", "customer_id": customer_id, "product_name": "Cuenta Ahorro", "currency": "COP",
        "fee_code": "MAINT", "merchant_id": "M-COMISION", "description": "Comisión mantenimiento",
        "amount": Decimal("9900.00"), "frequency": "monthly", "synthetic": True}))
    for cid, day in (("CMP-1", date(2026, 1, 5)), ("CMP-2", date(2026, 5, 20)), ("CMP-OLD", date(2024, 1, 5))):
        lines.append(_item(f"CUST#{customer_id}", f"CONTACT#{day}#{cid}", {
            "contact_id": cid, "customer_id": customer_id, "date": day, "channel": "App", "contact_type": "complaint",
            "subcategory": "unrecognized_charge", "status": "closed", "source": "complaints"}))
    lines.append(_item("MERCHANT#M-SUPER AHORRO", "STATS", {
        "merchant_id": "M-SUPER AHORRO", "merchant_name": "SUPER AHORRO", "merchant_category": "groceries",
        "n_txns": 1852, "n_customers": 1281, "fraud_rate": 0.006, "high_score_rate": 0.003, "decline_rate": 0.05,
        "reversal_rate": 0.016, "dispute_rate": None, "median_amount_usd": 21.5, "p95_amount_usd": 90.1}))
    return lines


def load_serving(ddb: Any, table: str = SERVING, lines: list[str] | None = None) -> int:
    lines = serving_lines() if lines is None else lines
    for line in lines:
        ddb.put_item(TableName=table, Item=json.loads(line)["Item"])
    return len(lines)


class Spy:
    """Records the calls made through a boto3 client and forwards them (to moto)."""

    def __init__(self, inner: Any, fail: dict[str, Exception] | None = None) -> None:
        self.inner = inner
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail or {}

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self.inner, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            if name in self.fail:
                raise self.fail[name]
            return fn(*args, **kwargs)

        return call

    def named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]


def client_error(code: str, operation: str = "Operation") -> Exception:
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


# ---------------------------------------------------------------- the whole cloud configuration

GROUP = "expvivo-test-sla-timers"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/expvivo-test-scheduler"
TIMER_FN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:expvivo-test-case-steps"


def create_workflow() -> str:
    """A state machine and a schedule group in moto (moto does not run the machine: the tests drive
    the steps the Lambdas would run). Returns the state machine ARN."""
    sfn = client("stepfunctions")
    arn = sfn.create_state_machine(
        name="expvivo-test-case-lifecycle", roleArn=f"arn:aws:iam::{ACCOUNT}:role/expvivo-test-sfn",
        definition=json.dumps({"StartAt": "Done", "States": {"Done": {"Type": "Succeed"}}}))["stateMachineArn"]
    client("scheduler").create_schedule_group(Name=GROUP)
    return arn


def cloud_env(state_machine_arn: str, **extra: str) -> dict[str, str]:
    """What the CDK sets in the API Lambda (DataStack + WorkflowStack + common.app_environment)."""
    return {"STAGE": "dev", "STORE_BACKEND": "dynamodb", "TABLE_SESSIONS": SESSIONS, "TABLE_CASES": CASES,
            "LIFECYCLE_BACKEND": "stepfunctions", "CASE_STATE_MACHINE_ARN": state_machine_arn,
            "SLA_SCHEDULE_GROUP": GROUP, "SLA_SCHEDULER_ROLE_ARN": ROLE, "SLA_TARGET_FUNCTION_ARN": TIMER_FN,
            "TRACE_SINK": "stdout", "GATEWAY_BACKEND": "demo", **extra}


def make_cloud_app(env: dict[str, str], clock: Any) -> Any:
    """build_app on moto with spies on the Step Functions and Scheduler clients, and a memory
    trace sink for the lifecycle events."""
    from conversation.app import build_app
    from conversation.store import MemoryTraceSink

    app = build_app(env=env, clock=clock, use_env_llm=False, sleep=lambda s: None)
    app.lifecycle._sfn = Spy(client("stepfunctions"))
    app.lifecycle._scheduler = Spy(client("scheduler"))
    app.stores.traces = MemoryTraceSink()
    return app
