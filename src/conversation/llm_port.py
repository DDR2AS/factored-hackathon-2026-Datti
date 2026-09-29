"""Port to the #8 LLM client (owner: arturo on this side; the package ``src/llm`` is Andrés's).

INTERFACES.md #8, exactly::

    complete(prompt_id, variables, output_schema=None, model_role="chat"|"investigator"|"judge")
        -> {output, usage: {tokens_in, tokens_out, cost_usd}, model_id, prompt_version}

Provider selection by ``LLM_PROVIDER`` (#9):

- ``mock`` (local default in ``scripts/local_api.py``): ``MockLLMClient``, deterministic, answers
  from the YAML fixtures in ``tests/fixtures/llm/<prompt_id>/*.yaml``. The key is the REDACTED
  text, normalized (``normalize_key``), plus the customer's reference date when the fixture
  file sets one. Without a fixture it raises ``NoFixture`` (the orchestrator then uses the
  regex extractor, without degrading). ``model_id`` is always ``"mock-g1"``; ``MODEL_CHAT`` is
  read only to show it (``describe``). ``usage`` is an ESTIMATE: tokens = characters / 4 of the
  rendered prompt and of the output, cost with the per-role prices of ``llm_prices.yaml``;
  ``usage.estimated`` is True.
- ``none`` (or unset): no model; the regex extractor answers every turn.
- ``bedrock``: Andrés's package ``src/llm`` if it exists. Until it does, ``UnavailableLLMClient``
  raises ``ModelUnavailable`` ("pendiente de Andrés") on every call and the orchestrator falls
  back to the regex extractor with ``degraded: ["model_timeout"]``.

No model ID is ever written in this repo: ``model_role`` maps to one in the cloud config
(``MODEL_CHAT`` etc., set by the CDK).

Prompts: #8 says ``prompts/<prompt_id>/<version>.md`` at the repo root, but the Lambda asset
is only ``src/`` (``infra/stacks/common.py``), so a root ``prompts/`` folder would not be
deployed. PROPOSAL to Andrés: prompts live in ``src/conversation/prompts/<prompt_id>/<version>.md``;
``load_prompt`` looks there first and then in the root ``prompts/``.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Protocol

import yaml

from conversation.textnorm import fold

SRC_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_DIR.parent
PROMPT_DIRS = (Path(__file__).resolve().parent / "prompts", REPO_ROOT / "prompts")
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "llm"
PRICES_FILE = Path(__file__).resolve().parent / "llm_prices.yaml"
MODEL_ROLES = ("chat", "investigator", "judge")
PROVIDERS = ("mock", "none", "bedrock")
MOCK_MODEL_ID = "mock-g1"
PENDING_BEDROCK = ("LLM_PROVIDER=bedrock pero src/llm no existe: el cliente #8 está pendiente de "
                   "Andrés; responde el extractor por reglas")

# The customer's text goes to the model between these tags and is declared as DATA. Any copy
# of the tags inside the text is removed first (``as_data``), so the text cannot close the
# block and write "instructions" after it.
DATA_OPEN = "<texto_cliente>"
DATA_CLOSE = "</texto_cliente>"
_DATA_TAG = re.compile(r"<\s*/?\s*texto_cliente\s*>", re.IGNORECASE)


# ---------------------------------------------------------------- errors

class ModelError(Exception):
    """Base of the errors a #8 client may raise. ``prompt_version`` when known."""

    code = "model_error"

    def __init__(self, message: str = "", prompt_version: str | None = None):
        super().__init__(message or self.code)
        self.prompt_version = prompt_version


class ModelTimeout(ModelError):
    code = "model_timeout"


class ModelUnavailable(ModelError):
    code = "model_unavailable"


class NoFixture(ModelError):
    """The mock has no fixture for this prompt and text (not a failure of the model)."""

    code = "no_fixture"


# ---------------------------------------------------------------- the port

class LLMClient(Protocol):
    def complete(self, prompt_id: str, variables: dict, output_schema: dict | None = None,
                 model_role: str = "chat") -> dict:
        """#8: ``{output, usage: {tokens_in, tokens_out, cost_usd}, model_id, prompt_version}``.
        May raise ModelTimeout, ModelUnavailable or NoFixture."""


# ---------------------------------------------------------------- prompts

def _versions(prompt_id: str) -> list[tuple[int, Path]]:
    out = []
    for base in PROMPT_DIRS:
        folder = base / prompt_id
        if folder.is_dir():
            for p in folder.glob("v*.md"):
                m = re.fullmatch(r"v(\d+)", p.stem)
                if m:
                    out.append((int(m.group(1)), p))
            if out:
                return out  # the first folder that has the prompt wins (src/ before the root)
    return out


