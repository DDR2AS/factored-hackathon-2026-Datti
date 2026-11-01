"""Gateway tools (INTERFACES.md #2): the only way code reaches customer data, scoped by session.

Backends, chosen by ``GATEWAY_BACKEND`` (INTERFACES.md #9):

- ``demo``      the six judge-mode customers (``conversation/demo_gateway.py``, synthetic YAML).
- ``duckdb``    the gold tables of the pipeline (``gateway/duckdb_backend.py``).
- ``composite`` demo customers from the demo store, dataset customers from gold (default when the
                gold tables exist).
- ``dynamodb``  the cloud serving table ``TABLE_SERVING`` (``cloud/dynamodb_gateway.py``: the same tools
                over the items written by ``pipeline/serving.py``).

``composite`` takes its dataset customers from ``GATEWAY_DATA_BACKEND``: ``duckdb`` (default, gold
Parquet) or ``dynamodb`` (the serving table; what the CDK sets in AWS).

Every backend exposes the same tool functions, error classes and helpers, so callers never know
which one answered.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from gateway.types import (  # noqa: F401 (public API)
    Baseline, Card, CardStatus, Contact, CustomerProfile, DigitalSession, FeeItem, FeeRule, GatewayContext,
    GatewayError, MerchantStats, NotConfirmed, NotFound, NotOwned, RiskEvidence, ToolUnavailable, Txn,
    fee_item_for_txn,
)

BACKENDS = ("demo", "duckdb", "composite", "dynamodb")
DATA_BACKENDS = ("duckdb", "dynamodb")
_cache: dict[tuple[str, str], Any] = {}


def gold_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    data_dir = env.get("DATA_DIR") or str(Path(__file__).resolve().parents[2] / "data")
    return Path(data_dir) / "gold"


def backend_name(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    name = (env.get("GATEWAY_BACKEND") or "demo").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"GATEWAY_BACKEND={name!r} is not valid; use {', '.join(BACKENDS)}")
    return name


def data_backend_name(env: dict[str, str] | None = None) -> str:
    """Where ``composite`` reads dataset customers: ``GATEWAY_DATA_BACKEND`` (duckdb | dynamodb)."""
    env = os.environ if env is None else env
    name = (env.get("GATEWAY_DATA_BACKEND") or "duckdb").strip().lower()
    if name not in DATA_BACKENDS:
        raise ValueError(f"GATEWAY_DATA_BACKEND={name!r} is not valid; use {', '.join(DATA_BACKENDS)}")
    return name


def make_gateway(env: dict[str, str] | None = None) -> Any:
    """The gateway for ``GATEWAY_BACKEND`` (cached per process: gold is loaded once)."""
    from conversation import demo_gateway  # the demo store lives with the conversation package

    name = backend_name(env)
    if name == "demo":
        return demo_gateway
    if name == "dynamodb" or (name == "composite" and data_backend_name(env) == "dynamodb"):
        return _dynamodb(name, env, demo_gateway)
    key = (name, str(gold_dir(env)))
    if key not in _cache:
        from gateway.duckdb_backend import DuckDBGateway

        duck = DuckDBGateway(gold_dir(env))
        _cache[key] = duck if name == "duckdb" else _composite(demo_gateway, duck)
    return _cache[key]


def _composite(demo_module: Any, duck: Any) -> Any:
    from gateway.composite import CompositeGateway

    return CompositeGateway(demo_module, duck)


def _dynamodb(name: str, env: dict[str, str] | None, demo_module: Any) -> Any:
    from cloud.dynamodb_gateway import DynamoDBGateway  # boto3 only in the cloud

    env = os.environ if env is None else env
    key = (name, "dynamodb:" + (env.get("TABLE_SERVING") or ""))
    if key not in _cache:
        dyn = DynamoDBGateway.from_env(env)
        _cache[key] = dyn if name == "dynamodb" else _composite(demo_module, dyn)
    return _cache[key]
