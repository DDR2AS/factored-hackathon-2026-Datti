"""PROVISIONAL deterministic investigator (owner of G2: Andrés). SYNTHETIC data only.

This is NOT the G2 investigator. G2 (plan v2 section 6: Sonnet 5 with read-only tools, at most
12 tool calls and 90 s, cited report) is Andrés's. Until it lands, lane B money cases get this
stub so the analyst console, the case lifecycle and the decision flow can be built and tested
end to end. It is marked everywhere as provisional:

- ``model_id = "stub-g2-deterministic"`` and ``prompt_version = "stub-provisional"`` (the
  values ``frontend/src/api/types.ts`` fixes for the stub; the console shows it as
  "Investigador provisional (sin modelo)"). No model is called.
- the first open question says it is provisional.

What it keeps from the G2 contract (INTERFACES.md #6, plus ``evidence_records``):
- It only reads through the demo gateway tools (#2), scoped by a ``GatewayContext`` that the
  server builds from the stored case, never from a request. Every attempt counts in
  ``tool_calls`` (at most ``MAX_TOOL_CALLS`` = 12) and ``latency_ms`` is measured.
- Every finding cites evidence ids taken from this run's tool results (``TX-…``,
  ``FEE:<product_id>:<fee_code>``, ``RISK:<txn_id>``, ``BASELINE``, ``MERCHANT:<merchant_id>``,
  ``DEMO-CT-…``, ``DEMO-K-…``; never the customer_id). ``validate_citations`` (reusable by
  the real G2) drops any finding with an id that is not in the tool results and counts it in
  ``removed_claims``; ``citations_valid`` says every citation LEFT in the stored report points to
  a record of this run (true after the cleanup). Reliability follows plan section 6: the console
  marks the report unreliable only when more than one claim was removed
  (``contract.report_reliable``).
- Six hypotheses with p summing to 1, one recommendation from the fixed list, a confidence,
  a draft reply in the customer's language and country register, open questions.
- Same case, same report (except ``created_at`` and ``latency_ms``).
- The draft never promises a refund or compensation: it goes through
  ``templates.forbidden_hits`` and falls back to a neutral template if anything matches. It
  describes what the record shows and the proposed next step; only a person approves it.
- Nothing here writes data or moves money. The recommendation is a proposal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from conversation.case import CaseRecord
from conversation.templates import forbidden_hits, format_date, format_money, render

STUB_MODEL_ID = "stub-g2-deterministic"
STUB_PROMPT_VERSION = "stub-provisional"
# Shown to the analyst: no internal team notes here (CX-15). Who owns the real investigator
# is in docs/contrato_consola.md.
PROVISIONAL_NOTE = ("Reporte PROVISIONAL (reglas, sin modelo). Verificar la evidencia antes de decidir.")
MAX_TOOL_CALLS = 12
RETRIES = 2  # like the orchestrator: two retries, then the tool counts as unavailable
FRAUD_THRESHOLD = 30.0
HISTORY_DAYS = 90
MONEY_TYPES = ("unrecognized_charge", "wrong_fee")
LOCALES = {("es", "MX"): "es-MX", ("es", "CO"): "es-CO", ("es", "AR"): "es-AR"}

HYPOTHESES = ("fraud", "forgotten_purchase", "unfamiliar_merchant_name", "duplicate", "fee_error",
              "pending_reversal")

# scenario -> (recommendation, confidence, hypothesis probabilities). Fixed tables: the stub
# ranks nothing, it only reads which scenario the evidence shows. Each row sums to 1.
SCENARIOS: dict[str, tuple[str, float, dict[str, float]]] = {
    "duplicate_not_reversed": ("open_chargeback", 0.8, {
        "duplicate": 0.8, "pending_reversal": 0.08, "forgotten_purchase": 0.05, "fraud": 0.03,
        "unfamiliar_merchant_name": 0.02, "fee_error": 0.02}),
    "fee_off_schedule": ("reverse_fee", 0.85, {
        "fee_error": 0.85, "pending_reversal": 0.05, "duplicate": 0.04, "fraud": 0.02,
        "forgotten_purchase": 0.02, "unfamiliar_merchant_name": 0.02}),
    "purchase_amount_disputed": ("request_information", 0.5, {
        "forgotten_purchase": 0.4, "pending_reversal": 0.2, "duplicate": 0.15, "fraud": 0.1,
        "fee_error": 0.1, "unfamiliar_merchant_name": 0.05}),
    "unrecognized_new_merchant": ("open_chargeback", 0.6, {
        "fraud": 0.45, "unfamiliar_merchant_name": 0.25, "forgotten_purchase": 0.15,
        "pending_reversal": 0.06, "duplicate": 0.05, "fee_error": 0.04}),
    "unrecognized_known_merchant": ("request_information", 0.55, {
        "forgotten_purchase": 0.45, "unfamiliar_merchant_name": 0.25, "fraud": 0.15,
        "pending_reversal": 0.06, "duplicate": 0.05, "fee_error": 0.04}),
    "explained": ("explain_and_close", 0.75, {
        "pending_reversal": 0.4, "forgotten_purchase": 0.25, "fee_error": 0.1, "duplicate": 0.1,
        "unfamiliar_merchant_name": 0.1, "fraud": 0.05}),
    "generic": ("escalate", 0.3, {
        "fraud": 0.25, "forgotten_purchase": 0.2, "unfamiliar_merchant_name": 0.15, "duplicate": 0.15,
        "fee_error": 0.15, "pending_reversal": 0.1}),
}

OPEN_QUESTIONS = {
    "duplicate_not_reversed": "Confirmar con el comercio que el segundo cargo no corresponde a otra compra",
    "fee_off_schedule": "Confirmar que no hubo un cambio de tarifa notificado para este producto",
    "purchase_amount_disputed": "Pedir al cliente el comprobante o el monto que esperaba pagar",
    "unrecognized_new_merchant": "Confirmar con el cliente que nadie con acceso a la tarjeta hizo la compra",
    "unrecognized_known_merchant": "Preguntar si alguien más usa la tarjeta y pedir el comprobante",
    "explained": "El registro explica el cargo: confirmar si el cliente acepta la explicación",
    "generic": "El stub no reconoce el escenario: revisar el caso completo",
}


class ToolBudgetExceeded(RuntimeError):
    """More than MAX_TOOL_CALLS tool calls in one run."""


class ToolsUnavailable(RuntimeError):
    """A required tool failed after the retries; no report is produced."""


@dataclass
class ToolCall:
    """One attempt, for the trace (#7): no free text, ids only."""

    name: str
    latency_ms: int
    error_code: str | None
    input_ref: str
    output_ref: str | None


@dataclass
class _Run:
    gw: Any
    ctx: Any
    sleep: Callable[[float], None]
    on_tool: Callable[[ToolCall], None] | None
    calls: int = 0
    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    def call(self, name: str, fn: Callable[[], Any], inp: str, out: Callable[[Any], str] | None = None) -> Any:
        for attempt in range(RETRIES + 1):
            if self.calls >= MAX_TOOL_CALLS:
                raise ToolBudgetExceeded(name)
            self.calls += 1
            started = time.perf_counter()
            try:
                result = fn()
            except self.gw.ToolUnavailable:
                self._trace(name, started, "tool_unavailable", inp, f"attempt:{attempt + 1}")
                if attempt < RETRIES:
                    self.sleep(0.2 * (attempt + 1))
                    continue
                raise ToolsUnavailable(name)
            except (self.gw.NotOwned, self.gw.NotFound) as e:
                self._trace(name, started, type(e).__name__.lower(), inp, None)
                raise ToolsUnavailable(name)
            self._trace(name, started, None, inp, out(result) if out else None)
            return result
        raise ToolsUnavailable(name)  # pragma: no cover

    def remaining(self) -> int:
        return MAX_TOOL_CALLS - self.calls

    def _trace(self, name, started, error, inp, out) -> None:
        if self.on_tool is not None:
            self.on_tool(ToolCall(name=name, latency_ms=int(round((time.perf_counter() - started) * 1000)),
                                  error_code=error, input_ref=inp, output_ref=out))

    def add(self, evidence_id: str, kind: str, summary: str, fields: dict[str, Any]) -> str:
        clean = {k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
                 for k, v in fields.items() if k not in ("synthetic", "customer_id")}
        self.records[evidence_id] = {"kind": kind, "summary": summary, "fields": clean}
        return evidence_id


# ---------------------------------------------------------------- citation check (reusable)

def validate_citations(report: Mapping[str, Any],
                       tool_results: Mapping[str, Any] | Iterable[str]) -> dict[str, Any]:
    """The #6 citation check, in code, before a report is stored. Reusable by the real G2.

    ``tool_results``: the evidence ids this run's tools returned (a mapping id -> EvidenceRecord
    dict, or just the ids). Every finding whose ``evidence_ids`` is empty or cites an id that is
    not there is removed. Returns a NEW report dict with ``findings`` filtered,
    ``removed_claims`` = how many were removed, ``citations_valid`` = every id still cited has
    its record (true after the cleanup; plan section 6 decides reliability by
    ``removed_claims > 1``, see ``contract.report_reliable``) and ``evidence_records`` = the records of the ids still cited (from ``tool_results`` when it
    is a mapping, else from the report's own ``evidence_records``).
    """
    known = dict(tool_results) if isinstance(tool_results, Mapping) else {i: None for i in tool_results}
    kept, removed = [], 0
    for f in report.get("findings") or []:
        ids = list(f.get("evidence_ids") or [])
        if ids and all(i in known for i in ids):
            kept.append({"claim": f.get("claim", ""), "evidence_ids": ids})
        else:
            removed += 1
    source = report.get("evidence_records") or {}
    records = {}
    for f in kept:
        for i in f["evidence_ids"]:
            rec = known.get(i) if known.get(i) is not None else source.get(i)
            if rec is not None:
                records[i] = rec
    out = dict(report)
    out["findings"] = kept
    out["removed_claims"] = removed
    out["citations_valid"] = all(i in records for f in kept for i in f["evidence_ids"])
    out["evidence_records"] = records
    return out


def safe_draft(text: str, fallback: str) -> tuple[str, bool]:
    """(text, replaced): the draft if it promises nothing, else the neutral fallback."""
    if forbidden_hits(text):
        return fallback, True
    return text, False


# ---------------------------------------------------------------- the stub

def _txn_fields(t) -> dict[str, Any]:
    d = t.to_dict()
    d.pop("fraud_score", None)
    return d


def _txn_summary(t) -> str:
    return f"{t.local_date.isoformat()} {t.local_time} · {t.merchant_name} · {t.amount} {t.currency}"


def investigate(case: CaseRecord, gateway: Any, ctx: Any, clock: Callable[[], datetime],
                sleep: Callable[[float], None] = time.sleep,
                on_tool: Callable[[ToolCall], None] | None = None,
                extra_findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One deterministic investigation of a lane B money case. Returns an InvestigatorReport
    dict (#6 + evidence_records), already through ``validate_citations``.

    ``extra_findings`` exists for tests: findings appended before the citation check (e.g.
    one citing an id no tool returned, which must be removed). Raises ToolsUnavailable when a
    required tool fails after the retries.
    """
    started = time.perf_counter()
    run = _Run(gw=gateway, ctx=ctx, sleep=sleep, on_tool=on_tool)
    lang = case.language
    findings: list[dict[str, Any]] = []
    questions = [PROVISIONAL_NOTE]
    scenario = "generic"
    draft_vars: dict[str, Any] = {"case_id": case.case_id}
    locale = "pt-BR" if lang == "pt" else LOCALES.get((lang, case.country or ""), "es-MX")
    expected = case.promise.expected_date
    draft_vars["expected_date"] = format_date(expected, lang) if expected else render("investigator.no_date", lang)

    txn_ref = (case.evidence.transaction or {}).get("txn_id")
    if txn_ref:
        txn = run.call("get_transaction", lambda: gateway.get_transaction(ctx, txn_ref), f"txn:{txn_ref}",
                       lambda r: f"txn:{r.txn_id}")
        run.add(txn.txn_id, "transaction", _txn_summary(txn), _txn_fields(txn))
        draft_vars.update(amount=format_money(txn.amount, txn.currency, locale), merchant=txn.merchant_name,
                          date=format_date(txn.local_date, lang))
        findings.append({"claim": f"Cargo verificado en el registro: {_txn_summary(txn)} "
                                  f"(tipo {txn.txn_type}, tarjeta •••• {txn.card_last4 or '----'})",
                         "evidence_ids": [txn.txn_id]})
        risk_id = run.add(f"RISK:{txn.txn_id}", "risk_evidence",
                          f"fraud_score {txn.fraud_score if txn.fraud_score is not None else 'sin dato'} "
                          f"(umbral {FRAUD_THRESHOLD:g})",
                          {"txn_id": txn.txn_id, "fraud_score": txn.fraud_score, "threshold": FRAUD_THRESHOLD,
                           "source": "get_transaction.fraud_score"})
        score = txn.fraud_score
        if score is not None:
            findings.append({"claim": f"fraud_score del registro {score:g}, "
                                      f"{'por encima' if score > FRAUD_THRESHOLD else 'por debajo'} del umbral de "
                                      f"{FRAUD_THRESHOLD:g}", "evidence_ids": [risk_id]})

        dups = run.call("find_duplicates", lambda: gateway.find_duplicates(ctx, txn.txn_id), f"txn:{txn.txn_id}",
                        lambda r: "txns:" + ",".join(x.txn_id for x in r))
        for d in dups:
            run.add(d.txn_id, "transaction", _txn_summary(d), _txn_fields(d))
        members = [txn] + list(dups)
        reversals = []
        for m in members:
            if run.remaining() < 4:  # keep budget for baseline, history, contacts and cards
                questions.append(f"No se consultaron reversos de {m.txn_id}: presupuesto de herramientas")
                continue
            revs = run.call("get_reversals", lambda m=m: gateway.get_reversals(ctx, m.txn_id), f"txn:{m.txn_id}",
                            lambda r: "txns:" + ",".join(x.txn_id for x in r))
            for r in revs:
                run.add(r.txn_id, "reversal", _txn_summary(r), _txn_fields(r))
                reversals.append(r)
        member_ids = [m.txn_id for m in members]
        if dups:
            findings.append({"claim": "Cargo idéntico (mismo comercio, monto y moneda) en menos de 24 h: "
                                      + ", ".join(d.txn_id for d in dups), "evidence_ids": member_ids})
        if reversals:
            for r in reversals:
                findings.append({"claim": f"Reverso registrado {r.txn_id} del {r.local_date.isoformat()} "
                                          f"por {abs(r.amount)} {r.currency} sobre {r.reversal_of}",
                                 "evidence_ids": [r.txn_id] + ([r.reversal_of] if r.reversal_of in member_ids else [])})
        else:
            findings.append({"claim": ("Ninguno de los cargos iguales tiene reverso registrado" if dups
                                       else "El cargo no tiene reverso registrado"), "evidence_ids": member_ids})

        fee_item = None
        if txn.txn_type == "fee":  # a purchase has no schedule to compare with
            rule = run.call("get_fee_schedule", lambda: gateway.get_fee_schedule(ctx, txn.product_id),
                            f"product:{txn.product_id}", lambda r: f"fees:{len(r.fees)}")
            fee_item = gateway.fee_item_for_txn(rule, txn)
            if fee_item is not None:
                fee_id = run.add(f"FEE:{rule.product_id}:{fee_item.fee_code}", "fee_schedule",
                                 f"{fee_item.description}: {fee_item.amount} {rule.currency} ({fee_item.frequency})",
                                 {"product_id": rule.product_id, "product_name": rule.product_name,
                                  "fee_code": fee_item.fee_code, "description": fee_item.description,
                                  "amount": str(fee_item.amount), "currency": rule.currency,
                                  "frequency": fee_item.frequency})
                matches = fee_item.amount == abs(txn.amount)
                findings.append({"claim": f"Tarifa vigente {fee_item.amount} {rule.currency}; el cargo fue "
                                          f"{abs(txn.amount)} {txn.currency}"
                                          + ("" if matches else f" (diferencia {abs(txn.amount) - fee_item.amount})"),
                                 "evidence_ids": [fee_id, txn.txn_id]})
                draft_vars.update(fee_name=fee_item.description,
                                  fee_amount=format_money(fee_item.amount, rule.currency, locale))

        baseline = run.call("get_customer_baseline", lambda: gateway.get_customer_baseline(ctx), "baseline",
                            lambda r: f"n:{r.n_txns}")
        run.add("BASELINE", "customer_baseline",
                f"{baseline.n_txns} compras habituales; mediana {baseline.median_amount} {baseline.currency}",
                {"window_from": baseline.window_from.isoformat() if baseline.window_from else None,
                 "window_to": baseline.window_to.isoformat(), "n_txns": baseline.n_txns, "currency": baseline.currency,
                 "median_amount": str(baseline.median_amount) if baseline.median_amount is not None else None,
                 "p90_amount": str(baseline.p90_amount) if baseline.p90_amount is not None else None,
                 "top_categories": ", ".join(baseline.top_categories),
                 "usual_hours": "-".join(str(h) for h in baseline.usual_hours) if baseline.usual_hours else None})
        if baseline.median_amount is not None and txn.currency == baseline.currency:
            above = baseline.p90_amount is not None and abs(txn.amount) > baseline.p90_amount
            findings.append({"claim": f"Monto {abs(txn.amount)} frente a la mediana habitual {baseline.median_amount} "
                                      f"y p90 {baseline.p90_amount} {baseline.currency}: "
                                      + ("por encima de lo habitual" if above else "dentro de lo habitual"),
                             "evidence_ids": ["BASELINE", txn.txn_id]})

        previous = []
        if txn.txn_type == "purchase":  # a fee has no merchant to know
            history = run.call("get_txn_history", lambda: gateway.get_txn_history(ctx, HISTORY_DAYS),
                               f"days:{HISTORY_DAYS}", lambda r: f"txns:{len(r)}")
            previous = [h for h in history if h.merchant_id == txn.merchant_id and h.txn_id not in member_ids
                        and h.txn_type == "purchase"]
            merchant_id = run.add(f"MERCHANT:{txn.merchant_id}", "merchant_stats",
                                  f"{len(previous)} compras previas en {txn.merchant_name} en {HISTORY_DAYS} días",
                                  {"merchant_id": txn.merchant_id, "merchant_name": txn.merchant_name,
                                   "prior_purchases": len(previous), "window_days": HISTORY_DAYS,
                                   "source": "get_txn_history (agregado de 90 días)"})
            cite = [merchant_id]
            if previous:
                last = max(previous, key=lambda h: (h.local_date, h.local_time, h.txn_id))
                cite.append(run.add(last.txn_id, "transaction", _txn_summary(last), _txn_fields(last)))
            findings.append({"claim": f"Compras previas del cliente en {txn.merchant_name} en {HISTORY_DAYS} "
                                      f"días: {len(previous)}", "evidence_ids": cite})

        # Scenario: what the evidence shows (never a model).
        reversed_any = bool(reversals)
        if dups and not reversed_any:
            scenario = "duplicate_not_reversed"
        elif reversed_any:
            scenario = "explained"
        elif fee_item is not None:
            scenario = "explained" if fee_item.amount == abs(txn.amount) else "fee_off_schedule"
        elif case.subcategory == "wrong_fee":
            scenario = "purchase_amount_disputed"
        elif case.subcategory == "unrecognized_charge" and (score is None or score <= FRAUD_THRESHOLD):
            scenario = "unrecognized_known_merchant" if previous else "unrecognized_new_merchant"
    else:
        questions.append("El caso no tiene un cargo confirmado: identificarlo con el cliente")

    if run.remaining() >= 2:
        contacts = run.call("get_prior_contacts", lambda: gateway.get_prior_contacts(ctx), "contacts",
                            lambda r: f"contacts:{len(r)}")
        complaints = [c for c in contacts if c.contact_type == "complaint"]
        for c in contacts:
            run.add(c.contact_id, "prior_contact", f"{c.date.isoformat()} · {c.channel} · {c.contact_type} · {c.status}",
                    {"contact_id": c.contact_id, "date": c.date.isoformat(), "channel": c.channel,
                     "contact_type": c.contact_type, "subcategory": c.subcategory, "status": c.status})
        if complaints:
            findings.append({"claim": f"Reclamos previos en el historial: {len(complaints)}",
                             "evidence_ids": [c.contact_id for c in complaints]})
        cards = run.call("get_cards", lambda: gateway.get_cards(ctx), "cards", lambda r: f"cards:{len(r)}")
        last4 = (case.evidence.transaction or {}).get("card_last4")
        for k in cards:
            if last4 and k.card_last4 != last4:
                continue
            run.add(k.card_id, "card", f"Tarjeta •••• {k.card_last4}: {k.status}",
                    {"card_id": k.card_id, "card_last4": k.card_last4, "status": k.status, "product_id": k.product_id})
            findings.append({"claim": f"Tarjeta •••• {k.card_last4} en estado {k.status}", "evidence_ids": [k.card_id]})

    recommendation, confidence, probs = SCENARIOS[scenario]
    questions.append(OPEN_QUESTIONS[scenario])
    hypotheses = sorted(({"name": n, "p": probs[n]} for n in HYPOTHESES), key=lambda h: (-h["p"], h["name"]))

    country = case.country
    key = f"investigator.draft.{scenario}"
    if scenario != "generic" and not {"amount", "merchant", "date"} <= set(draft_vars):
        key = "investigator.draft.generic"
    if key == "investigator.draft.fee_off_schedule" and "fee_amount" not in draft_vars:
        key = "investigator.draft.generic"
    fallback = render("investigator.draft.generic", lang, country, **draft_vars)
    draft, replaced = safe_draft(render(key, lang, country, **draft_vars), fallback)
    if replaced:
        questions.append("El borrador tenía una promesa prohibida y se reemplazó por el texto neutro")

    report = {
        "report_id": "IR-" + case.case_id.removeprefix("EV-"),
        "case_id": case.case_id,
        "created_at": clock().isoformat(),
        "findings": findings + list(extra_findings or []),
        "hypotheses": hypotheses,
        "recommendation": recommendation,
        "confidence": confidence,
        "draft_reply": {"language": lang, "text": draft},
        "open_questions": questions,
        "citations_valid": False,  # set by validate_citations
        "removed_claims": 0,
        "tool_calls": run.calls,
        "latency_ms": 0,
        "model_id": STUB_MODEL_ID,
        "prompt_version": STUB_PROMPT_VERSION,
        "evidence_records": {},
    }
    report = validate_citations(report, run.records)
    report["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
    return report


def is_money_case(case: CaseRecord) -> bool:
    """Lane B cases that go through the investigator: charges and fees (duplicates and
    purchase_amount_disputed included). App, branch and service complaints do not."""
    return case.subcategory in MONEY_TYPES

