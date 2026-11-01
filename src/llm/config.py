"""Configuration of the #8 client: provider, model ID per role, per-role settings and prices.

Everything comes from environment variables (#9) and from ``models.yaml`` / ``prices.yaml``
next to this file; no model ID is written in Python code.
"""

from __future__ import annotations

import functools
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import ProviderUnavailable

PACKAGE_DIR = Path(__file__).resolve().parent
MODELS_FILE = PACKAGE_DIR / "models.yaml"
PRICES_FILE = PACKAGE_DIR / "prices.yaml"
MODEL_ROLES = ("chat", "investigator", "judge")
PROVIDERS = ("mock", "none", "anthropic", "bedrock")
BEDROCK_PREFIX = "anthropic."  # Bedrock model IDs carry this provider prefix
DEFAULT_BACKOFF_SECONDS = 60.0


@functools.lru_cache(maxsize=None)
def load_models() -> dict[str, Any]:
    return yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}


@functools.lru_cache(maxsize=None)
def load_prices() -> dict[str, Any]:
    return yaml.safe_load(PRICES_FILE.read_text(encoding="utf-8")) or {}


def reload() -> None:
    """Forget the cached YAML files (tests, or after editing them in a running process)."""
    for loader in (load_models, load_prices):
        clear = getattr(loader, "cache_clear", None)  # absent if a test replaced the loader
        if callable(clear):
            clear()


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def check_role(model_role: str) -> str:
    if model_role not in MODEL_ROLES:
        raise ValueError(f"unknown model_role {model_role!r}; use one of {', '.join(MODEL_ROLES)}")
    return model_role


def check_provider(name: str | None) -> str:
    value = (name or "").strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER={name!r} no es válido; usa {', '.join(PROVIDERS)}")
    return value


def provider_name(env: Mapping[str, str] | None = None) -> str:
    """``LLM_PROVIDER``: mock | none | anthropic | bedrock (unset = none, like the app port)."""
    return check_provider(_env(env).get("LLM_PROVIDER") or "none")


# ---------------------------------------------------------------- model IDs

def bare_model_id(model_id: str) -> str:
    """First-party form of a model ID: without the Bedrock ``anthropic.`` prefix (and without a
    regional inference-profile prefix such as ``us.`` in front of it)."""
    value = (model_id or "").strip()
    if BEDROCK_PREFIX in value:
        value = value.rsplit(BEDROCK_PREFIX, 1)[1]
    return value


def bedrock_model_id(model_id: str) -> str:
    """Bedrock form: adds the ``anthropic.`` prefix when it is missing; keeps it (and any
    inference-profile prefix) otherwise."""
    value = (model_id or "").strip()
    return value if BEDROCK_PREFIX in value else BEDROCK_PREFIX + value


def _role_config(model_role: str) -> dict[str, Any]:
    return dict(((load_models().get("roles") or {}).get(check_role(model_role))) or {})


def model_id_for(model_role: str, provider: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Model ID of a role for a provider: ``MODEL_<ROLE>`` if set, else the default of
    ``models.yaml``; prefix adjusted to the provider. ``mock`` -> ``mock-<role>``."""
    check_role(model_role)
    values = _env(env)
    name = provider_name(values) if provider is None else check_provider(provider)
    if name == "mock":
        return f"mock-{model_role}"
    if name == "none":
        raise ProviderUnavailable("LLM_PROVIDER=none: no hay modelo configurado")
    env_name = _role_config(model_role).get("model_env") or f"MODEL_{model_role.upper()}"
    configured = (values.get(env_name) or "").strip()
    if not configured:
        defaults = (load_models().get("defaults") or {}).get(name) or {}
        configured = str(defaults.get(model_role) or "").strip()
    if not configured:
        raise ProviderUnavailable(f"sin model ID para el rol {model_role}: {env_name} vacío y sin default en models.yaml")
    return bare_model_id(configured) if name == "anthropic" else bedrock_model_id(configured)


def capabilities(model_id: str) -> dict[str, Any]:
    """Which optional request parameters a model accepts (``effort``, ``thinking``). An unknown
    model gets ``{}``: neither is sent."""
    table = load_models().get("capabilities") or {}
    return dict(table.get(bare_model_id(model_id)) or {})


# ---------------------------------------------------------------- per-role settings

@dataclass(frozen=True)
class RoleSettings:
    max_tokens: int
    timeout_seconds: float
    max_retries: int
    effort: str | None = None
    thinking: dict[str, Any] | None = None


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and math.isfinite(number) else None


def role_settings(model_role: str, env: Mapping[str, str] | None = None) -> RoleSettings:
    """``max_tokens``, ``timeout_seconds`` (per attempt), ``max_retries``, ``effort`` and
    ``thinking`` of a role. ``timeout_env`` (``LLM_TIMEOUT_SECONDS`` for chat) wins when set."""
    raw = _role_config(model_role)
    timeout = _positive_float(raw.get("timeout_seconds")) or 30.0
    if raw.get("timeout_env"):
        timeout = _positive_float(_env(env).get(raw["timeout_env"])) or timeout
    retries = raw.get("max_retries")
    thinking = raw.get("thinking")
    return RoleSettings(
        max_tokens=int(raw.get("max_tokens") or 4096),
        timeout_seconds=timeout,
        max_retries=2 if retries is None else max(0, int(retries)),
        effort=str(raw["effort"]) if raw.get("effort") else None,
        thinking=dict(thinking) if isinstance(thinking, Mapping) and thinking else None,
    )


def bedrock_region(env: Mapping[str, str] | None = None) -> str:
    """``AWS_REGION`` (Lambda always sets it), else ``bedrock.default_region`` of models.yaml."""
    region = (_env(env).get("AWS_REGION") or "").strip()
    if region:
        return region
    return str((load_models().get("bedrock") or {}).get("default_region") or "us-east-2")


def unavailable_backoff_seconds() -> float:
    value = load_models().get("unavailable_backoff_seconds")
    if value is None:
        return DEFAULT_BACKOFF_SECONDS
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return DEFAULT_BACKOFF_SECONDS


# ---------------------------------------------------------------- prices

def price_for(model_id: str) -> dict[str, float] | None:
    """List prices (USD per million tokens) of a model, or None when ``prices.yaml`` lacks it."""
    row = (load_prices().get("models") or {}).get(bare_model_id(model_id))
    if not row:
        return None
    base = float(row["input_usd_per_mtok"])
    return {
        "input": base,
        "output": float(row["output_usd_per_mtok"]),
        "cache_read": float(row.get("cache_read_usd_per_mtok", base * 0.1)),
        "cache_write": float(row.get("cache_write_usd_per_mtok", base * 1.25)),
    }


def estimate_cost(model_id: str, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """USD at list prices (an ESTIMATE, never a bill). ``tokens_in`` are the input tokens billed
    at the base price (not cached); ``cache_read`` / ``cache_write`` are the cached ones (5-minute
    TTL). An unknown model costs 0.0. The Bedrock ``anthropic.`` prefix is ignored."""
    price = price_for(model_id)
    if price is None:
        return 0.0
    usd = (max(0, tokens_in) * price["input"] + max(0, tokens_out) * price["output"]
           + max(0, cache_read) * price["cache_read"] + max(0, cache_write) * price["cache_write"]) / 1e6
    return round(usd, 8)


def estimate_tokens(text: str) -> int:
    """ESTIMATE used by the mock: about 4 characters per token (same rule as the app port)."""
    return max(1, math.ceil(len(text or "") / 4))
