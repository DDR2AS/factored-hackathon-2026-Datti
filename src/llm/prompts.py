"""Prompt files of the #8 client, with the same conventions as ``conversation/llm_port``.

- ``<dir>/<prompt_id>/v<n>.md``; the highest ``n`` by default; ``prompt_version`` is
  ``<prompt_id>@v<n>``. Folders, in order (the first that has the prompt wins):
  ``src/prompts/``, ``src/conversation/prompts/``, then the repo-root ``prompts/`` (not
  deployed: the Lambda asset is only ``src/``).
- Every ``<!-- ... -->`` comment (the header) is removed before sending.
- ``{{name}}`` placeholders take ``str(value)`` (missing -> ""). ``{{text}}`` goes through
  ``as_data`` (NFC, copies of the ``<texto_cliente>`` tags removed) because the customer's text
  is sent inside that data block. ``{{output_schema}}`` is the JSON schema (sorted keys).
- Optional split: a line ``<!-- system -->`` and a line ``<!-- user -->``. The text between them
  is the system prompt (cached by ``complete``), the text after ``<!-- user -->`` is the user
  message. Without markers the whole file is the user message.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

SRC_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_DIR.parent
PROMPT_DIRS = (SRC_DIR / "prompts", SRC_DIR / "conversation" / "prompts", REPO_ROOT / "prompts")

DATA_OPEN = "<texto_cliente>"
DATA_CLOSE = "</texto_cliente>"
_DATA_TAG = re.compile(r"<\s*/?\s*texto_cliente\s*>", re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->\s*", re.S)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_SYSTEM_MARK = re.compile(r"<!--\s*system\s*-->", re.IGNORECASE)
_USER_MARK = re.compile(r"<!--\s*user\s*-->", re.IGNORECASE)
_PROMPT_ID = re.compile(r"[a-z0-9_]+")


def _versions(prompt_id: str) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for base in PROMPT_DIRS:
        folder = Path(base) / prompt_id
        if folder.is_dir():
            for p in folder.glob("v*.md"):
                m = re.fullmatch(r"v(\d+)", p.stem)
                if m:
                    out.append((int(m.group(1)), p))
            if out:
                return out  # the first folder that has the prompt wins
    return out


def prompt_file(prompt_id: str, version: str | None = None) -> tuple[str, Path]:
    """(prompt_version, path). ``version`` like "v1"; None = the highest version."""
    if not _PROMPT_ID.fullmatch(prompt_id or ""):
        raise ValueError(f"invalid prompt_id {prompt_id!r}")
    found = _versions(prompt_id)
    if version is not None:
        found = [(n, p) for n, p in found if p.stem == version]
    if not found:
        raise FileNotFoundError(f"prompt {prompt_id}/{version or 'v*'}.md not found in {[str(d) for d in PROMPT_DIRS]}")
    n, path = max(found)
    return f"{prompt_id}@v{n}", path


def load_prompt(prompt_id: str, version: str | None = None) -> tuple[str, str]:
    """(prompt_version, template without ``<!-- -->`` comments), like ``llm_port.load_prompt``."""
    prompt_version, path = prompt_file(prompt_id, version)
    return prompt_version, _COMMENT.sub("", path.read_text(encoding="utf-8"))


def split_template(raw: str) -> tuple[str | None, str]:
    """(system template or None, user template), comments removed. See the module docstring."""
    marks = list(_USER_MARK.finditer(raw))
    if not marks:
        if _SYSTEM_MARK.search(raw):
            raise ValueError("prompt with <!-- system --> but without <!-- user -->")
        return None, _COMMENT.sub("", raw)
    if len(marks) > 1:
        raise ValueError("prompt with more than one <!-- user --> marker")
    system = _COMMENT.sub("", raw[: marks[0].start()])
    user = _COMMENT.sub("", raw[marks[0].start():])  # the marker goes like any comment, with its newline
    return (system if system.strip() else None), user


def load_prompt_parts(prompt_id: str, version: str | None = None) -> tuple[str, str | None, str]:
    """(prompt_version, system template or None, user template)."""
    prompt_version, path = prompt_file(prompt_id, version)
    system, user = split_template(path.read_text(encoding="utf-8"))
    return prompt_version, system, user


def as_data(text: str) -> str:
    """The customer's text as it goes inside the data block: NFC, the block tags removed."""
    return _DATA_TAG.sub(" ", unicodedata.normalize("NFC", text or "")).strip()


def _values(variables: Mapping[str, Any] | None, output_schema: Mapping[str, Any] | None) -> dict[str, str]:
    values = {k: ("" if v is None else str(v)) for k, v in (variables or {}).items()}
    if "text" in values:
        values["text"] = as_data(values["text"])
    values["output_schema"] = json.dumps(output_schema or {}, ensure_ascii=False, sort_keys=True)
    return values


def _fill(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), template)


def render_prompt(prompt_id: str, variables: Mapping[str, Any] | None, output_schema: Mapping[str, Any] | None = None,
                  version: str | None = None) -> tuple[str, str]:
    """(prompt_version, whole rendered prompt): byte for byte what ``llm_port.render_prompt``
    renders for the same file."""
    prompt_version, template = load_prompt(prompt_id, version)
    return prompt_version, _fill(template, _values(variables, output_schema))


def render_parts(prompt_id: str, variables: Mapping[str, Any] | None, output_schema: Mapping[str, Any] | None = None,
                 version: str | None = None) -> tuple[str, str | None, str]:
    """(prompt_version, rendered system prompt or None, rendered user message)."""
    prompt_version, system, user = load_prompt_parts(prompt_id, version)
    values = _values(variables, output_schema)
    return prompt_version, (None if system is None else _fill(system, values)), _fill(user, values)