def load_prompt(prompt_id: str, version: str | None = None) -> tuple[str, str]:
    """(prompt_version, template text without the ``<!-- -->`` header). ``version`` "v1";
    None = the highest version. Looks in ``src/conversation/prompts`` first, then ``prompts/``."""
    if not re.fullmatch(r"[a-z0-9_]+", prompt_id or ""):
        raise ValueError(f"invalid prompt_id {prompt_id!r}")
    found = _versions(prompt_id)
    if version is not None:
        found = [(n, p) for n, p in found if p.stem == version]
    if not found:
        raise FileNotFoundError(f"prompt {prompt_id}/{version or 'v*'}.md not found in {[str(d) for d in PROMPT_DIRS]}")
    n, path = max(found)
    text = re.sub(r"<!--.*?-->\s*", "", path.read_text(encoding="utf-8"), flags=re.S)
    return f"{prompt_id}@v{n}", text


def as_data(text: str) -> str:
    """The customer's text as it goes inside the data block: NFC, the block tags removed."""
    return _DATA_TAG.sub(" ", unicodedata.normalize("NFC", text or "")).strip()


def render_prompt(prompt_id: str, variables: dict, output_schema: dict | None = None,
                  version: str | None = None) -> tuple[str, str]:
    """(prompt_version, rendered prompt). ``{{name}}`` placeholders; ``{{text}}`` goes through
    ``as_data`` and ``{{output_schema}}`` is the JSON schema."""
    prompt_version, template = load_prompt(prompt_id, version)
    values = {k: ("" if v is None else str(v)) for k, v in (variables or {}).items()}
    if "text" in values:
        values["text"] = as_data(values["text"])
    values["output_schema"] = json.dumps(output_schema or {}, ensure_ascii=False, sort_keys=True)

    def sub(m: re.Match) -> str:
        return values.get(m.group(1), "")

    return prompt_version, re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, template)


# ---------------------------------------------------------------- usage estimate

def estimate_tokens(text: str) -> int:
    """ESTIMATE: about 4 characters per token (no tokenizer, no network)."""
    return max(1, math.ceil(len(text or "") / 4))


