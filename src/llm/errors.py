"""Errors of the #8 LLM client. Callers branch on the class, never on the message text.

Timeouts are the builtin ``TimeoutError`` (the app port maps it to ``model_timeout``).
"""

from __future__ import annotations


class LLMError(Exception):
    """Base of the errors this package raises. ``prompt_version`` when known."""

    code = "llm_error"

    def __init__(self, message: str = "", prompt_version: str | None = None) -> None:
        super().__init__(message or self.code)
        self.prompt_version = prompt_version


class ProviderUnavailable(LLMError):
    """No model can answer: ``LLM_PROVIDER=none``, SDK missing, no credentials, connection
    error, 401/403, model not found or not enabled, or 429/5xx after the SDK's retries."""

    code = "model_unavailable"


class ModelError(LLMError):
    """The provider answered but the result cannot be used: refusal, output truncated by
    ``max_tokens``, unparseable or non-object JSON, or a 4xx bad request."""

    code = "model_error"

    def __init__(self, message: str = "", prompt_version: str | None = None, status: int | None = None) -> None:
        super().__init__(message, prompt_version)
        self.status = status  # HTTP status when the provider rejected the request


class NoFixture(ModelError):
    """Mock provider: there is no fixture for this prompt and text (not a model failure)."""

    code = "no_fixture"
