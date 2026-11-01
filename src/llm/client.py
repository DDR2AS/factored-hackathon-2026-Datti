"""Calls to the model (#8): one SDK client per provider and role, one Messages API request per
call, normalized results and errors.

Providers (``LLM_PROVIDER``), with the same request code for both real ones:

- ``anthropic``: ``anthropic.Anthropic()``. The SDK resolves the credentials
  (``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN`` or an ``ant auth login`` profile); nothing
  here reads, stores or logs a key.
- ``bedrock``: ``anthropic.AnthropicBedrockMantle(aws_region=AWS_REGION or models.yaml)``, the
  Messages-API endpoint of Amazon Bedrock, with the standard AWS credential chain.
- ``mock``: ``complete`` answers from the YAML fixtures (``conversation.llm_port.MockLLMClient``,
  so G1 behaves exactly as with the app's own mock); ``converse`` asks the caller's
  ``mock_policy``.
- ``none``: no model; every call raises ``ProviderUnavailable``.

Errors: builtin ``TimeoutError`` (SDK timeout), ``ProviderUnavailable`` (no SDK or credentials,
connection error, 401/403/404, 429/5xx after the SDK's retries) and ``ModelError`` (refusal,
truncated output, unusable JSON, 4xx bad request). Never ``temperature``/``top_p``/``top_k``.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import config, prompts
from .errors import LLMError, ModelError, NoFixture, ProviderUnavailable

log = logging.getLogger("llm")

# JSON through a tool, only when the model or platform rejects ``output_config.format``.
OUTPUT_TOOL = "entregar_resultado"
_OUTPUT_TOOL_DESCRIPTION = ("Entrega el resultado final. Llámala una sola vez, con el objeto JSON completo "
                            "como argumentos.")
_OUTPUT_TOOL_INSTRUCTION = (f"Entrega tu respuesta llamando una sola vez a la herramienta {OUTPUT_TOOL} con el "
                            "objeto JSON como argumentos; no escribas el JSON como texto.")
_EPHEMERAL = {"type": "ephemeral"}
_MAX_BREAKPOINTS = 4  # cache_control breakpoints the API accepts per request
_TRUNCATED = ("max_tokens", "model_context_window_exceeded")
# JSON output modes, in order: structured outputs; a strict tool; a plain tool (when the schema
# is too complex for the strict grammar). The next one is tried only after an HTTP 400.
_NEXT_JSON_MODE = {"format": "tool", "tool": "tool_plain"}

_LOCK = threading.RLock()
_CLIENTS: dict[tuple, Any] = {}
_BLOCKED: dict[str, tuple[float, str]] = {}  # provider -> (monotonic deadline, reason)
_JSON_MODE: dict[str, str] = {}  # model ID -> the JSON mode that worked after a 400 ("tool" | "tool_plain")
_MOCK: list[Any] = []  # the shared MockLLMClient, built on first use


def _now() -> float:
    return time.monotonic()


def reset_clients() -> None:
    """Close and forget the cached SDK clients, the provider back-off, the JSON-mode memory, the
    mock and the cached YAML config (tests, or after changing the environment of a process)."""
    with _LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
        _BLOCKED.clear()
        _JSON_MODE.clear()
        _MOCK.clear()
    for client in clients:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # closing is best effort
                pass
    config.reload()


# ---------------------------------------------------------------- SDK clients

def _sdk() -> Any:
    try:
        import anthropic
    except ImportError as e:
        raise ProviderUnavailable("falta el SDK de Anthropic (pip install 'anthropic[bedrock]')") from e
    return anthropic


def warm_up(provider: str | None = None) -> bool:
    """Import the SDK (and boto3 for Bedrock's SigV4) ahead of the first call, so that cost (a
    second or more on a cold start) is not paid inside the first request's timeout. Builds no
    client, reads no credentials, opens no connection. False if there is nothing to warm."""
    name = _provider(provider)
    if name not in ("anthropic", "bedrock"):
        return False
    try:
        _sdk()
        if name == "bedrock":  # what the SDK imports lazily to sign with SigV4
            importlib.import_module("boto3")
            importlib.import_module("botocore.auth")
    except (LLMError, ImportError):
        return False  # the first call raises ProviderUnavailable with the reason
    return True


def make_client(provider: str, *, timeout: float, max_retries: int, region: str | None = None,
                http_client: Any = None) -> Any:
    """A new SDK client for ``provider`` ("anthropic" | "bedrock"); ``ProviderUnavailable`` when it
    cannot be built (SDK missing, no Anthropic credentials). Tests replace this function
    (``monkeypatch.setattr(llm.client, "make_client", ...)``) or pass ``http_client``."""
    sdk = _sdk()
    extra = {} if http_client is None else {"http_client": http_client}
    if provider == "anthropic":
        try:
            client = sdk.Anthropic(timeout=timeout, max_retries=max_retries, **extra)
        except (sdk.AnthropicError, ValueError) as e:
            raise ProviderUnavailable(f"anthropic: credenciales no válidas ({type(e).__name__})") from e
        if (getattr(client, "api_key", None) is None and getattr(client, "auth_token", None) is None
                and getattr(client, "credentials", None) is None):
            raise ProviderUnavailable("anthropic: sin credenciales (ANTHROPIC_API_KEY o un perfil de `ant auth login`)")
        return client
    if provider == "bedrock":
        try:
            return sdk.AnthropicBedrockMantle(aws_region=region, timeout=timeout, max_retries=max_retries, **extra)
        except (sdk.AnthropicError, ValueError) as e:
            raise ProviderUnavailable(f"bedrock: cliente no configurado ({type(e).__name__})") from e
    raise ProviderUnavailable(f"el proveedor {provider!r} no tiene cliente")


def _block(provider: str, reason: str) -> None:
    """After a credentials or permission failure, fail fast for ``unavailable_backoff_seconds``."""
    seconds = config.unavailable_backoff_seconds()
    if seconds > 0:
        with _LOCK:
            _BLOCKED[provider] = (_now() + seconds, reason)


def _get_client(provider: str, model_role: str, settings: config.RoleSettings) -> Any:
    """The cached client of (provider, role), built on first use. Thread-safe."""
    region = config.bedrock_region() if provider == "bedrock" else None
    key = (provider, model_role, region, settings.timeout_seconds, settings.max_retries)
    with _LOCK:
        blocked = _BLOCKED.get(provider)
        if blocked is not None:
            if _now() < blocked[0]:
                raise ProviderUnavailable(blocked[1])
            del _BLOCKED[provider]
        client = _CLIENTS.get(key)
        if client is None:
            try:
                client = make_client(provider, timeout=settings.timeout_seconds,
                                     max_retries=settings.max_retries, region=region)
            except ProviderUnavailable as e:
                _block(provider, str(e))
                raise
            _CLIENTS[key] = client
        return client


def _is_botocore_error(e: BaseException) -> bool:
    try:
        from botocore.exceptions import BotoCoreError
    except ImportError:
        return False
    return isinstance(e, BotoCoreError)


def _is_missing_credentials(e: BaseException) -> bool:
    """Errors the SDK raises before sending when it has no credentials: TypeError "Could not
    resolve authentication method" (Anthropic), RuntimeError "Could not resolve AWS credentials"
    or ImportError of boto3 (SigV4 signing for Bedrock)."""
    if isinstance(e, ImportError):
        return True
    text = str(e).lower()
    return ("authentication" in text) if isinstance(e, TypeError) else ("credentials" in text)


def _call(client: Any, request: dict[str, Any], provider: str) -> Any:
    """One ``messages.create`` with the SDK errors mapped to this package's errors."""
    sdk = _sdk()
    model = request.get("model")
    try:
        return client.messages.create(**request)
    except sdk.APITimeoutError as e:
        raise TimeoutError(f"{provider}: {model} no respondió en {request.get('timeout')} s") from e
    except sdk.APIConnectionError as e:
        raise ProviderUnavailable(f"{provider}: error de conexión ({type(e).__name__})") from e
    except (sdk.AuthenticationError, sdk.PermissionDeniedError) as e:
        reason = f"{provider}: credenciales rechazadas o sin permiso para {model} (HTTP {e.status_code})"
        _block(provider, reason)
        raise ProviderUnavailable(reason) from e
    except sdk.NotFoundError as e:
        raise ProviderUnavailable(f"{provider}: el modelo {model} no existe o no está habilitado (HTTP 404)") from e
    except sdk.APIStatusError as e:
        status = int(getattr(e, "status_code", 0) or 0)
        if status == 429 or status >= 500 or status in (402, 408, 409):
            raise ProviderUnavailable(f"{provider}: no disponible (HTTP {status}) después de los reintentos") from e
        kind = getattr(e, "type", None) or "error"
        raise ModelError(f"{provider}: solicitud rechazada (HTTP {status}, {kind})", status=status) from e
    except sdk.APIResponseValidationError as e:
        raise ModelError(f"{provider}: respuesta con forma inesperada") from e
    except sdk.AnthropicError as e:  # CredentialsError, WorkloadIdentityError, ...
        reason = f"{provider}: credenciales no disponibles ({type(e).__name__})"
        _block(provider, reason)
        raise ProviderUnavailable(reason) from e
    except (TypeError, RuntimeError, ImportError) as e:
        if not _is_missing_credentials(e):
            raise
        reason = f"{provider}: sin credenciales ({type(e).__name__})"
        _block(provider, reason)
        raise ProviderUnavailable(reason) from e
    except Exception as e:
        if not _is_botocore_error(e):
            raise
        reason = f"{provider}: credenciales de AWS no disponibles ({type(e).__name__})"
        _block(provider, reason)
        raise ProviderUnavailable(reason) from e


