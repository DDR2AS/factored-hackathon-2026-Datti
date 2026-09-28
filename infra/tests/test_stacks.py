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


def build(stage: str) -> dict[str, Template]:
    app = cdk.App(context={**BASE_CONTEXT, "stage": stage})
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
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
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
