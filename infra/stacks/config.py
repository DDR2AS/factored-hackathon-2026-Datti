"""Stage configuration shared by every stack.

All settings come from CDK context: defaults live in ``infra/cdk.json`` and any
value can be overridden on the command line, e.g. ``cdk synth -c stage=dev-diego``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import aws_cdk as cdk

PROJECT = "expvivo"
PROJECT_TITLE = "ExpVivo"
_STAGE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,19}$")
_SERVING_VERSION = re.compile(r"^[0-9]{8,20}$")
_LLM_PROVIDERS = ("bedrock", "none", "mock")


@dataclass(frozen=True)
class StageConfig:
    stage: str
    region: str
    account: str | None
    owner: str
    raw_bucket_name: str
    models: dict[str, str]
    alert_email: str | None
    bundle_dependencies: bool
    warmup: bool
    with_access: bool
    create_budget: bool
    budget_limits_usd: tuple[int, ...]
    deployers: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    # Application backends in AWS (INTERFACES.md #9; common.app_environment). ``llm_provider`` none
    # runs the chat with the rule extractor and the investigator with its deterministic policy
    # (a first deploy before Bedrock access is confirmed).
    llm_provider: str = "bedrock"
    investigator_backend: str = "g2"
    # ARNs for bedrock-mantle:CreateInference (the Messages API endpoint of the Mantle client).
    # "*" until the resource format is confirmed in the Service Authorization Reference; the
    # statement is also limited to the stage's region by a condition.
    bedrock_mantle_resources: tuple[str, ...] = ("*",)
    # Key prefix of the raw CSVs in the raw bucket (the organizers' layout is data/<table>/...).
    raw_prefix: str = "data/"
    # Version of a serving export in the lake (serving/<version>/, written by the pipeline's gold
    # step). When set, the serving table is the DynamoDB import of that file (a new table
    # expvivo-<stage>-serving-<version>); empty = an empty serving table.
    serving_import: str | None = None
    pipeline_storage_mb: int = 4096

    @property
    def is_prod(self) -> bool:
        return self.stage == "prod"

    @property
    def prefix(self) -> str:
        return f"{PROJECT}-{self.stage}"

    def name(self, suffix: str) -> str:
        """Physical name for a resource, unique per stage: ``expvivo-<stage>-<suffix>``."""
        return f"{self.prefix}-{suffix}"

    def stack_id(self, name: str) -> str:
        return f"{PROJECT_TITLE}-{self.stage}-{name}"

    @property
    def removal_policy(self) -> cdk.RemovalPolicy:
        # Only prod keeps data when a stack is deleted; developer stages clean up after themselves.
        return cdk.RemovalPolicy.RETAIN if self.is_prod else cdk.RemovalPolicy.DESTROY

    @property
    def tags(self) -> dict[str, str]:
        return {
            "project": PROJECT,
            "stage": self.stage,
            "owner": self.owner,
            "managed-by": "cdk",
        }


def _context(app: cdk.App, key: str, default=None):
    value = app.node.try_get_context(key)
    return default if value is None else value


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return list(value)


def load_config(app: cdk.App) -> StageConfig:
    stage = str(_context(app, "stage", "dev"))
    if not _STAGE_PATTERN.match(stage):
        raise ValueError(
            f"Invalid stage '{stage}': use 2-20 lowercase letters, digits or hyphens, "
            "starting with a letter (for example dev, prod or dev-diego)."
        )

    models = dict(_context(app, "models", {}))
    for role in ("chat", "investigator", "judge"):
        if role not in models:
            raise ValueError(f"Context 'models' must define a model ID for role '{role}'.")

    alert_email = _context(app, "alertEmail") or None
    create_budget = _as_bool(_context(app, "createBudget", False))
    if create_budget and not alert_email:
        raise ValueError("createBudget=true needs an alertEmail to notify.")

    llm_provider = str(_context(app, "llmProvider", "bedrock")).strip().lower()
    if llm_provider not in _LLM_PROVIDERS:
        raise ValueError(f"llmProvider must be one of {', '.join(_LLM_PROVIDERS)}, not '{llm_provider}'.")
    investigator_backend = str(_context(app, "investigatorBackend", "g2")).strip().lower()
    if investigator_backend not in ("g2", "stub"):
        raise ValueError(f"investigatorBackend must be g2 or stub, not '{investigator_backend}'.")
    serving_import = str(_context(app, "servingImport", "") or "").strip() or None
    if serving_import is not None and not _SERVING_VERSION.match(serving_import):
        raise ValueError(f"servingImport must be the version of a serving export (digits), not '{serving_import}'.")
    mantle = tuple(str(v) for v in _as_list(_context(app, "bedrockMantleResources", ["*"]))) or ("*",)
    raw_prefix = str(_context(app, "rawPrefix", "data/"))
    if raw_prefix and not raw_prefix.endswith("/"):
        raw_prefix += "/"

    return StageConfig(
        stage=stage,
        region=str(_context(app, "region", "us-east-2")),
        account=_context(app, "account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
        owner=str(_context(app, "owner", stage)),
        raw_bucket_name=str(_context(app, "rawBucket")),
        models=models,
        alert_email=alert_email,
        bundle_dependencies=_as_bool(_context(app, "bundle", False)),
        warmup=_as_bool(_context(app, "warmup", False)),
        with_access=_as_bool(_context(app, "withAccess", False)),
        create_budget=create_budget,
        budget_limits_usd=tuple(int(v) for v in _as_list(_context(app, "budgetLimitsUsd", [50, 100, 150]))),
        deployers=tuple(str(v) for v in _as_list(_context(app, "deployers", []))),
        allowed_origins=tuple(str(v) for v in _as_list(_context(app, "allowedOrigins", ["*"]))),
        llm_provider=llm_provider,
        investigator_backend=investigator_backend,
        bedrock_mantle_resources=mantle,
        raw_prefix=raw_prefix,
        serving_import=serving_import,
        pipeline_storage_mb=int(_context(app, "pipelineStorageMb", 4096)),
    )