# ---------------------------------------------------------------- requests

def _api_schema(output_schema: Mapping[str, Any]) -> dict[str, Any]:
    """The schema as structured outputs accept it: the SDK's ``transform_schema``
    (``additionalProperties: false`` on every object; unsupported keywords moved into the
    descriptions). The schema as given when it cannot be transformed."""
    try:
        import anthropic

        return anthropic.transform_schema(dict(output_schema))
    except Exception as e:  # ImportError, or a shape transform_schema does not handle
        log.debug("llm: transform_schema failed (%s); sending the schema as given", type(e).__name__)
        return dict(output_schema)


def _model_options(settings: config.RoleSettings, caps: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(output_config with ``effort`` if set and accepted, ``thinking`` if set and accepted)."""
    output_config: dict[str, Any] = {}
    if settings.effort and caps.get("effort"):
        output_config["effort"] = settings.effort
    thinking = dict(settings.thinking) if settings.thinking and caps.get("thinking") else None
    return output_config, thinking


def _complete_request(model_id: str, settings: config.RoleSettings, caps: Mapping[str, Any], system: str | None,
                      user: str, output_schema: Mapping[str, Any] | None, mode: str) -> dict[str, Any]:
    """``mode``: "text" (no schema), "format" (``output_config.format``), "tool" (strict tool) or
    "tool_plain" (same tool without ``strict``). Tools go with ``tool_choice`` auto plus an
    instruction: forced tool use is a 400 on the newest models."""
    output_config, thinking = _model_options(settings, caps)
    request: dict[str, Any] = {"model": model_id, "max_tokens": settings.max_tokens,
                               "timeout": settings.timeout_seconds}
    if system:
        request["system"] = [{"type": "text", "text": system, "cache_control": dict(_EPHEMERAL)}]
    content: Any = user
    if mode == "format":
        output_config["format"] = {"type": "json_schema", "schema": _api_schema(output_schema or {})}
    elif mode in ("tool", "tool_plain"):
        tool = {"name": OUTPUT_TOOL, "description": _OUTPUT_TOOL_DESCRIPTION,
                "input_schema": _api_schema(output_schema or {})}
        if mode == "tool":
            tool["strict"] = True
        request["tools"] = [tool]
        request["tool_choice"] = {"type": "auto"}
        content = [{"type": "text", "text": user}, {"type": "text", "text": _OUTPUT_TOOL_INSTRUCTION}]
    request["messages"] = [{"role": "user", "content": content}]
    if output_config:
        request["output_config"] = output_config
    if thinking:
        request["thinking"] = thinking
    return request


def _system_blocks(system: str | Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if system is None:
        return []
    if isinstance(system, str):
        return [{"type": "text", "text": system}] if system.strip() else []
    return [dict(block) for block in system]


def _count_breakpoints(value: Any) -> int:
    if isinstance(value, Mapping):
        return int("cache_control" in value) + sum(_count_breakpoints(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_count_breakpoints(v) for v in value)
    return 0


def _mark_last_message(messages: list[Any]) -> list[Any]:
    """A copy of ``messages`` whose last content block carries a cache breakpoint (the caller's
    list and dicts are never modified)."""
    last = messages[-1]
    if not isinstance(last, Mapping):
        return messages
    content = last.get("content")
    if isinstance(content, str):
        if not content.strip():
            return messages
        new_content: list[Any] = [{"type": "text", "text": content, "cache_control": dict(_EPHEMERAL)}]
    elif isinstance(content, list) and content and isinstance(content[-1], Mapping):
        block = content[-1]
        if block.get("type") in ("thinking", "redacted_thinking") or "cache_control" in block:
            return messages
        new_content = [*content[:-1], {**block, "cache_control": dict(_EPHEMERAL)}]
    else:
        return messages
    return [*messages[:-1], {**last, "content": new_content}]


def _converse_request(model_id: str, settings: config.RoleSettings, caps: Mapping[str, Any],
                      system: str | Sequence[Mapping[str, Any]] | None, messages: Sequence[Any],
                      tools: Sequence[Mapping[str, Any]] | None, max_tokens: int | None,
                      timeout: float | None) -> dict[str, Any]:
    """Prompt caching: one breakpoint at the end of the stable prefix (tools render before
    system: the last system block, or the last tool without a system prompt) and one on the last
    block of the last message, so each turn of the loop reads the previous turns from the cache.
    Never more than the API's 4 breakpoints, counting the caller's own."""
    output_config, thinking = _model_options(settings, caps)
    system_list = _system_blocks(system)
    tool_list = [dict(tool) for tool in (tools or [])]
    message_list = list(messages)
    budget = _MAX_BREAKPOINTS - _count_breakpoints([system_list, tool_list, message_list])
    prefix_end = system_list[-1] if system_list else (tool_list[-1] if tool_list else None)
    if budget > 0 and prefix_end is not None and "cache_control" not in prefix_end:
        prefix_end["cache_control"] = dict(_EPHEMERAL)
        budget -= 1
    if budget > 0:
        message_list = _mark_last_message(message_list)
    request: dict[str, Any] = {"model": model_id, "max_tokens": int(max_tokens or settings.max_tokens),
                               "timeout": float(timeout or settings.timeout_seconds), "messages": message_list}
    if system_list:
        request["system"] = system_list
    if tool_list:
        request["tools"] = tool_list
    if output_config:
        request["output_config"] = output_config
    if thinking:
        request["thinking"] = thinking
    return request


# ---------------------------------------------------------------- responses

def _stop_problem(response: Any) -> str | None:
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details is not None else None
        return f"el modelo se negó a responder (refusal{': ' + str(category) if category else ''})"
    if stop in _TRUNCATED:
        return f"respuesta truncada ({stop}); sube max_tokens del rol en src/llm/models.yaml"
    if stop == "pause_turn":
        return "pause_turn inesperado: este cliente no usa herramientas de servidor"
    return None


def _text_of(response: Any) -> str:
    return "".join(getattr(b, "text", "") or "" for b in (getattr(response, "content", None) or [])
                   if getattr(b, "type", None) == "text")


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in ``text`` (the whole text, a fenced block, or the outermost braces);
    ``ModelError`` when there is none."""
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    not_object = False
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
        not_object = True
    raise ModelError("la salida JSON del modelo no es un objeto" if not_object else "la salida del modelo no es JSON válido")


def _output(response: Any, mode: str) -> dict[str, Any] | str:
    problem = _stop_problem(response)
    if problem:
        raise ModelError(problem)
    if mode in ("tool", "tool_plain"):
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == OUTPUT_TOOL:
                value = getattr(block, "input", None)
                if isinstance(value, Mapping):
                    return dict(value)
                raise ModelError("la herramienta de salida no recibió un objeto")
    text = _text_of(response)
    if mode == "text":
        if not text.strip():
            raise ModelError("el modelo no devolvió texto")
        return text
    return parse_json_object(text)


def _usage(response: Any, model_id: str, *, estimated: bool) -> dict[str, Any]:
    """``tokens_in`` counts every input token (uncached + cache reads + cache writes); the cost
    prices each kind separately. ``estimated`` for Bedrock (it bills with its own list) and
    for a model without a price in prices.yaml."""
    usage = getattr(response, "usage", None)

    def count(name: str) -> int:
        try:
            return max(0, int(getattr(usage, name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    tokens_in, tokens_out = count("input_tokens"), count("output_tokens")
    cache_read, cache_write = count("cache_read_input_tokens"), count("cache_creation_input_tokens")
    return {
        "tokens_in": tokens_in + cache_read + cache_write,
        "tokens_out": tokens_out,
        "cost_usd": config.estimate_cost(model_id, tokens_in, tokens_out, cache_read=cache_read, cache_write=cache_write),
        "estimated": bool(estimated or config.price_for(model_id) is None),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }


def _block_dict(block: Any) -> dict[str, Any]:
    """A response content block as a plain dict that can go back in the next request unchanged
    (thinking blocks keep their signature)."""
    kind = getattr(block, "type", None)
    if kind == "text":
        return {"type": "text", "text": block.text}
    if kind == "tool_use":
        value = getattr(block, "input", None)
        return {"type": "tool_use", "id": block.id, "name": block.name,
                "input": dict(value) if isinstance(value, Mapping) else {}}
    if kind == "thinking":
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if kind == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    return dict(block) if isinstance(block, Mapping) else {"type": str(kind)}


def _stop_details(response: Any) -> dict[str, Any] | None:
    if getattr(response, "stop_reason", None) != "refusal":
        return None
    details = getattr(response, "stop_details", None)
    if details is None:
        return None
    return {"type": "refusal", "category": getattr(details, "category", None),
            "explanation": getattr(details, "explanation", None)}


def _dump(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------- public calls

def _provider(provider: str | None) -> str:
    return config.provider_name() if provider is None else config.check_provider(provider)


def complete(prompt_id: str, variables: Mapping[str, Any] | None, output_schema: Mapping[str, Any] | None = None,
             model_role: str = "chat", *, provider: str | None = None) -> dict[str, Any]:
    """INTERFACES.md #8: render the prompt ``prompt_id`` with ``variables`` and ask the model of
    ``model_role`` (chat | investigator | judge).

    Returns ``{"output", "usage": {"tokens_in", "tokens_out", "cost_usd", "estimated",
    "cache_read_tokens", "cache_write_tokens"}, "model_id", "prompt_version"}``. With
    ``output_schema`` the output is the parsed JSON object (structured outputs; after a 400, a
    strict tool, then a plain tool), else the text. ``provider`` overrides ``LLM_PROVIDER`` (the app port
    passes the one it selected). Raises ``TimeoutError``, ``ProviderUnavailable``, ``ModelError``
    or (mock) ``NoFixture``; ``FileNotFoundError``/``ValueError`` for a bad prompt or role.
    """
    config.check_role(model_role)
    name = _provider(provider)
    if name == "none":
        raise ProviderUnavailable("LLM_PROVIDER=none: no hay modelo configurado")
    if name == "mock":
        return _mock_complete(prompt_id, variables, output_schema, model_role)
    prompt_version, system, user = prompts.render_parts(prompt_id, variables, output_schema)
    try:
        return _complete_real(name, model_role, prompt_version, system, user, output_schema)
    except LLMError as e:
        if e.prompt_version is None:
            e.prompt_version = prompt_version
        raise


def _complete_real(name: str, model_role: str, prompt_version: str, system: str | None, user: str,
                   output_schema: Mapping[str, Any] | None) -> dict[str, Any]:
    settings = config.role_settings(model_role)
    model_id = config.model_id_for(model_role, name)
    caps = config.capabilities(model_id)
    client = _get_client(name, model_role, settings)
    mode = "text" if output_schema is None else _JSON_MODE.get(model_id, "format")
    while True:
        try:
            response = _call(client, _complete_request(model_id, settings, caps, system, user, output_schema, mode),
                             name)
            break
        except ModelError as e:
            # A 400 to a JSON mode (the model, the platform or the schema's complexity): try
            # the next mode, and remember the one that is accepted for this model.
            following = _NEXT_JSON_MODE.get(mode) if e.status == 400 else None
            if following is None:
                raise
            log.warning("llm: %s rejected JSON mode %s (HTTP 400); retrying with %s", model_id, mode, following)
            mode = following
    if mode in ("tool", "tool_plain"):
        _JSON_MODE[model_id] = mode
    return {"output": _output(response, mode), "usage": _usage(response, model_id, estimated=(name == "bedrock")),
            "model_id": model_id, "prompt_version": prompt_version}


def _mock_complete(prompt_id: str, variables: Mapping[str, Any] | None, output_schema: Mapping[str, Any] | None,
                   model_role: str) -> dict[str, Any]:
    """The app's own deterministic mock (YAML fixtures), so G1 gets exactly the same answers."""
    from conversation import llm_port  # lazy: the app port imports this package lazily too

    with _LOCK:
        if not _MOCK:
            mock = llm_port.MockLLMClient()
            mock.calls = deque(maxlen=50)  # inspection only; bounded in a long-lived process
            _MOCK.append(mock)
        mock = _MOCK[0]
    try:
        return mock.complete(prompt_id, dict(variables or {}), output_schema=output_schema, model_role=model_role)
    except llm_port.NoFixture as e:
        raise NoFixture(str(e), prompt_version=e.prompt_version) from e


def converse(system: str | Sequence[Mapping[str, Any]] | None, messages: Sequence[Mapping[str, Any]],
             tools: Sequence[Mapping[str, Any]] | None, model_role: str = "investigator",
             max_tokens: int | None = None, timeout: float | None = None,
             mock_policy: Callable[..., Mapping[str, Any]] | None = None, *,
             provider: str | None = None) -> dict[str, Any]:
    """One Messages API turn with client-side tools (``{name, description, input_schema}``,
    optionally ``strict``), for the caller's own agent loop (G2).

    Returns ``{"content": [{"type": "text", "text"} | {"type": "tool_use", "id", "name", "input"}
    | {"type": "thinking", ...} | {"type": "redacted_thinking", ...}], "stop_reason", "usage",
    "model_id"}`` plus ``stop_details`` on a refusal. Append ``content`` unchanged as the
    assistant turn (thinking blocks must go back as they came). The loop decides what to do with
    ``stop_reason`` (``tool_use``, ``end_turn``, ``max_tokens``, ``refusal``). The system prompt and
    tools are cached, and so is the conversation so far. ``max_tokens``/``timeout`` default to
    the role's settings. With ``LLM_PROVIDER=mock`` the answer is ``mock_policy(system, messages,
    tools)`` -> ``{content, stop_reason}`` (``ProviderUnavailable`` without a policy).
    """
    config.check_role(model_role)
    if not messages:
        raise ValueError("converse needs at least one message")
    name = _provider(provider)
    if name == "none":
        raise ProviderUnavailable("LLM_PROVIDER=none: no hay modelo configurado")
    if name == "mock":
        return _mock_converse(system, messages, tools, model_role, mock_policy)
    settings = config.role_settings(model_role)
    model_id = config.model_id_for(model_role, name)
    caps = config.capabilities(model_id)
    client = _get_client(name, model_role, settings)
    request = _converse_request(model_id, settings, caps, system, messages, tools, max_tokens, timeout)
    response = _call(client, request, name)
    out: dict[str, Any] = {
        "content": [_block_dict(b) for b in (getattr(response, "content", None) or [])],
        "stop_reason": getattr(response, "stop_reason", None),
        "usage": _usage(response, model_id, estimated=(name == "bedrock")),
        "model_id": model_id,
    }
    details = _stop_details(response)
    if details is not None:
        out["stop_details"] = details
    return out


def _mock_converse(system: Any, messages: Sequence[Any], tools: Any, model_role: str,
                   mock_policy: Callable[..., Mapping[str, Any]] | None) -> dict[str, Any]:
    if mock_policy is None:
        raise ProviderUnavailable("LLM_PROVIDER=mock: converse necesita mock_policy (no hay fixtures de conversaciones)")
    answer = mock_policy(system, messages, tools)
    if (not isinstance(answer, Mapping) or not isinstance(answer.get("content"), list)
            or not isinstance(answer.get("stop_reason"), str)):
        raise ModelError("mock_policy debe devolver {content: [...], stop_reason: str}")
    content = json.loads(json.dumps(answer["content"], ensure_ascii=False, default=str))  # deep copy, JSON-ready
    tokens_in = config.estimate_tokens(_dump(system) + _dump(list(messages)) + _dump(tools))
    tokens_out = config.estimate_tokens(_dump(content))
    priced_as = config.model_id_for(model_role, "anthropic")  # the role's real model, for the estimate
    return {
        "content": content,
        "stop_reason": answer["stop_reason"],
        "usage": {"tokens_in": tokens_in, "tokens_out": tokens_out,
                  "cost_usd": config.estimate_cost(priced_as, tokens_in, tokens_out), "estimated": True},
        "model_id": f"mock-{model_role}",
    }
