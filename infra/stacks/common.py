"""Helpers every stack uses, so Lambdas and permissions look the same everywhere."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import aws_cdk as cdk
import jsii
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from constructs import Construct

from .config import StageConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
# Lambda code is the repo's src/ folder; handlers live in src/handlers/.
SRC_DIR = REPO_ROOT / "src"
LAMBDA_EXCLUDES = ["**/__pycache__", "**/*.pyc", "**/*.ipynb", "etl"]
# Dependencies of the API and workflow functions; the pipeline function has its own list
# (DuckDB + pandas, which would make every package bigger for nothing).
API_REQUIREMENTS = "requirements-lambda.txt"
PIPELINE_REQUIREMENTS = "cloud/requirements-pipeline.txt"


@jsii.implements(cdk.ILocalBundling)
class _PipLocalBundling:
    """Bundle without Docker (docs/propuesta_infra.patch, verified 29 sep): pip downloads the Linux
    x86_64 / CPython 3.12 wheels of the requirements file (binary wheels only, nothing is compiled
    here) and src/ is copied next to them. If pip fails, CDK falls back to the Docker image."""

    def __init__(self, requirements: str) -> None:
        self.requirements = requirements

    def try_bundle(self, output_dir: str, options=None) -> bool:  # jsii passes both positionally
        command = [
            sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir",
            "--platform", "manylinux2014_x86_64", "--implementation", "cp",
            "--python-version", "3.12", "--only-binary=:all:",
            "--target", output_dir, "-r", str(SRC_DIR / self.requirements),
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stdout + result.stderr, file=sys.stderr)
            return False
        shutil.copytree(
            SRC_DIR, output_dir, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.ipynb", "etl"),
        )
        return True


def lambda_code(cfg: StageConfig, requirements: str = API_REQUIREMENTS) -> _lambda.Code:
    """Package src/ as the Lambda asset.

    With ``-c bundle=true`` the dependencies in ``src/<requirements>`` are installed with pip for
    Linux x86_64 / Python 3.12 on this machine (no Docker needed) or, if that fails, inside the
    official Lambda build image (needs Docker). Without it the asset is the plain source, which
    is enough for ``cdk synth`` and for handlers that only use the standard library and boto3.
    """
    if not cfg.bundle_dependencies:
        return _lambda.Code.from_asset(str(SRC_DIR), exclude=LAMBDA_EXCLUDES)
    return _lambda.Code.from_asset(
        str(SRC_DIR),
        exclude=LAMBDA_EXCLUDES,
        bundling=cdk.BundlingOptions(
            local=_PipLocalBundling(requirements),
            image=_lambda.Runtime.PYTHON_3_12.bundling_image,
            command=[
                "bash",
                "-c",
                f"pip install --no-cache-dir -r {requirements} -t /asset-output "
                "&& cp -au . /asset-output && rm -rf /asset-output/etl",
            ],
        ),
    )


def python_function(
    scope: Construct,
    construct_id: str,
    cfg: StageConfig,
    *,
    slug: str,
    handler: str,
    description: str,
    environment: dict[str, str] | None = None,
    memory_mb: int = 1024,
    timeout: cdk.Duration = cdk.Duration.seconds(30),
    requirements: str = API_REQUIREMENTS,
    ephemeral_storage_mb: int | None = None,
) -> _lambda.Function:
    """A Python 3.12 Lambda named ``expvivo-<stage>-<slug>`` with its own log group."""
    log_group = logs.LogGroup(
        scope,
        f"{construct_id}Logs",
        log_group_name=f"/aws/lambda/{cfg.name(slug)}",
        retention=logs.RetentionDays.ONE_MONTH,
        removal_policy=cdk.RemovalPolicy.DESTROY,
    )
    return _lambda.Function(
        scope,
        construct_id,
        function_name=cfg.name(slug),
        description=description,
        runtime=_lambda.Runtime.PYTHON_3_12,
        architecture=_lambda.Architecture.X86_64,
        handler=handler,
        code=lambda_code(cfg, requirements),
        memory_size=memory_mb,
        timeout=timeout,
        ephemeral_storage_size=cdk.Size.mebibytes(ephemeral_storage_mb) if ephemeral_storage_mb else None,
        tracing=_lambda.Tracing.ACTIVE,
        log_group=log_group,
        environment={"STAGE": cfg.stage, "POWERTOOLS_SERVICE_NAME": slug, **(environment or {})},
    )


def allow_bedrock(function: _lambda.IFunction, cfg: StageConfig | None = None) -> None:
    """Let a function call Anthropic models on Bedrock.

    - ``bedrock-mantle:CreateInference``: the Messages API endpoint
      (``https://bedrock-mantle.<region>.api.aws/anthropic/v1/messages``) that src/llm calls through
      the ``AnthropicBedrockMantle`` client of the Anthropic SDK. Resources from ``-c
      bedrockMantleResources`` ("*" until the model ARN format is confirmed), always limited to
      the stage's region.
    - ``bedrock:InvokeModel*`` on Anthropic foundation models and inference profiles (the
      InvokeModel path, kept for clients that use it).
    """
    function.add_to_role_policy(
        iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                "arn:aws:bedrock:*::foundation-model/anthropic.*",
                f"arn:aws:bedrock:*:{cdk.Aws.ACCOUNT_ID}:inference-profile/*",
            ],
        )
    )
    function.add_to_role_policy(
        iam.PolicyStatement(
            actions=["bedrock-mantle:CreateInference"],
            resources=list(cfg.bedrock_mantle_resources) if cfg is not None else ["*"],
            conditions={"StringEquals": {"aws:RequestedRegion": cfg.region if cfg is not None else cdk.Aws.REGION}},
        )
    )


def app_environment(cfg: StageConfig) -> dict[str, str]:
    """The application backends every cloud function selects (INTERFACES.md #9): DynamoDB stores
    and serving table, the Step Functions lifecycle, the trained models, the G2 investigator and
    CloudWatch traces. Locally the same variables come from .env."""
    return {
        "STORE_BACKEND": "dynamodb",
        "LIFECYCLE_BACKEND": "stepfunctions",
        "GATEWAY_BACKEND": "composite",
        "GATEWAY_DATA_BACKEND": "dynamodb",
        "M1_BACKEND": "model",
        "M2_BACKEND": "model",
        "INVESTIGATOR_BACKEND": cfg.investigator_backend,
        "INVESTIGATOR_MAX_SECONDS": "90",
        "TRACE_SINK": "stdout",
        "TRACE_DIR": "/tmp/traces",  # the only writable path in Lambda (used with TRACE_SINK=jsonl)
        "SESSION_RATE_PER_MINUTE": "60",
        "CASE_CLOSE_AFTER_SECONDS": "30",
    }


def model_environment(cfg: StageConfig) -> dict[str, str]:
    """Model IDs by role (application code reads them through src/llm, never hard-codes them) plus
    ``app_environment``: every function that gets the model variables also runs the application."""
    return {
        "LLM_PROVIDER": cfg.llm_provider,
        "MODEL_CHAT": cfg.models["chat"],
        "MODEL_INVESTIGATOR": cfg.models["investigator"],
        "MODEL_JUDGE": cfg.models["judge"],
        **app_environment(cfg),
    }
