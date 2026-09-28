"""Storage: S3 buckets, DynamoDB tables, Glue database and Athena workgroup.

Owner: andres. Table and bucket shapes follow INTERFACES.md (#3 case record, #5 gold
tables, #7 trace event). Every other stack receives this stack and grants itself access.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_athena as athena
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_glue as glue
from aws_cdk import aws_s3 as s3
from constructs import Construct

from .config import StageConfig


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: StageConfig, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.cfg = cfg

        # --- S3 -------------------------------------------------------------------------
        self.lake = self._bucket("Lake")  # bronze/, silver/, gold/ prefixes (Parquet)
        self.artifacts = self._bucket("Artifacts", versioned=True)  # models/, eval/, athena-results/
        self.traces = self._bucket(
            "Traces",
            lifecycle_rules=[s3.LifecycleRule(expiration=cdk.Duration.days(90))],
        )
        # The raw dataset copy already exists; it is referenced, never created or deleted here.
        self.raw = s3.Bucket.from_bucket_name(self, "Raw", cfg.raw_bucket_name)

        # --- DynamoDB -------------------------------------------------------------------
        self.sessions = self._table("Sessions", "sessions", "session_id", ttl="expires_at")
        self.cases = self._table("Cases", "cases", "case_id")
        self.cases.add_global_secondary_index(
            index_name="by_customer",
            partition_key=dynamodb.Attribute(name="customer_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )
        self.cases.add_global_secondary_index(
            index_name="by_status",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )
        # Serving table for the chat and the gateway: pk = "CUST#<customer_id>",
        # sk = "PROFILE" | "CARD#<id>" | "TXN#<local_date>#<txn_id>" | "BASELINE".
        self.serving = self._table("Serving", "serving", "pk", sort_key="sk")
        # Prepared judge-mode customers live apart from gold data so a demo reset never touches it.
        self.demo = self._table("Demo", "demo", "pk", sort_key="sk")

        # --- Glue and Athena ------------------------------------------------------------
        self.glue_database_name = cfg.prefix.replace("-", "_") + "_lake"
        glue.CfnDatabase(
            self,
            "LakeDatabase",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.glue_database_name,
                description=f"Expediente Vivo lake tables ({cfg.stage})",
                location_uri=self.lake.s3_url_for_object("gold/"),
            ),
        )
        self.athena_workgroup_name = cfg.name("analytics")
        athena.CfnWorkGroup(
            self,
            "AnalyticsWorkGroup",
            name=self.athena_workgroup_name,
            recursive_delete_option=not cfg.is_prod,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                # Cost guard: no single query may scan more than 10 GB.
                bytes_scanned_cutoff_per_query=10 * 1024**3,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=self.artifacts.s3_url_for_object("athena-results/"),
                ),
            ),
        )

        cdk.CfnOutput(self, "LakeBucketName", value=self.lake.bucket_name)
        cdk.CfnOutput(self, "ArtifactsBucketName", value=self.artifacts.bucket_name)
        cdk.CfnOutput(self, "TracesBucketName", value=self.traces.bucket_name)

    def table_environment(self) -> dict[str, str]:
        """Environment variables application code reads (INTERFACES.md #9)."""
        return {
            "TABLE_SESSIONS": self.sessions.table_name,
            "TABLE_CASES": self.cases.table_name,
            "TABLE_SERVING": self.serving.table_name,
            "TABLE_DEMO": self.demo.table_name,
            "BUCKET_LAKE": self.lake.bucket_name,
            "BUCKET_ARTIFACTS": self.artifacts.bucket_name,
            "BUCKET_TRACES": self.traces.bucket_name,
            "GLUE_DATABASE": self.glue_database_name,
            "ATHENA_WORKGROUP": self.athena_workgroup_name,
        }

    def _bucket(self, construct_id: str, **extra) -> s3.Bucket:
        return s3.Bucket(
            self,
            construct_id,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=self.cfg.removal_policy,
            auto_delete_objects=not self.cfg.is_prod,
            **extra,
        )

    def _table(
        self,
        construct_id: str,
        suffix: str,
        partition_key: str,
        *,
        sort_key: str | None = None,
        ttl: str | None = None,
    ) -> dynamodb.Table:
        return dynamodb.Table(
            self,
            construct_id,
            table_name=self.cfg.name(suffix),
            partition_key=dynamodb.Attribute(name=partition_key, type=dynamodb.AttributeType.STRING),
            sort_key=(
                dynamodb.Attribute(name=sort_key, type=dynamodb.AttributeType.STRING) if sort_key else None
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute=ttl,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=self.cfg.is_prod
            ),
            removal_policy=self.cfg.removal_policy,
        )
