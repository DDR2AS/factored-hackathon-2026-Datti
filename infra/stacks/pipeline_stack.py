"""Data pipeline: one Lambda per partition, fanned out by a Step Functions map.

Owner of the pipeline code: diego (src/handlers/pipeline.py calls the local pipeline
functions). The same ``run_partition(table, date)`` code runs on a laptop with DuckDB.
Start an execution with input like:
    {"partitions": [{"table": "transactions", "date": "2026-06-16"}, ...]}
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_logs as logs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from .common import python_function
from .config import StageConfig
from .data_stack import DataStack


class PipelineStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, cfg: StageConfig, data: DataStack, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.function = python_function(
            self,
            "PipelineFn",
            cfg,
            slug="pipeline",
            handler="handlers.pipeline.handler",
            description="Runs one pipeline partition: raw CSV -> silver -> gold Parquet",
            memory_mb=3008,
            timeout=cdk.Duration.minutes(15),
            environment={
                **data.table_environment(),
                "BUCKET_RAW": data.raw.bucket_name,
            },
        )
        data.raw.grant_read(self.function)
        data.lake.grant_read_write(self.function)

        run_partition = tasks.LambdaInvoke(
            self,
            "RunPartition",
            lambda_function=self.function,
            payload_response_only=True,
            retry_on_service_exceptions=True,
        )
        run_partition.add_retry(
            errors=["States.TaskFailed"],
            max_attempts=2,
            interval=cdk.Duration.seconds(10),
            backoff_rate=2,
        )
        for_each_partition = sfn.Map(
            self,
            "ForEachPartition",
            items_path="$.partitions",
            max_concurrency=10,
            result_path=sfn.JsonPath.DISCARD,
        )
        for_each_partition.item_processor(run_partition)

        self.state_machine = sfn.StateMachine(
            self,
            "PipelineStateMachine",
            state_machine_name=cfg.name("pipeline"),
            definition_body=sfn.DefinitionBody.from_chainable(for_each_partition),
            timeout=cdk.Duration.hours(6),
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=logs.LogGroup(
                    self,
                    "PipelineStateMachineLogs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=cdk.RemovalPolicy.DESTROY,
                ),
                level=sfn.LogLevel.ERROR,
            ),
        )

        cdk.CfnOutput(self, "PipelineStateMachineArn", value=self.state_machine.state_machine_arn)
