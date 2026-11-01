"""Data pipeline: one Lambda per partition, fanned out by a Step Functions map, then one gold step.

Owner of the pipeline code: diego (src/handlers/pipeline.py calls the local pipeline
functions). The same ``run_partition(table, date)`` code runs on a laptop with DuckDB.
Start an execution with input like:
    {"partitions": [{"table": "transactions", "date": "2026-06-16"}, ...]}
``Dimensions`` first loads the six dimension tables; each partition then refreshes bronze and silver
in the lake; ``RefreshGold`` rebuilds gold from the whole silver layer and writes the DynamoDB import
file to ``serving/<version>/`` (its output says which version to pass as ``-c servingImport=<version>``).
Step Functions inputs are capped at 256 KB: start one execution per table for a full backfill.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_logs as logs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from .common import PIPELINE_REQUIREMENTS, python_function
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
            # DuckDB + pandas only (pipeline.run imports pandas through pipeline.quality)
            requirements=PIPELINE_REQUIREMENTS,
            ephemeral_storage_mb=cfg.pipeline_storage_mb,  # /tmp holds the silver layer for the gold step
            environment={
                **data.table_environment(),
                "BUCKET_RAW": data.raw.bucket_name,
                "RAW_PREFIX": cfg.raw_prefix,
                "DATA_DIR": "/tmp/data",
                "DUCKDB_MEMORY": "2GB",
                "DUCKDB_THREADS": "2",
            },
        )
        data.raw.grant_read(self.function)
        data.lake.grant_read_write(self.function)
        data.lake.grant_delete(self.function)  # a re-run replaces its partition (and gold/) in the lake

        # Each partition only refreshes bronze and silver; gold (and the DynamoDB import file) is
        # rebuilt once at the end, from the whole silver layer (handlers/pipeline.py).
        run_partition = tasks.LambdaInvoke(
            self,
            "RunPartition",
            lambda_function=self.function,
            payload=sfn.TaskInput.from_object(
                {"table.$": "$.table", "date.$": "$.date", "refresh_gold": False}
            ),
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
        dimensions = tasks.LambdaInvoke(
            self,
            "Dimensions",
            lambda_function=self.function,
            payload=sfn.TaskInput.from_object({"step": "dimensions"}),
            payload_response_only=True,
            retry_on_service_exceptions=True,
            result_path=sfn.JsonPath.DISCARD,  # the partitions join these silver tables
        )
        refresh_gold = tasks.LambdaInvoke(
            self,
            "RefreshGold",
            lambda_function=self.function,
            payload=sfn.TaskInput.from_object({"step": "gold", "serving": True}),
            payload_response_only=True,
            retry_on_service_exceptions=True,
            result_path="$.gold",  # {gold, serving: {import_prefix, key, items}} for the import
        )

        self.state_machine = sfn.StateMachine(
            self,
            "PipelineStateMachine",
            state_machine_name=cfg.name("pipeline"),
            definition_body=sfn.DefinitionBody.from_chainable(
                dimensions.next(for_each_partition).next(refresh_gold)
            ),
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
