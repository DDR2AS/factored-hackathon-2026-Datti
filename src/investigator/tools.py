"""The investigator's tools (plan v2 Appendix B): read-only, through the gateway, scoped to the case's
customer (the context is built from the STORED case, never from what the model writes).

``ToolRunner`` executes one tool call: validates arguments, calls the gateway with bounded retries
(2, with backoff), turns the result into compact JSON with an ``evidence_id`` per record, keeps a
snapshot of every record it returned (``records``: the evidence the citation check accepts) and
reports each attempt to the trace (ids and counts only, never free text).

Errors: a data source that keeps failing raises ``ToolsUnavailable`` (no report: the case goes to the
analyst marked ``evidence_incomplete``); a record of another customer or an unknown id is NOT an
exception: the model gets an error result ("not_authorized" / "not_found") and the attempt is traced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from conversation.investigator_stub import ToolBudgetExceeded, ToolCall, ToolsUnavailable

RETRIES = 2
DATA_TOOLS = ("get_case", "get_transaction", "get_txn_history", "find_duplicates", "get_reversals",
              "get_fee_schedule", "get_customer_baseline", "get_risk_evidence", "get_digital_sessions",
              "get_prior_contacts", "get_merchant_stats", "get_cards")
OUTPUT_TOOL = "submit_report"
HYPOTHESES = ("fraud", "forgotten_purchase", "unfamiliar_merchant_name", "duplicate", "fee_error", "pending_reversal")
RECOMMENDATIONS = ("reverse_fee", "open_chargeback", "block_card", "explain_and_close", "request_information",
                   "escalate")


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOL_SPECS: list[dict] = [
    {"name": "get_case", "description": "The case record so far (complaint type, lane reason, the confirmed charge id, "
                                        "promised date, the customer's redacted statement).", "input_schema": _obj({})},
    {"name": "get_transaction", "description": "One transaction of this customer: date, time, amount, merchant, status, "
                                               "response code, fraud_score, type.",
     "input_schema": _obj({"txn_id": {"type": "string"}}, ["txn_id"])},
    {"name": "get_txn_history", "description": "The customer's transactions of the last N days up to the reference date.",
     "input_schema": _obj({"days": {"type": "integer", "minimum": 1, "maximum": 180}}, ["days"])},
    {"name": "find_duplicates", "description": "Other charges with the same merchant, amount and currency within 24 hours.",
     "input_schema": _obj({"txn_id": {"type": "string"}}, ["txn_id"])},
    {"name": "get_reversals", "description": "Reversals linked to a charge (empty if not reversed).",
     "input_schema": _obj({"txn_id": {"type": "string"}}, ["txn_id"])},
    {"name": "get_fee_schedule", "description": "The applicable fee schedule of one of the customer's products (synthetic).",
     "input_schema": _obj({"product_id": {"type": "string"}}, ["product_id"])},
    {"name": "get_customer_baseline", "description": "The customer's usual amounts, merchants, hours and channels "
                                                     "(older purchases only).", "input_schema": _obj({})},
    {"name": "get_risk_evidence", "description": "M3 risk evidence for a charge: amount vs the customer's usual, first time "
                                                 "at the merchant, unusual hour, velocity, login or new device before it.",
     "input_schema": _obj({"txn_id": {"type": "string"}}, ["txn_id"])},
    {"name": "get_digital_sessions", "description": "Digital-banking sessions (logins, device) around a time.",
     "input_schema": _obj({"around": {"type": "string", "description": "ISO date-time"},
                           "minutes": {"type": "integer", "minimum": 1, "maximum": 1440}}, ["around", "minutes"])},
    {"name": "get_prior_contacts", "description": "Prior complaints and calls of the customer (last 12 months).",
     "input_schema": _obj({})},
    {"name": "get_merchant_stats", "description": "Aggregates of a merchant across the bank (never other customers' records).",
     "input_schema": _obj({"merchant_id": {"type": "string"}}, ["merchant_id"])},
    {"name": "get_cards", "description": "The customer's cards and their status.", "input_schema": _obj({})},
    {"name": OUTPUT_TOOL, "description": "Submit the final report (exactly once, at the end).",
     "input_schema": _obj({
         "findings": {"type": "array", "items": _obj({"claim": {"type": "string"},
                                                       "evidence_ids": {"type": "array", "items": {"type": "string"}}},
                                                      ["claim", "evidence_ids"])},
         "hypotheses": {"type": "array", "items": _obj({"name": {"type": "string", "enum": list(HYPOTHESES)},
                                                         "p": {"type": "number"}}, ["name", "p"])},
         "recommendation": {"type": "string", "enum": list(RECOMMENDATIONS)},
         "confidence": {"type": "number"},
         "draft_reply": _obj({"language": {"type": "string", "enum": ["es", "pt"]}, "text": {"type": "string"}},
                             ["language", "text"]),
         "open_questions": {"type": "array", "items": {"type": "string"}},
     }, ["findings", "hypotheses", "recommendation", "confidence", "draft_reply", "open_questions"])},
]


def _scalar(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(_scalar(x)) for x in v)
    return v


def txn_fields(t: Any) -> dict:
    d = t.to_dict()
    d.pop("synthetic", None)
    return d


def txn_summary(t: Any) -> str:
    return f"{t.local_date.isoformat()} {t.local_time} · {t.merchant_name} · {t.amount} {t.currency}"


@dataclass
class ToolRunner:
    case: Any
    gw: Any
    ctx: Any
    sleep: Callable[[float], None]
    on_tool: Callable[[ToolCall], None] | None
    max_calls: int = 12
    calls: int = 0
    refused: int = 0
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ evidence

    def add(self, evidence_id: str, kind: str, summary: str, fields: dict[str, Any]) -> str:
        clean = {k: _scalar(v) for k, v in fields.items() if k not in ("synthetic", "customer_id")}
        clean = {k: v for k, v in clean.items() if v is None or isinstance(v, (str, int, float, bool))}
        self.records[evidence_id] = {"kind": kind, "summary": summary, "fields": clean}
        return evidence_id

    def remaining(self) -> int:
        return self.max_calls - self.calls

    # ------------------------------------------------------------ calls

    def call(self, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """(result for the model, is_error). Raises ToolsUnavailable / ToolBudgetExceeded."""
        if name not in DATA_TOOLS:
            return {"error": "unknown_tool", "tool": name}, True
        if self.calls >= self.max_calls:
            raise ToolBudgetExceeded(name)
        fn = getattr(self, f"_t_{name}")
        inp = ",".join(f"{k}:{_scalar(v)}" for k, v in sorted((args or {}).items()))[:120] or name
        for attempt in range(RETRIES + 1):
            if self.calls >= self.max_calls:
                raise ToolBudgetExceeded(name)
            self.calls += 1
            started = time.perf_counter()
            try:
                result = fn(**(args or {}))
            except TypeError:
                self._trace(name, started, "invalid_arguments", inp, None)
                return {"error": "invalid_arguments", "tool": name}, True
            except self.gw.ToolUnavailable:
                self._trace(name, started, "tool_unavailable", inp, f"attempt:{attempt + 1}")
                if attempt < RETRIES:
                    self.sleep(0.2 * (attempt + 1))
                    continue
                raise ToolsUnavailable(name)
            except self.gw.NotOwned:
                self.refused += 1
                self._trace(name, started, "notowned", inp, None)
                return {"error": "not_authorized", "tool": name}, True
            except self.gw.NotFound:
                self._trace(name, started, "notfound", inp, None)
                return {"error": "not_found", "tool": name}, True
            out_ref = ",".join(result.get("evidence_ids", []))[:200] if isinstance(result, dict) else None
            self._trace(name, started, None, inp, out_ref or "ok")
            self.log.append({"tool": name, "args": {k: _scalar(v) for k, v in (args or {}).items()}})
            return result, False
        raise ToolsUnavailable(name)  # pragma: no cover

    def _trace(self, name, started, error, inp, out) -> None:
        if self.on_tool is not None:
            self.on_tool(ToolCall(name=name, latency_ms=int(round((time.perf_counter() - started) * 1000)),
                                  error_code=error, input_ref=inp, output_ref=out))

    # ------------------------------------------------------------ tool implementations

    def _fn(self, name: str):
        """The gateway function, or NotFound when this backend doesn't offer the tool (the demo
        store has no risk, session or merchant aggregates): the model gets "not_found"."""
        fn = getattr(self.gw, name, None)
        if fn is None:
            raise self.gw.NotFound(f"{name} not available in this backend")
        return fn

    def _txn_out(self, t: Any, kind: str = "transaction") -> dict:
        self.add(t.txn_id, kind, txn_summary(t), txn_fields(t))
        d = txn_fields(t)
        d["evidence_id"] = t.txn_id
        return d

    def _t_get_case(self) -> dict:
        c = self.case
        txn = (c.evidence.transaction or {})
        brief = {"case_id": c.case_id, "complaint_type": c.subcategory, "lane": c.lane.value if c.lane else None,
                 "lane_reason": c.lane_reason, "charge_txn_id": txn.get("txn_id"),
                 "expected_date": c.promise.expected_date.isoformat() if c.promise.expected_date else None,
                 "language": c.language, "country": c.country, "urgency_flags": list(c.urgency_flags),
                 "customer_statement": c.customer_statement}
        # The case itself is context, not evidence (#6 evidence kinds are data records): cite the
        # charge and the records the other tools return.
        return {**brief, "evidence_ids": []}

    def _t_get_transaction(self, txn_id: str) -> dict:
        t = self.gw.get_transaction(self.ctx, str(txn_id))
        return {**self._txn_out(t), "evidence_ids": [t.txn_id]}

    def _t_get_txn_history(self, days: int) -> dict:
        rows = self.gw.get_txn_history(self.ctx, max(1, min(180, int(days))))[:60]
        return {"transactions": [self._txn_out(t) for t in rows], "count": len(rows),
                "evidence_ids": [t.txn_id for t in rows]}

    def _t_find_duplicates(self, txn_id: str) -> dict:
        rows = self.gw.find_duplicates(self.ctx, str(txn_id))
        return {"duplicates": [self._txn_out(t) for t in rows], "evidence_ids": [t.txn_id for t in rows]}

    def _t_get_reversals(self, txn_id: str) -> dict:
        rows = self.gw.get_reversals(self.ctx, str(txn_id))
        return {"reversals": [self._txn_out(t, "reversal") for t in rows], "evidence_ids": [t.txn_id for t in rows]}

    def _t_get_fee_schedule(self, product_id: str) -> dict:
        rule = self.gw.get_fee_schedule(self.ctx, str(product_id))
        fees = []
        for i in rule.fees:
            eid = self.add(f"FEE:{rule.product_id}:{i.fee_code}", "fee_schedule",
                           f"{i.description}: {i.amount} {rule.currency} ({i.frequency})",
                           {"product_id": rule.product_id, "product_name": rule.product_name, "fee_code": i.fee_code,
                            "merchant_id": i.merchant_id, "description": i.description, "amount": str(i.amount),
                            "currency": rule.currency, "frequency": i.frequency})
            fees.append({"evidence_id": eid, "fee_code": i.fee_code, "merchant_id": i.merchant_id,
                         "description": i.description, "amount": str(i.amount), "frequency": i.frequency})
        return {"product_id": rule.product_id, "currency": rule.currency, "fees": fees, "synthetic_schedule": True,
                "evidence_ids": [f["evidence_id"] for f in fees]}

    def _t_get_customer_baseline(self) -> dict:
        b = self.gw.get_customer_baseline(self.ctx)
        fields = {"window_from": b.window_from, "window_to": b.window_to, "n_txns": b.n_txns, "currency": b.currency,
                  "median_amount": b.median_amount, "p90_amount": b.p90_amount, "top_categories": b.top_categories,
                  "top_merchants": b.top_merchants,
                  "usual_hours": "-".join(str(h) for h in b.usual_hours) if b.usual_hours else None,
                  "median_amount_usd": b.median_amount_usd, "p95_amount_usd": b.p95_amount_usd}
        self.add("BASELINE", "customer_baseline",
                 f"{b.n_txns} compras habituales; mediana {b.median_amount} {b.currency}", fields)
        out = {k: _scalar(v) for k, v in fields.items()}
        out["known_merchant_ids"] = list(b.known_merchant_ids)[:40]
        out["usual_channels"] = list(b.usual_channels)
        out["usual_countries"] = list(b.usual_countries)
        return {**out, "evidence_id": "BASELINE", "evidence_ids": ["BASELINE"]}

    def _t_get_risk_evidence(self, txn_id: str) -> dict:
        r = self._fn("get_risk_evidence")(self.ctx, str(txn_id))
        eid = self.add(f"RISK:{r.txn_id}", "risk_evidence",
                       f"fraud_score {r.fraud_score if r.fraud_score is not None else 'sin dato'}; regla >30: "
                       f"{'sí' if r.rule_fired else 'no'}; {r.model_version}",
                       {"txn_id": r.txn_id, "fraud_score": r.fraud_score, "rule_fired": r.rule_fired,
                        "lift_score": r.lift_score, "model_version": r.model_version,
                        **{f"f_{k}": v for k, v in (r.features or {}).items()}})
        return {"txn_id": r.txn_id, "fraud_score": r.fraud_score, "rule_fired": r.rule_fired,
                "features": dict(r.features or {}), "lift_score": r.lift_score, "model_version": r.model_version,
                "evidence_id": eid, "evidence_ids": [eid]}

    def _t_get_digital_sessions(self, around: str, minutes: int) -> dict:
        rows = self._fn("get_digital_sessions")(self.ctx, around, max(1, min(1440, int(minutes))))
        out = []
        for s in rows:
            eid = self.add(f"SESSION:{s.session_id}", "digital_session",
                           f"{s.started_at:%Y-%m-%d %H:%M} · {s.channel} · login {'sí' if s.has_login else 'no'}"
                           f"{' · dispositivo nuevo' if s.first_seen_device else ''}",
                           {"session_id": s.session_id, "started_at": s.started_at, "ended_at": s.ended_at,
                            "channel": s.channel, "platform": s.platform, "ip_country": s.ip_country,
                            "has_login": s.has_login, "first_seen_device": s.first_seen_device})
            out.append({"evidence_id": eid, "started_at": s.started_at.isoformat(), "channel": s.channel,
                        "has_login": s.has_login, "first_seen_device": s.first_seen_device,
                        "ip_country": s.ip_country})
        return {"sessions": out, "evidence_ids": [s["evidence_id"] for s in out]}

    def _t_get_prior_contacts(self) -> dict:
        rows = self.gw.get_prior_contacts(self.ctx)[:30]
        out = []
        for c in rows:
            self.add(c.contact_id, "prior_contact", f"{c.date.isoformat()} · {c.channel} · {c.contact_type} · {c.status}",
                     {"contact_id": c.contact_id, "date": c.date, "channel": c.channel, "contact_type": c.contact_type,
                      "subcategory": c.subcategory, "status": c.status})
            out.append({"evidence_id": c.contact_id, "date": c.date.isoformat(), "channel": c.channel,
                        "contact_type": c.contact_type, "subcategory": c.subcategory, "status": c.status})
        return {"contacts": out, "complaints": sum(1 for c in rows if c.contact_type == "complaint"),
                "evidence_ids": [c["evidence_id"] for c in out]}

    def _t_get_merchant_stats(self, merchant_id: str) -> dict:
        m = self._fn("get_merchant_stats")(self.ctx, str(merchant_id))
        fields = {"merchant_id": m.merchant_id, "merchant_name": m.merchant_name, "category": m.merchant_category,
                  "n_txns": m.n_txns, "n_customers": m.n_customers, "fraud_rate": m.fraud_rate,
                  "decline_rate": m.decline_rate, "reversal_rate": m.reversal_rate,
                  "median_amount_usd": m.median_amount_usd}
        eid = self.add(f"MERCHANT:{m.merchant_id}", "merchant_stats",
                       f"{m.merchant_name}: {m.n_txns} transacciones de {m.n_customers} clientes (agregado)", fields)
        return {**fields, "evidence_id": eid, "evidence_ids": [eid]}

    def _t_get_cards(self) -> dict:
        rows = self.gw.get_cards(self.ctx)
        out = []
        for k in rows:
            self.add(k.card_id, "card", f"Tarjeta •••• {k.card_last4}: {k.status}",
                     {"card_id": k.card_id, "card_last4": k.card_last4, "status": k.status, "product_id": k.product_id})
            out.append({"evidence_id": k.card_id, "card_last4": k.card_last4, "status": k.status,
                        "product_id": k.product_id})
        return {"cards": out, "evidence_ids": [c["evidence_id"] for c in out]}
