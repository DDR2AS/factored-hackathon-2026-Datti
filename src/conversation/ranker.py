"""Provisional M1 charge ranker (INTERFACES.md #4, owner Cristhian). Rule, not a model.

Stand-in with the #4 signature until Cristhian's M1 v1 arrives in ``src/models``. It scores
each candidate with the v1.4 rule and turns the scores into probabilities:

    penalty = amount_term + date_term + merchant_term      (0 = perfect match)
    amount_term   = min(|ln(said / charged)| / AMOUNT_SCALE, CAP)   both in USD if the
                    customer named a currency, else in the charge's own currency
    date_term     = min(|days between| / DATE_SCALE, CAP)
    merchant_term = (1 - trigram containment of the said text in the merchant name) * MERCHANT_WEIGHT
    (+ CAP if the customer named card digits and the charge is on another card; addition
     to the v1.4 rule)
    p_i = exp(-penalty_i) / (sum_j exp(-penalty_j) + exp(-NONE_PENALTY))

A slot the customer did not give contributes 0 to every candidate. The ``NONE_PENALTY``
term is the "none of these" option: it keeps p low when nothing fits, and it is why the
returned p values sum to less than 1. Bands use the thresholds below; Cristhian's definitive
model reads them from ``config/thresholds.yaml``.

Known limit (for the orchestrator): two identical charges (a duplicate, Andrés the demo
customer) share the probability, so the band is "choose"; detect that case with
``find_duplicates`` instead of asking the customer to pick one.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from conversation.fx import to_usd

MODEL_VERSION = "m1-rule-0"

# Bands (#4). The definitive M1 reads these from config/thresholds.yaml (Cristhian).
CONFIRM_THRESHOLD = 0.85  # p_top1 >= 0.85 -> "confirm": show one charge, "¿Es este?"
CHOOSE_THRESHOLD = 0.40   # 0.40 <= p_top1 < 0.85 -> "choose": show the top candidates
                          # p_top1 < 0.40 -> "ask": ask for another detail

AMOUNT_SCALE = 0.02     # a 2% difference costs one unit
DATE_SCALE = 1.0        # one day costs one unit
MERCHANT_WEIGHT = 3.0   # no trigram in common costs three units
CAP = 6.0               # per term, so one wrong detail does not erase the others
NONE_PENALTY = 4.0      # the "none of these" option


def _get(c: Any, name: str) -> Any:
    return c.get(name) if isinstance(c, Mapping) else getattr(c, name, None)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def normalize_merchant(text: str) -> str:
    """Lowercase, strip accents, keep letters and digits separated by single spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in normalize_merchant(text).split():
        padded = f" {word} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def merchant_similarity(said: str, merchant_name: str) -> float:
    """Share of the trigrams of what the customer said that appear in the merchant name
    (containment, 0..1). "super" vs "SUPER AHORRO SA" = 1.0; vs "OXXO 1123" = 0.0."""
    q = _trigrams(said)
    if not q:
        return 0.0
    return len(q & _trigrams(merchant_name)) / len(q)


def _penalty(slots: Mapping[str, Any], c: Any) -> float:
    total = 0.0
    said_amount = _dec(slots.get("amount"))
    charged = _dec(_get(c, "amount"))
    if said_amount is not None and charged is not None and said_amount != 0 and charged != 0:
        said_cur = slots.get("currency")
        cur = _get(c, "currency")
        if said_cur and cur and said_cur.upper() != cur.upper():
            a, b = to_usd(abs(said_amount), said_cur), to_usd(abs(charged), cur)
        else:
            a, b = abs(said_amount), abs(charged)
        if a and b:
            total += min(abs(math.log(float(a) / float(b))) / AMOUNT_SCALE, CAP)
        else:
            total += CAP
    said_date = _date(slots.get("date"))
    txn_date = _date(_get(c, "local_date"))
    if said_date is not None and txn_date is not None:
        total += min(abs((said_date - txn_date).days) / DATE_SCALE, CAP)
    said_merchant = slots.get("merchant_text")
    if said_merchant:
        total += (1.0 - merchant_similarity(said_merchant, _get(c, "merchant_name") or "")) * MERCHANT_WEIGHT
    last4 = slots.get("card_last4")
    if last4 and _get(c, "card_last4") and str(last4) != str(_get(c, "card_last4")):
        total += CAP
    return total


def band_for(p_top1: float | None) -> str:
    """Band for the top probability: "confirm", "choose" or "ask" (None -> "ask")."""
    if p_top1 is None:
        return "ask"
    if p_top1 >= CONFIRM_THRESHOLD:
        return "confirm"
    if p_top1 >= CHOOSE_THRESHOLD:
        return "choose"
    return "ask"


def rank_candidates(slots: Mapping[str, Any] | None, candidates: list[Any]) -> dict[str, Any]:
    """Rank the candidate charges for what the customer said.

    slots: #4 slots ``{amount, currency, date, date_is_relative, merchant_text, card_last4}``,
        any may be None; amount as decimal string, date as ISO string or ``date``.
    candidates: ``Txn`` objects or mappings with at least txn_id, amount, currency,
        local_date, merchant_name (card_last4 optional).
    Returns ``{"ranked": [{"txn_id", "p"}], "band", "model_version"}``: every candidate,
    highest p first (ties by txn_id), p rounded to 4 decimals. No candidates -> ranked [] and
    band "ask". Pure: no I/O, no randomness.
    """
    slots = slots or {}
    if not candidates:
        return {"ranked": [], "band": "ask", "model_version": MODEL_VERSION}
    scored = [(str(_get(c, "txn_id")), math.exp(-_penalty(slots, c))) for c in candidates]
    z = sum(w for _, w in scored) + math.exp(-NONE_PENALTY)
    ranked = sorted(({"txn_id": t, "p": round(w / z, 4)} for t, w in scored),
                    key=lambda r: (-r["p"], r["txn_id"]))
    return {"ranked": ranked, "band": band_for(ranked[0]["p"]), "model_version": MODEL_VERSION}
