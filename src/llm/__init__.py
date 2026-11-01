"""#8 LLM client (INTERFACES.md #8): the only way application code calls a model.

    complete(prompt_id, variables, output_schema=None, model_role="chat")
        -> {output, usage: {tokens_in, tokens_out, cost_usd, estimated, ...}, model_id, prompt_version}
    converse(system, messages, tools, model_role="investigator", max_tokens=None, timeout=None,
             mock_policy=None)
        -> {content: [text | tool_use | thinking blocks], stop_reason, usage, model_id}

Provider by ``LLM_PROVIDER`` (mock | none | anthropic | bedrock); model ID by role from
``MODEL_CHAT`` / ``MODEL_INVESTIGATOR`` / ``MODEL_JUDGE`` or the defaults of ``models.yaml``
(D5). Callers never pass a model ID or a raw prompt: prompts are files
(``src/prompts/<prompt_id>/v<n>.md``, see ``llm.prompts``). Errors: ``TimeoutError``,
``ProviderUnavailable``, ``ModelError`` (``NoFixture`` with the mock). Guide:
``docs/llm_client.md``.
"""

from __future__ import annotations

from .client import OUTPUT_TOOL, complete, converse, parse_json_object, reset_clients, warm_up
from .config import (
    MODEL_ROLES,
    PROVIDERS,
    capabilities,
    estimate_cost,
    model_id_for,
    provider_name,
    role_settings,
)
from .errors import LLMError, ModelError, NoFixture, ProviderUnavailable
from .prompts import as_data, load_prompt, render_parts, render_prompt

__all__ = [
    "MODEL_ROLES",
    "OUTPUT_TOOL",
    "PROVIDERS",
    "LLMError",
    "ModelError",
    "NoFixture",
    "ProviderUnavailable",
    "as_data",
    "capabilities",
    "complete",
    "converse",
    "estimate_cost",
    "load_prompt",
    "model_id_for",
    "parse_json_object",
    "provider_name",
    "render_parts",
    "render_prompt",
    "reset_clients",
    "role_settings",
    "warm_up",
]