def load_prices(path: Path = PRICES_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def estimate_cost(model_role: str, tokens_in: int, tokens_out: int, prices: dict | None = None) -> float:
    """USD with the per-role list prices of ``llm_prices.yaml`` (an estimate, never a bill)."""
    prices = prices or load_prices()
    role = (prices.get("roles") or {}).get(model_role)
    if not role:
        return 0.0
    usd = tokens_in * float(role["input_usd_per_mtok"]) / 1e6 + tokens_out * float(role["output_usd_per_mtok"]) / 1e6
    return round(usd, 8)


# ---------------------------------------------------------------- fixtures

def normalize_key(text: str) -> str:
    """Lookup key of a fixture: the redacted text as it goes to the model (``as_data``),
    lowercase without accents, whitespace collapsed."""
    return " ".join(fold(as_data(text)).split())


def load_fixtures(prompt_id: str, directory: Path | None = None) -> dict[tuple[str, str | None], dict]:
    """{(normalized redacted text, reference_date or None): case} from every YAML file of
    ``<directory>/<prompt_id>/``. Each file: ``synthetic: true``, optional ``reference_date``,
    ``language`` and ``cases: [{id, kind, text, output, ...}]``. The text is redacted with the
    same ``redact`` the orchestrator uses before building the key. Duplicate keys are an error."""
    from conversation.redact import redact  # lazy: redact imports nothing from here

    folder = (directory or FIXTURES_DIR) / prompt_id
    out: dict[tuple[str, str | None], dict] = {}
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("synthetic") is not True:
            raise ValueError(f"{path.name}: fixtures must say synthetic: true")
        ref = str(data["reference_date"]) if data.get("reference_date") else None
        for case in data.get("cases") or []:
            language = case.get("language") or data.get("language") or "es"
            key = (normalize_key(redact(case["text"], language)), ref)
            if key in out:
                raise ValueError(f"{path.name}: duplicate fixture for {case.get('id')!r}")
            out[key] = {**case, "language": language, "reference_date": ref, "file": path.name}
    return out


class MockLLMClient:
    """Deterministic #8 client for local runs and tests. No network, no model."""

    def __init__(self, fixtures_dir: Path | None = None, env: dict[str, str] | None = None) -> None:
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR
        env = os.environ if env is None else env
        self.model_chat_configured = env.get("MODEL_CHAT") or None  # shown only, never used
        self.prices = load_prices()
        self._fixtures: dict[str, dict] = {}
        self.calls: list[dict] = []

    def _for(self, prompt_id: str) -> dict:
        if prompt_id not in self._fixtures:
            self._fixtures[prompt_id] = load_fixtures(prompt_id, self.fixtures_dir)
        return self._fixtures[prompt_id]

    def complete(self, prompt_id: str, variables: dict, output_schema: dict | None = None,
                 model_role: str = "chat") -> dict:
        if model_role not in MODEL_ROLES:
            raise ValueError(f"unknown model_role {model_role!r}")
        prompt_version, prompt = render_prompt(prompt_id, variables, output_schema)
        self.calls.append({"prompt_id": prompt_id, "variables": dict(variables), "model_role": model_role})
        text = normalize_key((variables or {}).get("text") or "")
        ref = (variables or {}).get("reference_date")
        fixtures = self._for(prompt_id)
        case = fixtures.get((text, str(ref) if ref else None)) or fixtures.get((text, None))
        if case is None:
            raise NoFixture(f"no fixture for {prompt_id}", prompt_version=prompt_version)
        output = json.loads(json.dumps(case["output"]))  # deep copy, JSON-ready
        tokens_in = estimate_tokens(prompt)
        tokens_out = estimate_tokens(json.dumps(output, ensure_ascii=False))
        return {
            "output": output,
            "usage": {"tokens_in": tokens_in, "tokens_out": tokens_out,
                      "cost_usd": estimate_cost(model_role, tokens_in, tokens_out, self.prices),
                      "estimated": True},
            "model_id": MOCK_MODEL_ID,
            "prompt_version": prompt_version,
        }


class UnavailableLLMClient:
    """Stands for a provider that cannot answer (``bedrock`` without ``src/llm``)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def complete(self, prompt_id: str, variables: dict, output_schema: dict | None = None,
                 model_role: str = "chat") -> dict:
        raise ModelUnavailable(self.reason)


class PackageLLMClient:
    """Andrés's ``src/llm`` package behind the port (it already has the #8 signature)."""

    def __init__(self, module: Any) -> None:
        self.module = module

    def complete(self, prompt_id: str, variables: dict, output_schema: dict | None = None,
                 model_role: str = "chat") -> dict:
        try:
            return self.module.complete(prompt_id, variables, output_schema=output_schema, model_role=model_role)
        except TimeoutError as e:
            raise ModelTimeout(str(e)) from e


def _andres_package() -> Any | None:
    """``src/llm`` only (never another installed package called ``llm``)."""
    if not (SRC_DIR / "llm" / "__init__.py").exists():
        return None
    import importlib

    module = importlib.import_module("llm")
    origin = Path(getattr(module, "__file__", "") or "").resolve()
    if SRC_DIR / "llm" not in origin.parents or not callable(getattr(module, "complete", None)):
        return None
    return module


def provider(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = (env.get("LLM_PROVIDER") or "none").strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER={value!r} no es válido; usa {', '.join(PROVIDERS)}")
    return value


def load_llm_client(env: dict[str, str] | None = None) -> LLMClient | None:
    """The client for ``LLM_PROVIDER``: mock, None (``none`` or unset), or bedrock (Andrés's
    package, or an always-unavailable client while it does not exist)."""
    env = os.environ if env is None else env
    name = provider(env)
    if name == "none":
        return None
    if name == "mock":
        return MockLLMClient(env=env)
    try:
        module = _andres_package()
    except Exception as e:  # a broken package is reported, never a crash at start
        return UnavailableLLMClient(f"src/llm no se pudo importar ({type(e).__name__}); responde el extractor por reglas")
    return PackageLLMClient(module) if module is not None else UnavailableLLMClient(PENDING_BEDROCK)


def describe(env: dict[str, str] | None = None) -> str:
    """One line for the local server's start message."""
    env = os.environ if env is None else env
    name = provider(env)
    shown = env.get("MODEL_CHAT") or "sin definir"
    if name == "none":
        return "G1: sin modelo (LLM_PROVIDER=none): extractor por reglas en cada turno"
    if name == "mock":
        count = len(load_fixtures("g1_extract"))
        return (f"G1: mock determinista (model_id {MOCK_MODEL_ID}, {count} fixtures YAML; sin fixture responde "
                f"el extractor por reglas; costo estimado). MODEL_CHAT de la nube: {shown} (solo informativo)")
    client = load_llm_client(env)
    if isinstance(client, UnavailableLLMClient):
        return f"G1: {client.reason}"
    return f"G1: paquete src/llm (bedrock), MODEL_CHAT={shown}"
