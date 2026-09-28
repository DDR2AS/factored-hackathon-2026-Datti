#!/usr/bin/env python3
"""CDK entry point for Expediente Vivo v2. See infra/README.md before changing anything.

Stacks per stage (``-c stage=<name>``, default ``dev``):
  Data        S3 buckets, DynamoDB tables, Glue database, Athena workgroup
  Pipeline    Pipeline Lambda + Step Functions map over partitions
  Workflow    Case lifecycle state machine, investigator, SLA timers
  Api         HTTP API, API Lambda, analyst Cognito user pool
  Web         CloudFront + S3 site, /api/* forwarded to the HTTP API
  Monitoring  Alarms, SNS topic, optional account budget
Account-wide, only with ``-c withAccess=true`` (andres):
  Access      IAM group (and optional users) allowed to deploy through CDK
"""

import aws_cdk as cdk

from stacks.access_stack import AccessStack
from stacks.api_stack import ApiStack
from stacks.config import PROJECT_TITLE, load_config
from stacks.data_stack import DataStack
from stacks.monitoring_stack import MonitoringStack
from stacks.pipeline_stack import PipelineStack
from stacks.web_stack import WebStack
from stacks.workflow_stack import WorkflowStack

app = cdk.App()
cfg = load_config(app)
env = cdk.Environment(account=cfg.account, region=cfg.region)

data = DataStack(app, cfg.stack_id("Data"), cfg=cfg, env=env)
pipeline = PipelineStack(app, cfg.stack_id("Pipeline"), cfg=cfg, data=data, env=env)
workflow = WorkflowStack(app, cfg.stack_id("Workflow"), cfg=cfg, data=data, env=env)
api = ApiStack(app, cfg.stack_id("Api"), cfg=cfg, data=data, workflow=workflow, env=env)
WebStack(app, cfg.stack_id("Web"), cfg=cfg, api=api, env=env)
MonitoringStack(
    app, cfg.stack_id("Monitoring"), cfg=cfg, api=api, workflow=workflow, pipeline=pipeline, env=env
)

if cfg.with_access:
    AccessStack(app, f"{PROJECT_TITLE}-Access", cfg=cfg, env=env)

for key, value in cfg.tags.items():
    cdk.Tags.of(app).add(key, value)

app.synth()
