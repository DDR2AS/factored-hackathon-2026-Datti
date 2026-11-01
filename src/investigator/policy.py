"""Deterministic investigation policy: drives the SAME agent loop as a model (tool calls, budget,
citation check) when no model is configured (``LLM_PROVIDER=mock|none``) or the provider fails.

It is a transparent heuristic, not GenAI, and the report says so (``model_id`` ``policy-g2-v1``):
it gathers the Appendix B evidence in a fixed order, scores the six hypotheses from that evidence
(log-linear weights below, documented and tested) and writes the draft from the reviewed templates.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from conversation.templates import format_date, format_money, render
from investigator.tools import HYPOTHESES, OUTPUT_TOOL

POLICY_MODEL_ID = "policy-g2-v1"
FRAUD_THRESHOLD = 30.0
LOCALES = {("es", "MX"): "es-MX", ("es", "CO"): "es-CO", ("es", "AR"): "es-AR"}
PRIORS = {"fraud": 0.10, "forgotten_purchase": 0.25, "unfamiliar_merchant_name": 0.15, "duplicate": 0.05,
          "fee_error": 0.05, "pending_reversal": 0.05}
OPEN_QUESTIONS = {
    "duplicate": "Confirmar con el comercio que el segundo cargo no corresponde a otra compra",
    "fee_error": "Confirmar que no hubo un cambio de tarifa notificado para este producto",
    "fraud": "Confirmar con el cliente que nadie con acceso a la tarjeta hizo la compra",
    "forgotten_purchase": "Preguntar si alguien más usa la tarjeta y pedir el comprobante",
    "unfamiliar_merchant_name": "Explicar el nombre con el que el comercio aparece en el estado de cuenta",
    "pending_reversal": "El registro explica el cargo: confirmar si el cliente acepta la explicación",
    "purchase": "Pedir al cliente el comprobante o el monto que esperaba pagar",
}


class Policy:
    """Call ``next_call()`` until it returns the submit_report input."""

    def __init__(self, case: Any) -> None:
        self.case = case
        self.charge_id = (case.evidence.transaction or {}).get("txn_id")
        self.results: dict[str, Any] = {}
        self.reversals: dict[str, list[dict]] = {}
        self.plan: list[tuple[str, dict]] = []
        self.done: set[str] = set()

    # ------------------------------------------------------------ observing results

    def observe(self, name: str, args: dict, result: dict, is_error: bool) -> None:
        key = name if name != "get_reversals" else f"get_reversals:{args.get('txn_id')}"
        self.done.add(key)
        if is_error:
            return
        if name == "get_reversals":
            self.reversals[str(args.get("txn_id"))] = result.get("reversals", [])
        else:
            self.results[name] = result

    # ------------------------------------------------------------ choosing the next call

    def next_call(self, remaining: int) -> tuple[str, dict]:
        txn = self.results.get("get_transaction")
        steps: list[tuple[str, str, dict]] = []
        if self.charge_id:
            steps.append(("get_transaction", "get_transaction", {"txn_id": self.charge_id}))
            if txn:
                steps.append(("find_duplicates", "find_duplicates", {"txn_id": self.charge_id}))
                members = [self.charge_id] + [d["txn_id"] for d in self.results.get("find_duplicates", {}).get("duplicates", [])]
                if "find_duplicates" in self.done:
                    steps += [(f"get_reversals:{m}", "get_reversals", {"txn_id": m}) for m in members[:3]]
                if txn.get("txn_type") == "fee":
                    steps.append(("get_fee_schedule", "get_fee_schedule", {"product_id": txn["product_id"]}))
                steps.append(("get_customer_baseline", "get_customer_baseline", {}))
                base = self.results.get("get_customer_baseline")
                if "get_customer_baseline" in self.done and not (base or {}).get("known_merchant_ids")                         and txn.get("txn_type") == "purchase":
                    # no merchant history in the baseline (demo store): read 90 days of movements
                    steps.append(("get_txn_history", "get_txn_history", {"days": 90}))
                steps.append(("get_risk_evidence", "get_risk_evidence", {"txn_id": self.charge_id}))
                steps.append(("get_digital_sessions", "get_digital_sessions",
                              {"around": f"{txn['local_date']}T{txn['local_time']}:00", "minutes": 60}))
                steps.append(("get_prior_contacts", "get_prior_contacts", {}))
                if str(txn.get("merchant_id", "")).startswith("M-"):
                    steps.append(("get_merchant_stats", "get_merchant_stats", {"merchant_id": txn["merchant_id"]}))
                steps.append(("get_cards", "get_cards", {}))
        else:
            steps.append(("get_case", "get_case", {}))
            steps.append(("get_prior_contacts", "get_prior_contacts", {}))
        for key, name, args in steps:
            if key not in self.done and remaining > 0:
                return name, args
        return OUTPUT_TOOL, self.compose()

    # ------------------------------------------------------------ the report

    def compose(self) -> dict:
        case = self.case
        lang = case.language if case.language in ("es", "pt") else "es"
        locale = "pt-BR" if lang == "pt" else LOCALES.get((lang, case.country or ""), "es-MX")
        txn = self.results.get("get_transaction")
        findings: list[dict] = []
        questions: list[str] = []
        draft_vars: dict[str, Any] = {"case_id": case.case_id,
                                      "expected_date": format_date(case.promise.expected_date, lang)
                                      if case.promise.expected_date else render("investigator.no_date", lang)}
        score = {h: 0.0 for h in HYPOTHESES}
        if not txn:
            questions.append("El caso no tiene un cargo confirmado: identificarlo con el cliente")
            return self._report(findings, score, questions, lang, "generic", draft_vars, txn=None)

        tid = txn["txn_id"]
        amount = abs(float(txn["amount"]))
        draft_vars.update(amount=format_money(abs(float(txn["amount"])), txn["currency"], locale),
                          merchant=txn["merchant_name"], date=format_date(txn["local_date"], lang))
        findings.append({"claim": f"Cargo verificado en el registro: {txn['local_date']} {txn['local_time']} · "
                                  f"{txn['merchant_name']} · {txn['amount']} {txn['currency']} "
                                  f"(tipo {txn['txn_type']}, estado {txn['status']})", "evidence_ids": [tid]})
        dups = self.results.get("find_duplicates", {}).get("duplicates", [])
        members = [tid] + [d["txn_id"] for d in dups]
        revs = [r for m in members for r in self.reversals.get(m, [])]
        if dups:
            findings.append({"claim": "Cargo idéntico (mismo comercio, monto y moneda) en menos de 24 h: "
                                      + ", ".join(d["txn_id"] for d in dups), "evidence_ids": members})
        if revs:
            for r in revs:
                findings.append({"claim": f"Reverso {r['txn_id']} del {r['local_date']} sobre {r.get('reversal_of')}",
                                 "evidence_ids": [r["txn_id"]] + ([r["reversal_of"]] if r.get("reversal_of") in members else [])})
            score["pending_reversal"] += 2.5
        elif "find_duplicates" in self.done:
            findings.append({"claim": ("Ninguno de los cargos iguales tiene reverso registrado" if dups
                                       else "El cargo no tiene reverso registrado"), "evidence_ids": members})
        if txn["status"] in ("pending", "reversed"):
            score["pending_reversal"] += 1.5
        if dups and not revs:
            score["duplicate"] += 3.2

        scenario = None
        fees = self.results.get("get_fee_schedule")
        if txn["txn_type"] == "fee":
            item = next((f for f in (fees or {}).get("fees", []) if f["merchant_id"] == txn["merchant_id"]), None)
            if item:
                matches = abs(float(item["amount"]) - amount) < 0.005
                findings.append({"claim": f"Tarifa vigente {item['amount']} {fees['currency']} ({item['description']}); "
                                          f"el cargo fue {amount:.2f} {txn['currency']}"
                                          + ("" if matches else f" (diferencia {amount - float(item['amount']):.2f})"),
                                 "evidence_ids": [item["evidence_id"], tid]})
                draft_vars.update(fee_name=item["description"],
                                  fee_amount=format_money(float(item["amount"]), fees["currency"], locale))
                if matches:
                    score["pending_reversal"] += 1.0
                    scenario = "explained"
                else:
                    score["fee_error"] += 3.4
            else:
                score["fee_error"] += 0.8
        elif case.subcategory == "wrong_fee":
            scenario = "purchase"

        base = self.results.get("get_customer_baseline")
        risk = self.results.get("get_risk_evidence")
        feats = (risk or {}).get("features") or {}
        if base and base.get("median_amount") is not None and txn["currency"] == base.get("currency"):
            p90 = float(base["p90_amount"]) if base.get("p90_amount") is not None else None
            above = p90 is not None and amount > p90
            findings.append({"claim": f"Monto {amount:.2f} frente a la mediana habitual {base['median_amount']} y p90 "
                                      f"{base['p90_amount']} {base['currency']}: "
                                      + ("por encima de lo habitual" if above else "dentro de lo habitual"),
                             "evidence_ids": ["BASELINE", tid]})
            if above:
                score["fraud"] += 0.5
        known = bool(base) and txn.get("merchant_id") in (base.get("known_merchant_ids") or [])
        history = self.results.get("get_txn_history")
        merchant_checked = bool(base and base.get("known_merchant_ids")) or history is not None
        if history is not None:
            prev = [h for h in history.get("transactions", []) if h.get("merchant_id") == txn.get("merchant_id")
                    and h["txn_id"] not in members and h.get("txn_type") == "purchase"]
            known = known or bool(prev)
            findings.append({"claim": f"Compras previas del cliente en {txn['merchant_name']} en 90 días: {len(prev)}",
                             "evidence_ids": [tid] + [h["txn_id"] for h in prev[:3]]})
        if risk:
            fs = risk.get("fraud_score")
            rule = bool(risk.get("rule_fired"))
            parts = [f"fraud_score {fs if fs is not None else 'sin dato'} (umbral {FRAUD_THRESHOLD:g})"]
            if feats:
                if feats.get("first_time_merchant"):
                    parts.append("primera vez en este comercio")
                if feats.get("amount_vs_cat_p95") is not None:
                    parts.append(f"monto/p95 de la categoría {feats['amount_vs_cat_p95']:.2f}")
                if feats.get("velocity_24h"):
                    parts.append(f"{int(feats['velocity_24h'])} cargos en las 24 h previas")
                if feats.get("foreign"):
                    parts.append("país distinto al del cliente")
                if feats.get("login_before_30m"):
                    parts.append("inicio de sesión en los 30 min previos")
                if feats.get("new_device_before"):
                    parts.append("dispositivo nuevo antes del cargo")
            findings.append({"claim": "Evidencia de riesgo: " + "; ".join(parts), "evidence_ids": [risk["evidence_id"]]})
            if rule:
                score["fraud"] += 3.0
            if feats.get("first_time_merchant"):
                score["fraud"] += 0.8
                known = False
            if (feats.get("amount_vs_cat_p95") or 0) > 1.0:
                score["fraud"] += 0.6
            if feats.get("foreign"):
                score["fraud"] += 0.7
            if feats.get("new_device_before"):
                score["fraud"] += 1.0
            if feats.get("unusual_hour"):
                score["fraud"] += 0.3
            if (feats.get("velocity_24h") or 0) >= 3:
                score["fraud"] += 0.5
        if known:
            score["forgotten_purchase"] += 1.2
            score["unfamiliar_merchant_name"] += 0.4
        merch = self.results.get("get_merchant_stats")
        if merch:
            findings.append({"claim": f"{merch['merchant_name']}: comercio con {merch['n_customers']} clientes y "
                                      f"{merch['n_txns']} transacciones en el banco (agregado; tasa de fraude "
                                      f"{100 * (merch.get('fraud_rate') or 0):.2f}%)", "evidence_ids": [merch["evidence_id"]]})
            if (merch.get("n_customers") or 0) >= 20:
                score["unfamiliar_merchant_name"] += 0.3
        sess = (self.results.get("get_digital_sessions") or {}).get("sessions", [])
        new_dev = [s for s in sess if s.get("first_seen_device") and s.get("has_login")]
        if new_dev:
            findings.append({"claim": "Inicio de sesión desde un dispositivo nuevo cerca del cargo",
                             "evidence_ids": [s["evidence_id"] for s in new_dev]})
            score["fraud"] += 0.6
        contacts = self.results.get("get_prior_contacts") or {}
        complaints = [c for c in contacts.get("contacts", []) if c.get("contact_type") == "complaint"]
        if complaints:
            findings.append({"claim": f"Reclamos previos en los últimos 12 meses: {len(complaints)}",
                             "evidence_ids": [c["evidence_id"] for c in complaints]})
        cards = (self.results.get("get_cards") or {}).get("cards", [])
        card = next((k for k in cards if k.get("card_last4") == txn.get("card_last4")), None)
        if card:
            findings.append({"claim": f"Tarjeta •••• {card['card_last4']} en estado {card['status']}",
                             "evidence_ids": [card["evidence_id"]]})
        if case.subcategory == "unrecognized_charge" and not known and merchant_checked:
            score["fraud"] += 0.4
        self.merchant_known = known if merchant_checked else None
        return self._report(findings, score, questions, lang, scenario, draft_vars, txn=txn, card=card)

    def _report(self, findings, score, questions, lang, scenario, draft_vars, txn, card=None) -> dict:
        weights = {h: PRIORS[h] * math.exp(score[h]) for h in HYPOTHESES}
        z = sum(weights.values())
        probs = {h: round(w / z, 3) for h, w in weights.items()}
        hyps = sorted(({"name": h, "p": p} for h, p in probs.items()), key=lambda x: (-x["p"], x["name"]))
        top, p_top = hyps[0]["name"], hyps[0]["p"]
        if txn is None:
            rec, key = "request_information", "generic"
        elif scenario == "explained" or (top == "pending_reversal" and p_top >= 0.35):
            rec, key = "explain_and_close", "explained"
        elif top == "duplicate":
            rec = "reverse_fee" if txn.get("txn_type") == "fee" else "open_chargeback"
            key = "duplicate_not_reversed"
        elif top == "fee_error":
            rec, key = "reverse_fee", "fee_off_schedule"
        elif scenario == "purchase":
            rec, key = "request_information", "purchase_amount_disputed"
        elif top == "fraud" and p_top >= 0.5:
            rec = "block_card" if card and card.get("status") == "active" else "open_chargeback"
            key = "unrecognized_new_merchant"
        elif top == "fraud":
            rec, key = "open_chargeback", "unrecognized_new_merchant"
        elif top in ("forgotten_purchase", "unfamiliar_merchant_name"):
            known = getattr(self, "merchant_known", None)
            rec = "request_information"
            # the drafts state whether there were earlier purchases: only with that evidence
            key = {True: "unrecognized_known_merchant", False: "unrecognized_new_merchant"}.get(known, "generic")
        else:
            rec, key = "escalate", "generic"
        if p_top < 0.30 and rec not in ("reverse_fee", "explain_and_close"):
            rec = "escalate"
        questions.append(OPEN_QUESTIONS.get(scenario if scenario == "purchase" else top, OPEN_QUESTIONS["fraud"]))
        tkey = f"investigator.draft.{key}"
        if key != "generic" and not {"amount", "merchant", "date"} <= set(draft_vars):
            tkey = "investigator.draft.generic"
        if tkey == "investigator.draft.fee_off_schedule" and "fee_amount" not in draft_vars:
            tkey = "investigator.draft.generic"
        text = render(tkey, lang, self.case.country, **draft_vars)
        return {"findings": findings, "hypotheses": hyps, "recommendation": rec, "confidence": round(p_top, 2),
                "draft_reply": {"language": lang, "text": text}, "open_questions": questions,
                "_fallback_text": render("investigator.draft.generic", lang, self.case.country, **draft_vars)}


def _iso(d: Any) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)
