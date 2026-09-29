"""G1: slot and flag extraction with a model, validated and guarded (owner: arturo).

The orchestrator calls ``run`` with the REDACTED text and the regex extraction of the same
turn (``extract.extract``). What G1 may and may not do:

- It only extracts. The lane is decided by ``lane_rules`` from evidence; the model never
  chooses it, never sees a document, address or phone (the text is redacted before), and has
  no tools. Its text goes to the model between data tags (``llm_port.as_data``).
- Call through the #8 port with ``model_role="chat"`` and a timeout (``LLM_TIMEOUT_SECONDS``).
- The output must validate against ``Extraction`` (Pydantic, extra fields forbidden); an
  invalid one is dropped (``error_code: schema_invalid``) and the regex answers.
- Guards against hallucination, on the redacted text: an amount or last-4 that the text does
  not mention (normalized: "cuatrocientos cincuenta", "52 lucas", "449 con 90", digit words)
  is dropped; so is a currency not said explicitly, a merchant whose words are not in the
  text, and a date after the reference date or more than 400 days before it. A dropped slot
  takes the regex value, if any.
- When the model answered: its slots and complaint_type are used (``null`` = "not said"; the
  regex only fills the complaint type and the dropped slots). The lane C flags are OR-ed with
  the pattern lists: the model can add a flag, never remove one the patterns found.
- Failures: timeout, provider unavailable or any other error -> regex, ``degraded``
  (``model_timeout`` for the customer); no fixture (mock) -> regex, NOT degraded, annotated in
  the trace (``error_code: no_fixture``).
"""

from __future__ import annotations

import time
from concurrent.futures import Executor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError

from conversation import extract as extract_mod
from conversation import llm_port

PROMPT_ID = "g1_extract"
MAX_DATE_AGE_DAYS = 400
GUARDED_SLOTS = ("amount", "currency", "date", "merchant_text", "card_last4")


@dataclass
class G1Outcome:
    extraction: extract_mod.Extraction
    used_model: bool
    error_code: str | None = None
    degraded: bool = False
    model_id: str | None = None
    prompt_version: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    dropped: list[str] = field(default_factory=list)

    @property
    def output_ref(self) -> str | None:
        """What the trace keeps (no free text): slot names found and dropped."""
        if not self.used_model:
            return None
        found = ",".join(k for k, v in self.extraction.slots.model_dump().items() if v not in (None, False))
        return f"slots:{found}" + (f";dropped:{','.join(self.dropped)}" if self.dropped else "")


def current_prompt_version() -> str | None:
    try:
        return llm_port.load_prompt(PROMPT_ID)[0]
    except (FileNotFoundError, ValueError, OSError):
        return None


def guard(model: extract_mod.Extraction, text: str, reference_date: date | None) -> tuple[extract_mod.Extraction, list[str]]:
    """Drop the slots the redacted ``text`` does not support. Returns (extraction, dropped)."""
    s = model.slots.model_dump()
    dropped: list[str] = []
    if s["amount"] is not None and not extract_mod.amount_is_mentioned(s["amount"], text):
        dropped.append("amount")
    if s["card_last4"] is not None and (not str(s["card_last4"]).isdigit() or len(str(s["card_last4"])) != 4
                                        or s["card_last4"] not in extract_mod.mentioned_last4(text)):
        dropped.append("card_last4")
    if s["currency"] is not None and s["currency"] not in extract_mod.mentioned_currencies(text):
        dropped.append("currency")
    if s["merchant_text"] is not None and not extract_mod.merchant_is_mentioned(s["merchant_text"], text):
        dropped.append("merchant_text")
    if s["date"] is not None:
        try:
            d = date.fromisoformat(s["date"])
            if reference_date is not None and not (reference_date - timedelta(days=MAX_DATE_AGE_DAYS) <= d <= reference_date):
                dropped.append("date")
        except (TypeError, ValueError):
            dropped.append("date")
    for k in dropped:
        s[k] = None
    if "date" in dropped:
        s["date_is_relative"] = None
    return model.model_copy(update={"slots": extract_mod.Slots(**s)}), dropped


def combine(model: extract_mod.Extraction, regex: extract_mod.Extraction, dropped: list[str],
            version: str | None) -> extract_mod.Extraction:
    slots = model.slots.model_dump()
    for k in dropped:
        slots[k] = getattr(regex.slots, k)
        if k == "date":
            slots["date_is_relative"] = regex.slots.date_is_relative
    flags = {k: bool(getattr(model.flags, k) or getattr(regex.flags, k)) for k in extract_mod.Flags.model_fields}
    return extract_mod.Extraction(slots=extract_mod.Slots(**slots), flags=extract_mod.Flags(**flags),
                                  complaint_type=model.complaint_type or regex.complaint_type,
                                  extractor_version=f"g1:{version or 'unknown'}")


def run(client: Any, text: str, language: str, reference_date: str | None, timeout: float,
        regex: extract_mod.Extraction, pool: Executor) -> G1Outcome:
    """One G1 call. Never raises: every failure returns the regex extraction."""
    ref = date.fromisoformat(reference_date) if reference_date else None
    variables = {"text": llm_port.as_data(text), "language": language, "reference_date": reference_date}
    started = time.perf_counter()

    def done(**kw) -> G1Outcome:
        return G1Outcome(latency_ms=int(round((time.perf_counter() - started) * 1000)), **kw)

    fut = pool.submit(client.complete, PROMPT_ID, variables,
                      output_schema=extract_mod.Extraction.model_json_schema(), model_role="chat")
    try:
        out = fut.result(timeout=timeout)
    except FutureTimeout:
        fut.cancel()
        return done(extraction=regex, used_model=False, error_code="model_timeout", degraded=True,
                    prompt_version=current_prompt_version())
    except llm_port.NoFixture as e:
        return done(extraction=regex, used_model=False, error_code="no_fixture", prompt_version=e.prompt_version)
    except llm_port.ModelError as e:
        return done(extraction=regex, used_model=False, error_code=e.code, degraded=True,
                    prompt_version=e.prompt_version or current_prompt_version())
    except Exception:
        return done(extraction=regex, used_model=False, error_code="model_error", degraded=True,
                    prompt_version=current_prompt_version())
    if not isinstance(out, dict):
        return done(extraction=regex, used_model=False, error_code="schema_invalid")
    version = out.get("prompt_version")
    usage = out.get("usage") if isinstance(out.get("usage"), dict) else {}
    meta = dict(model_id=out.get("model_id"), prompt_version=version,
                tokens_in=usage.get("tokens_in"), tokens_out=usage.get("tokens_out"),
                cost_usd=float(usage.get("cost_usd") or 0.0))
    try:
        model = extract_mod.Extraction.model_validate(out.get("output"))
    except ValidationError:
        # The model answered (it is billed), but its output is not used.
        return done(extraction=regex, used_model=False, error_code="schema_invalid", **meta)
    guarded, dropped = guard(model, text, ref)
    return done(extraction=combine(guarded, regex, dropped, version), used_model=True, dropped=dropped, **meta)
