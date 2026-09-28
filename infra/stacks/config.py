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
    )
