"""G2 agent loop: model (or deterministic policy) <-> read-only tools, then a checked report.

``make_investigator(env)`` returns the callable the case lifecycle runs for a lane B money case:
``investigate(case, gateway, ctx, clock, sleep=..., on_tool=...) -> InvestigatorReport dict (#6)``.

- Model: ``llm.converse(model_role="investigator")`` with the Appendix B tools plus
  ``submit_report`` (prompt ``src/prompts/g2_investigator``). With ``LLM_PROVIDER`` mock or none, or
  if the provider fails, the deterministic ``Policy`` drives the same loop and the report says so.
- Budget: ``INVESTIGATOR_MAX_TOOL_CALLS`` data-tool calls (12) and ``INVESTIGATOR_MAX_SECONDS`` (90).
  When the tool budget is spent the model is told to submit; if it still doesn't, no report.
- Checks in code before the report is returned: fixed hypothesis and recommendation lists, the
  draft in the case's language and without promises (else the neutral template), and the citation
  check (``validate_citations``): a finding citing an id no tool returned in this run is removed and
  counted; more than one removed marks the report unreliable in the console.
- The context (whose data) comes from the stored case. Nothing the model writes can widen it.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Callable, Mapping

from conversation.investigator_stub import ToolBudgetExceeded, ToolCall, ToolsUnavailable, safe_draft, validate_citations
from conversation.templates import forbidden_hits
from investigator.policy import POLICY_MODEL_ID, Policy
from investigator.tools import HYPOTHESES, OUTPUT_TOOL, RECOMMENDATIONS, TOOL_SPECS, ToolRunner

PROMPT_ID = "g2_investigator"
MAX_TOOL_CALLS = 12
MAX_SECONDS = 90.0
MAX_TURNS = 20


class InvestigatorError(RuntimeError):
    """The model did not produce a usable report (no submit, refusal, truncated output)."""


def _case_brief(case: Any) -> str:
    txn = case.evidence.transaction or {}
    brief = {
        "case_id": case.case_id, "complaint_type": case.subcategory, "lane_reason": case.lane_reason,
        "language": case.language, "country": case.country,
        "expected_date": case.promise.expected_date.isoformat() if case.promise.expected_date else None,
        "charge": {k: txn.get(k) for k in ("txn_id", "local_date", "local_time", "amount", "currency", "merchant_name",
                                           "merchant_id", "product_id", "card_last4", "txn_type", "status")} if txn else None,
        "customer_statement_redacted": case.customer_statement,
        "urgency_flags": list(case.urgency_flags),
    }
    return json.dumps(brief, ensure_ascii=False, indent=1)


def _normalize(raw: Mapping[str, Any], case: Any, fallback_text: str) -> tuple[dict, list[str]]:
    notes: list[str] = []
    findings = []
    for f in raw.get("findings") or []:
        if isinstance(f, Mapping) and isinstance(f.get("claim"), str):
            ids = [str(i) for i in (f.get("evidence_ids") or []) if isinstance(i, (str, int))]
            findings.append({"claim": f["claim"][:500], "evidence_ids": ids[:20]})
    seen, hyps = set(), []
    for h in raw.get("hypotheses") or []:
        if isinstance(h, Mapping) and h.get("name") in HYPOTHESES and h["name"] not in seen:
            try:
                p = min(1.0, max(0.0, float(h.get("p") or 0)))
            except (TypeError, ValueError):
                continue
            seen.add(h["name"])
            hyps.append({"name": h["name"], "p": p})
    total = sum(h["p"] for h in hyps)
    if total > 0:
        hyps = [{"name": h["name"], "p": round(h["p"] / total, 3)} for h in hyps]
    hyps.sort(key=lambda h: (-h["p"], h["name"]))
    rec = raw.get("recommendation")
    if rec not in RECOMMENDATIONS:
        notes.append(f"Recomendación inválida del modelo ({str(rec)[:40]}); se propone escalar")
        rec = "escalate"
    try:
        conf = min(1.0, max(0.0, float(raw.get("confidence") or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    draft = raw.get("draft_reply") if isinstance(raw.get("draft_reply"), Mapping) else {}
    text = draft.get("text") if isinstance(draft.get("text"), str) else ""
    if draft.get("language") != case.language or not text.strip():
        notes.append("El borrador no estaba en el idioma del caso; se usa el texto neutro")
        text = fallback_text
    text, replaced = safe_draft(text[:2000], fallback_text)
    if replaced:
        notes.append("El borrador tenía una promesa prohibida y se reemplazó por el texto neutro")
    questions = [q[:300] for q in (raw.get("open_questions") or []) if isinstance(q, str)][:10]
    return ({"findings": findings, "hypotheses": hyps, "recommendation": rec, "confidence": round(conf, 2),
             "draft_reply": {"language": case.language, "text": text}, "open_questions": questions + notes}, notes)


def _tool_result_block(tool_use_id: str, result: dict, is_error: bool) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": json.dumps(result, ensure_ascii=False, default=str),
            **({"is_error": True} if is_error else {})}


def run(case: Any, gateway: Any, ctx: Any, clock: Callable[[], datetime], sleep: Callable[[float], None] = time.sleep,
        on_tool: Callable[[ToolCall], None] | None = None, provider: str | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS, max_seconds: float = MAX_SECONDS, llm_module: Any = None) -> dict:
    """One investigation. Raises ToolsUnavailable / ToolBudgetExceeded / InvestigatorError (no report)."""
    started = time.perf_counter()
    runner = ToolRunner(case=case, gw=gateway, ctx=ctx, sleep=sleep, on_tool=on_tool, max_calls=max_tool_calls)
    policy = Policy(case)
    if llm_module is None:
        import llm as llm_module  # noqa: PLC0415
    provider = provider or llm_module.provider_name()
    use_policy = provider in ("mock", "none")
    lang = case.language if case.language in ("es", "pt") else "es"
    prompt_version, system, user = llm_module.render_parts(PROMPT_ID, {
        "case_brief": _case_brief(case), "max_tool_calls": max_tool_calls, "language": lang,
        "country": case.country or ""})
    messages: list[dict] = [{"role": "user", "content": user}]
    usage = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    model_id = POLICY_MODEL_ID if use_policy else None
    fallback_note = None
    submitted: dict | None = None
    nudged = False

    def policy_turn(_system, _messages, _tools) -> dict:
        name, args = policy.next_call(runner.remaining())
        return {"content": [{"type": "tool_use", "id": f"pol-{len(runner.log) + len(policy.done)}-{name}",
                             "name": name, "input": args}], "stop_reason": "tool_use"}

    turns_left = MAX_TURNS

    def fall_back(reason: str) -> None:
        # The provider can't answer, or answered something unusable (HTTP 4xx, refusal, output cut
        # by max_tokens, no submit_report): the deterministic policy continues the SAME run. The
        # tool calls already made were observed by the policy, the reason travels to the analyst as
        # an open question and model_id says who wrote the report. Raising here would leave the
        # case without a report; the lifecycle still catches anything that escapes.
        nonlocal use_policy, model_id, fallback_note, turns_left
        fallback_note = f"El modelo no respondió ({reason}); el reporte lo completó la política determinista"
        use_policy, model_id = True, POLICY_MODEL_ID
        turns_left = MAX_TURNS  # the policy gets a turn budget of its own; the time budget is shared

    while turns_left > 0:
        turns_left -= 1
        elapsed = time.perf_counter() - started
        if elapsed > max_seconds:
            raise ToolBudgetExceeded("time")
        if use_policy:
            resp = policy_turn(system, messages, TOOL_SPECS)
        else:
            try:
                # The per-call timeout is what is left of the run's time budget, so one slow call
                # (SDK retries included) cannot take the run past max_seconds.
                resp = llm_module.converse(system, messages, TOOL_SPECS, model_role="investigator", provider=provider,
                                           timeout=max(1.0, max_seconds - elapsed))
                model_id = resp.get("model_id") or model_id
                for k in ("tokens_in", "tokens_out"):
                    usage[k] += int((resp.get("usage") or {}).get(k) or 0)
                usage["cost_usd"] += float((resp.get("usage") or {}).get("cost_usd") or 0.0)
            except (llm_module.ProviderUnavailable, TimeoutError) as e:
                fall_back(type(e).__name__)
                continue
            except llm_module.ModelError as e:
                status = getattr(e, "status", None)
                fall_back(f"{type(e).__name__}{f' HTTP {status}' if status else ''}")
                continue
            if resp.get("stop_reason") in ("refusal", "max_tokens"):
                fall_back(f"stop_reason {resp.get('stop_reason')}")
                continue
        content = list(resp.get("content") or [])
        messages.append({"role": "assistant", "content": content})
        uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        submit = next((b for b in uses if b.get("name") == OUTPUT_TOOL), None)
        if submit is not None:
            submitted = dict(submit.get("input") or {})
            break
        if not uses:
            if use_policy:
                raise InvestigatorError("the policy produced a turn without tool calls")
            if nudged:
                fall_back("sin submit_report tras el recordatorio")
                continue
            nudged = True
            messages.append({"role": "user", "content": "Termina llamando a submit_report con tu reporte."})
            continue
        results = []
        for b in uses:
            if runner.remaining() <= 0:
                results.append(_tool_result_block(b["id"], {"error": "tool_budget_exhausted",
                                                            "next": "call submit_report now"}, True))
                if nudged:
                    raise ToolBudgetExceeded("tools")
                nudged = True
                continue
            result, is_error = runner.call(b["name"], b.get("input") or {})
            policy.observe(b["name"], b.get("input") or {}, result, is_error)
            results.append(_tool_result_block(b["id"], result, is_error))
        messages.append({"role": "user", "content": results})
    if submitted is None:
        raise InvestigatorError("no report after the maximum number of turns")

    fallback_text = submitted.pop("_fallback_text", None) or Policy(case).compose()["_fallback_text"]
    report, _notes = _normalize(submitted, case, fallback_text)
    if fallback_note:
        report["open_questions"].append(fallback_note)
    if runner.refused:
        report["open_questions"].append(f"Se rechazaron {runner.refused} consultas a registros de otro cliente")
    report.update({
        "report_id": "IR-" + case.case_id.removeprefix("EV-"),
        "case_id": case.case_id,
        "created_at": clock().isoformat(),
        "citations_valid": False,
        "removed_claims": 0,
        "tool_calls": runner.calls,
        "latency_ms": 0,
        "model_id": model_id or "unknown",
        "prompt_version": prompt_version,
        "evidence_records": {},
    })
    report = validate_citations(report, runner.records)
    report["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
    if forbidden_hits(report["draft_reply"]["text"]):  # belt and braces: the lifecycle checks it too
        report["draft_reply"]["text"] = fallback_text
    report["_usage"] = usage
    return report


def make_investigator(env: Mapping[str, str] | None = None) -> Callable[..., dict]:
    env = os.environ if env is None else env
    try:
        max_calls = int(env.get("INVESTIGATOR_MAX_TOOL_CALLS") or MAX_TOOL_CALLS)
    except ValueError:
        max_calls = MAX_TOOL_CALLS
    try:
        max_seconds = float(env.get("INVESTIGATOR_MAX_SECONDS") or MAX_SECONDS)
    except ValueError:
        max_seconds = MAX_SECONDS
    provider = (env.get("LLM_PROVIDER") or "none").strip().lower()

    def investigate(case, gateway, ctx, clock, sleep=time.sleep, on_tool=None) -> dict:
        report = run(case, gateway, ctx, clock, sleep=sleep, on_tool=on_tool, provider=provider,
                     max_tool_calls=max_calls, max_seconds=max_seconds)
        report.pop("_usage", None)
        return report

    investigate.trace_step = ("model", "investigator")  # type: ignore[attr-defined]
    investigate.provider = provider  # type: ignore[attr-defined]
    return investigate
