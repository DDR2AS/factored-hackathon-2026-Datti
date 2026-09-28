"""Helpers every stack uses, so Lambdas and permissions look the same everywhere."""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from constructs import Construct

from .config import StageConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
# Lambda code is the repo's src/ folder; handlers live in src/handlers/.
SRC_DIR = REPO_ROOT / "src"
LAMBDA_EXCLUDES = ["**/__pycache__", "**/*.pyc", "**/*.ipynb", "etl"]


def lambda_code(cfg: StageConfig) -> _lambda.Code:
    """Package src/ as the Lambda asset.

    With ``-c bundle=true`` the dependencies in ``src/requirements-lambda.txt`` are
    installed inside the official Lambda build image (needs Docker). Without it the
    asset is the plain source, which is enough for ``cdk synth`` and for handlers
    that only use the standard library and boto3.
    """
    if not cfg.bundle_dependencies:
        return _lambda.Code.from_asset(str(SRC_DIR), exclude=LAMBDA_EXCLUDES)
    return _lambda.Code.from_asset(
        str(SRC_DIR),
        exclude=LAMBDA_EXCLUDES,
        bundling=cdk.BundlingOptions(
            image=_lambda.Runtime.PYTHON_3_12.bundling_image,
            command=[
                "bash",
                "-c",
                "pip install --no-cache-dir -r requirements-lambda.txt -t /asset-output "
                "&& cp -au . /asset-output",
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
        code=lambda_code(cfg),
        memory_size=memory_mb,
        timeout=timeout,
        tracing=_lambda.Tracing.ACTIVE,
        log_group=log_group,
        environment={"STAGE": cfg.stage, "POWERTOOLS_SERVICE_NAME": slug, **(environment or {})},
    )


def allow_bedrock(function: _lambda.IFunction) -> None:
    """Let a function call Anthropic models on Bedrock, directly or via inference profiles.

    Adjust the actions if the LLM client moves to a different Bedrock API.
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


def model_environment(cfg: StageConfig) -> dict[str, str]:
    """Model IDs by role; application code reads these through src/llm, never hard-codes them."""
    return {
        "LLM_PROVIDER": "bedrock",
        "MODEL_CHAT": cfg.models["chat"],
        "MODEL_INVESTIGATOR": cfg.models["investigator"],
        "MODEL_JUDGE": cfg.models["judge"],
    }
