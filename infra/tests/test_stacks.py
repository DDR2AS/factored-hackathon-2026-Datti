"""Guardrails every infrastructure change must keep. Run: cd infra && python -m pytest -q

These tests synthesize the stacks without AWS credentials, so CI and every teammate
can run them before opening a pull request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

INFRA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRA_DIR))

from stacks.api_stack import ApiStack  # noqa: E402
from stacks.config import load_config  # noqa: E402
from stacks.data_stack import DataStack  # noqa: E402
from stacks.monitoring_stack import MonitoringStack  # noqa: E402
from stacks.pipeline_stack import PipelineStack  # noqa: E402
from stacks.web_stack import WebStack  # noqa: E402
from stacks.workflow_stack import WorkflowStack  # noqa: E402

BASE_CONTEXT = json.loads((INFRA_DIR / "cdk.json").read_text(encoding="utf-8"))["context"]


def build(stage: str, **context) -> dict[str, Template]:
    app = cdk.App(context={**BASE_CONTEXT, "stage": stage, **context})
    cfg = load_config(app)
    env = cdk.Environment(account="123456789012", region=cfg.region)
    data = DataStack(app, cfg.stack_id("Data"), cfg=cfg, env=env)
    pipeline = PipelineStack(app, cfg.stack_id("Pipeline"), cfg=cfg, data=data, env=env)
    workflow = WorkflowStack(app, cfg.stack_id("Workflow"), cfg=cfg, data=data, env=env)
    api = ApiStack(app, cfg.stack_id("Api"), cfg=cfg, data=data, workflow=workflow, env=env)
    web = WebStack(app, cfg.stack_id("Web"), cfg=cfg, api=api, env=env)
    monitoring = MonitoringStack(
        app, cfg.stack_id("Monitoring"), cfg=cfg, api=api, workflow=workflow, pipeline=pipeline, env=env
    )
    stacks = {
        "data": data,
        "pipeline": pipeline,
        "workflow": workflow,
        "api": api,
        "web": web,
        "monitoring": monitoring,
    }
    return {name: Template.from_stack(stack) for name, stack in stacks.items()}


@pytest.fixture(scope="module")
def dev() -> dict[str, Template]:
    return build("dev")


@pytest.fixture(scope="module")
def prod() -> dict[str, Template]:
    return build("prod")


def test_every_bucket_blocks_public_access(dev):
    for template in dev.values():
        for bucket in template.find_resources("AWS::S3::Bucket").values():
            config = bucket["Properties"]["PublicAccessBlockConfiguration"]
            assert all(config[key] for key in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"))


def test_resource_names_carry_the_stage(dev):
    for table in dev["data"].find_resources("AWS::DynamoDB::Table").values():
        assert table["Properties"]["TableName"].startswith("expvivo-dev-")
    for template in dev.values():
        for function in template.find_resources("AWS::Lambda::Function").values():
            name = function["Properties"].get("FunctionName")
            if isinstance(name, str):
                assert name.startswith("expvivo-dev-")


def test_sessions_expire_through_ttl(dev):
    dev["data"].has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "expvivo-dev-sessions",
            # a NUMBER attribute (epoch seconds) written by src/cloud/dynamodb_store.py
            "TimeToLiveSpecification": {"AttributeName": "expires_at_epoch", "Enabled": True},
        },
    )


def test_tables_are_on_demand(dev):
    for table in dev["data"].find_resources("AWS::DynamoDB::Table").values():
        assert table["Properties"]["BillingMode"] == "PAY_PER_REQUEST"


def test_prod_keeps_data_and_dev_cleans_up(dev, prod):
    for table in prod["data"].find_resources("AWS::DynamoDB::Table").values():
        assert table["DeletionPolicy"] == "Retain"
    for table in dev["data"].find_resources("AWS::DynamoDB::Table").values():
        assert table["DeletionPolicy"] == "Delete"


def test_app_lambdas_know_their_stage(dev):
    for key in ("pipeline", "workflow", "api"):
        for function in dev[key].find_resources("AWS::Lambda::Function").values():
            variables = function["Properties"].get("Environment", {}).get("Variables", {})
            assert variables.get("STAGE") == "dev"


def test_model_ids_come_from_config_not_code(dev):
    dev["api"].has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "expvivo-dev-api",
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "MODEL_CHAT": BASE_CONTEXT["models"]["chat"],
                        "MODEL_INVESTIGATOR": BASE_CONTEXT["models"]["investigator"],
                    }
                )
            },
        },
    )


def test_analyst_routes_require_a_jwt(dev):
    routes = dev["api"].find_resources("AWS::ApiGatewayV2::Route")
    for route in routes.values():
        key = route["Properties"]["RouteKey"]
        if " /analyst/" in key:
            assert route["Properties"]["AuthorizationType"] == "JWT", key


def test_api_is_throttled(dev):
    dev["api"].has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {"DefaultRouteSettings": Match.object_like({"ThrottlingRateLimit": Match.any_value()})},
    )


def test_bad_stage_name_is_rejected():
    app = cdk.App(context={**BASE_CONTEXT, "stage": "Dev_Diego"})
    with pytest.raises(ValueError):
        load_config(app)


# ---------------------------------------------------------------- cloud backends (src/cloud)

def _function(template: Template, name: str) -> dict:
    functions = template.find_resources("AWS::Lambda::Function")
    return next(f for f in functions.values() if f["Properties"].get("FunctionName") == name)


def _policy_actions(template: Template) -> set[str]:
    actions: set[str] = set()
    for policy in template.find_resources("AWS::IAM::Policy").values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            found = statement["Action"]
            actions.update([found] if isinstance(found, str) else found)
    return actions


def test_app_functions_select_the_cloud_backends(dev):
    expected = {
        "STORE_BACKEND": "dynamodb", "LIFECYCLE_BACKEND": "stepfunctions", "GATEWAY_BACKEND": "composite",
        "GATEWAY_DATA_BACKEND": "dynamodb", "M1_BACKEND": "model", "M2_BACKEND": "model",
        "INVESTIGATOR_BACKEND": "g2", "TRACE_SINK": "stdout", "LLM_PROVIDER": "bedrock",
    }
    for key, name in (("api", "expvivo-dev-api"), ("workflow", "expvivo-dev-investigator"),
                      ("workflow", "expvivo-dev-case-steps")):
        variables = _function(dev[key], name)["Properties"]["Environment"]["Variables"]
        for var, value in expected.items():
            assert variables.get(var) == value, (name, var)
        assert variables["TRACE_DIR"].startswith("/tmp/")  # the only writable path in Lambda
        assert {"TABLE_SESSIONS", "TABLE_CASES", "TABLE_SERVING"} <= set(variables)
    api = _function(dev["api"], "expvivo-dev-api")["Properties"]["Environment"]["Variables"]
    assert {"CASE_STATE_MACHINE_ARN", "SLA_SCHEDULE_GROUP", "SLA_SCHEDULER_ROLE_ARN", "SLA_TARGET_FUNCTION_ARN"} <= set(api)


def test_store_and_gateway_indexes(dev):
    tables = dev["data"].find_resources("AWS::DynamoDB::Table")
    by_name = {t["Properties"]["TableName"]: t["Properties"] for t in tables.values()}
    queue = next(i for i in by_name["expvivo-dev-cases"]["GlobalSecondaryIndexes"] if i["IndexName"] == "by_queue")
    assert queue["Projection"]["ProjectionType"] == "KEYS_ONLY"
    assert queue["KeySchema"][0]["AttributeName"] == "queue_lane"
    by_txn = next(i for i in by_name["expvivo-dev-serving"]["GlobalSecondaryIndexes"] if i["IndexName"] == "by_txn")
    assert by_txn["Projection"]["ProjectionType"] == "KEYS_ONLY" and by_txn["KeySchema"][0]["AttributeName"] == "txn_id"
    assert "ImportSourceSpecification" not in by_name["expvivo-dev-serving"]


def test_serving_table_can_be_imported_from_the_lake():
    templates = build("dev", servingImport="20261030120000")
    tables = templates["data"].find_resources("AWS::DynamoDB::Table")
    serving = next(t["Properties"] for t in tables.values()
                   if t["Properties"]["TableName"] == "expvivo-dev-serving-20261030120000")
    spec = serving["ImportSourceSpecification"]
    assert spec["InputFormat"] == "DYNAMODB_JSON" and spec["InputCompressionType"] == "GZIP"
    assert spec["S3BucketSource"]["S3KeyPrefix"] == "serving/20261030120000/"
    app = cdk.App(context={**BASE_CONTEXT, "servingImport": "../etc"})
    with pytest.raises(ValueError):
        load_config(app)
    # the other stacks use the table by name: a new import never changes an export in use
    for key in ("api", "workflow"):
        rendered = json.dumps(templates[key].to_json())
        assert "expvivo-dev-serving-20261030120000" in rendered
        assert not any("Serving" in part.split('"')[0] for part in rendered.split('"Fn::ImportValue": "')[1:]), key


def test_bedrock_through_mantle_and_invoke_model(dev):
    for key in ("api", "workflow"):
        actions = _policy_actions(dev[key])
        assert {"bedrock-mantle:CreateInference", "bedrock:InvokeModel"} <= actions, key


def test_lifecycle_permissions(dev):
    api = _policy_actions(dev["api"])
    assert {"states:StartExecution", "states:SendTaskSuccess", "scheduler:CreateSchedule",
            "scheduler:DeleteSchedule"} <= api
    workflow = _policy_actions(dev["workflow"])
    assert {"states:SendTaskSuccess", "states:SendTaskFailure"} <= workflow  # late task tokens (case steps)


def test_case_state_machine_matches_the_handlers(dev):
    machines = dev["workflow"].find_resources("AWS::StepFunctions::StateMachine")
    definition = json.dumps(next(iter(machines.values()))["Properties"]["DefinitionString"])
    for fragment in ('\\"action\\":\\"register_task_token\\"', '\\"task_token.$\\":\\"$$.Task.Token\\"',
                     '\\"execution_arn.$\\":\\"$$.Execution.Id\\"', '\\"round.$\\":\\"$.round\\"',
                     '\\"error.$\\":\\"$.investigation_error.Error\\"', '\\"action\\":\\"close_case\\"',
                     '\\"SecondsPath\\":\\"$.notify.close_after_seconds\\"', '\\"Variable\\":\\"$.notify.close\\"'):
        assert fragment in definition, fragment


def test_pipeline_runs_partitions_then_one_gold_step(dev):
    machines = dev["pipeline"].find_resources("AWS::StepFunctions::StateMachine")
    definition = json.dumps(next(iter(machines.values()))["Properties"]["DefinitionString"])
    for step in ('\\"step\\":\\"dimensions\\"', '\\"refresh_gold\\":false', '\\"step\\":\\"gold\\"'):
        assert step in definition, step
    fn = _function(dev["pipeline"], "expvivo-dev-pipeline")["Properties"]
    assert fn["EphemeralStorage"]["Size"] >= 2048
    assert fn["Environment"]["Variables"]["DATA_DIR"] == "/tmp/data"
    assert fn["Environment"]["Variables"]["RAW_PREFIX"] == "data/"
