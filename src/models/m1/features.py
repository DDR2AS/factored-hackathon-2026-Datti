"""M1 features: one vector per (what the customer said, candidate charge). Standard library only.

The same function builds the training matrix (``models/m1/train.py``) and scores candidates at run
time, so training and serving can't drift. Inputs follow INTERFACES.md #4: ``slots`` =
``{amount, currency, date, date_is_relative, merchant_text, card_last4}`` (any may be None) and
candidates as gateway ``Txn`` objects or mappings with at least txn_id, amount, currency,
local_date, merchant_name (merchant_category, card_last4, status, txn_type optional).
Missing values are NaN (LightGBM learns where they go).
"""

from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from models.common import fold, to_usd

NAN = float("nan")

FEATURES = (
    "amount_log_gap", "amount_rel_gap", "amount_missing", "said_is_round", "cand_rounds_to_said", "currency_mismatch",
    "day_gap", "day_gap_signed", "date_missing", "date_is_relative",
    "merchant_sim", "merchant_word_hit", "category_hint_match", "category_hint_present", "merchant_missing",
    "last4_match", "last4_missing",
    "is_fee", "is_purchase", "is_cash", "is_reversed", "is_pending",
    "amount_rank", "amount_gap_minus_best", "date_rank", "merchant_rank", "n_candidates", "days_before_latest",
)

# Category families: gold categories (food, services, ...) and demo categories (groceries, fuel, ...)
FAMILY = {
    "food": "food", "groceries": "food", "restaurants": "food", "convenience": "food", "bakery": "food",
    "delivery": "food",
    "health": "health", "pharmacy": "health",
    "transport": "transport", "fuel": "transport", "taxi": "transport",
    "entertainment": "entertainment", "streaming": "entertainment",
    "services": "services", "utilities": "services", "telecom": "services", "payment": "services",
    "other": "retail", "retail": "retail", "home": "retail", "electronics": "retail", "online": "retail",
    "bank_fee": "fee", "fee": "fee",
    "cash": "cash", "transfer": "transfer", "adjustment": "adjustment",
}
# What customers say for each family (ES and PT, folded: no accents, lowercase).
HINTS = {
    "food": ("super", "supermercado", "mercado", "tienda", "almacen", "abarrotes", "restaurante", "resto", "comida",
             "cafe", "cafeteria", "panaderia", "padaria", "lanchonete", "mercearia", "oxxo", "kiosco", "delivery",
             "domicilio", "rappi", "ifood", "parrilla", "taqueria", "pizzeria", "almoco", "cena", "almuerzo"),
    "health": ("farmacia", "drogueria", "clinica", "medico", "optica", "hospital", "laboratorio", "consulta"),
    "transport": ("gasolina", "gasolinera", "nafta", "combustible", "posto", "taxi", "uber", "bus", "peaje",
                  "pedagio", "estacion", "transporte", "metro", "colectivo", "estacionamiento"),
    "entertainment": ("cine", "cinema", "streaming", "netflix", "teatro", "parque", "juego", "spotify", "show",
                      "entrada", "ingresso", "concierto", "suscripcion", "assinatura"),
    "services": ("luz", "agua", "telefono", "celular", "internet", "cable", "servicio", "energia", "gas", "tv",
                 "factura", "conta", "plan"),
    "retail": ("online", "ferreteria", "electro", "tecnologia", "ropa", "libreria", "papeleria", "tecno",
               "tienda online", "loja", "compra online", "amazon", "mercado libre"),
    "fee": ("comision", "cuota", "manejo", "anualidad", "mantenimiento", "tarifa", "taxa", "manutencao", "anuidade",
            "comissao", "cargo por servicio"),
    "cash": ("retiro", "cajero", "saque", "efectivo", "atm", "caixa"),
    "transfer": ("transferencia", "transferi", "pix", "giro", "envio"),
}
_STOP = set("el la los las de del en un una unos unas me mi y o a al que con por para lo le se su como "
            "o os as do da dos das em no na um uma meu minha e com pelo pela cobro cobraron cargo compra "
            "pago pague paguei cobraram reconozco reconheco nao no si sim fue foi algo".split())


def _get(c: Any, name: str) -> Any:
    return c.get(name) if isinstance(c, Mapping) else getattr(c, name, None)


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(Decimal(str(v)))
    except (InvalidOperation, ValueError):
        return None


def _date(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in fold(text).split():
        padded = f" {word} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def merchant_similarity(said: str, merchant_name: str) -> float:
    """Share of the trigrams of what the customer said that appear in the merchant name (0..1)."""
    q = _trigrams(said)
    if not q:
        return 0.0
    return len(q & _trigrams(merchant_name)) / len(q)


def _words(text: str) -> list[str]:
    return [w for w in fold(text).split() if len(w) >= 3 and w not in _STOP]


def _word_hit(said: str, merchant: str) -> float:
    m_words = fold(merchant).split()
    for w in _words(said):
        for m in m_words:
            if m.startswith(w) or w.startswith(m) and len(m) >= 4:
                return 1.0
    return 0.0


def said_families(said: str | None) -> set[str]:
    f = f" {fold(said)} "
    out = set()
    for fam, words in HINTS.items():
        if any(f" {w}" in f for w in words):
            out.add(fam)
    return out


def _precision(amount: float) -> float:
    """Rounding the customer used: 450 -> 10, 52000 -> 1000, 449.9 -> 0.1, 449.95 -> 0.01."""
    if abs(amount - round(amount)) > 1e-9:
        cents = round(amount * 100)
        return 0.1 if cents % 10 == 0 else 0.01
    n = int(round(amount))
    p = 1
    while n and n % (p * 10) == 0 and p < 10**6:
        p *= 10
    return float(p)


def _rank(values: list[float], reverse: bool = False) -> list[float]:
    """Average rank (0 = best) ignoring NaN (NaN gets NaN)."""
    idx = [i for i, v in enumerate(values) if not math.isnan(v)]
    order = sorted(idx, key=lambda i: values[i], reverse=reverse)
    out = [NAN] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def build(slots: Mapping[str, Any] | None, candidates: list[Any]) -> list[list[float]]:
    """Feature vectors (order = ``FEATURES``) for every candidate of one query."""
    slots = slots or {}
    said_amount = _num(slots.get("amount"))
    said_cur = (slots.get("currency") or "").upper() or None
    said_date = _date(slots.get("date"))
    said_merchant = slots.get("merchant_text") or None
    said_last4 = str(slots.get("card_last4")) if slots.get("card_last4") else None
    families = said_families(said_merchant) if said_merchant else set()
    rel = 1.0 if slots.get("date_is_relative") else 0.0

    rows: list[list[float]] = []
    gaps, dgaps, msims = [], [], []
    dates = [_date(_get(c, "local_date")) for c in candidates]
    latest = max((d for d in dates if d), default=None)
    for c, cdate in zip(candidates, dates):
        amount = _num(_get(c, "amount"))
        amount = abs(amount) if amount is not None else None
        cur = (_get(c, "currency") or "").upper()
        merchant = _get(c, "merchant_name") or ""
        fam = FAMILY.get((_get(c, "merchant_category") or "").lower(), "retail")
        txn_type = (_get(c, "txn_type") or "purchase").lower()
        status = (_get(c, "status") or "approved").lower()
        # amount
        if said_amount and amount:
            if said_cur and cur and said_cur != cur:
                a, b = to_usd(said_amount, said_cur), to_usd(amount, cur)
                mismatch = 1.0
            else:
                a, b, mismatch = said_amount, amount, 0.0
            if a and b:
                log_gap = abs(math.log(a / b))
                rel_gap = abs(a - b) / b
            else:
                log_gap = rel_gap = NAN
            p = _precision(said_amount)
            rounds = 1.0 if abs(round(amount / p) * p - said_amount) < 1e-6 else 0.0
            is_round = 1.0 if p >= 10 else 0.0
            amissing = 0.0
        else:
            log_gap = rel_gap = rounds = is_round = mismatch = NAN
            amissing = 1.0
        # date
        if said_date and cdate:
            signed = float((said_date - cdate).days)
            dgap = abs(signed)
            dmissing = 0.0
        else:
            signed = dgap = NAN
            dmissing = 1.0
        # merchant
        if said_merchant:
            msim = merchant_similarity(said_merchant, merchant)
            whit = _word_hit(said_merchant, merchant)
            chint = 1.0 if fam in families else 0.0
            cpresent = 1.0 if families else 0.0
            mmissing = 0.0
        else:
            msim = whit = chint = cpresent = NAN
            mmissing = 1.0
        # card
        c4 = _get(c, "card_last4")
        if said_last4:
            l4 = 1.0 if (c4 and str(c4) == said_last4) else 0.0
            l4missing = 0.0
        else:
            l4, l4missing = NAN, 1.0
        days_before = float((latest - cdate).days) if latest and cdate else NAN
        rows.append([log_gap, rel_gap, amissing, is_round, rounds, mismatch,
                     dgap, signed, dmissing, rel,
                     msim, whit, chint, cpresent, mmissing,
                     l4, l4missing,
                     1.0 if txn_type == "fee" else 0.0, 1.0 if txn_type == "purchase" else 0.0,
                     1.0 if txn_type == "withdrawal" else 0.0, 1.0 if status == "reversed" else 0.0,
                     1.0 if status == "pending" else 0.0,
                     NAN, NAN, NAN, NAN, float(len(candidates)), days_before])
        gaps.append(log_gap)
        dgaps.append(dgap)
        msims.append(msim)
    arank, drank, mrank = _rank(gaps), _rank(dgaps), _rank(msims, reverse=True)
    finite = [g for g in gaps if not math.isnan(g)]
    best = min(finite) if finite else NAN
    i_ar, i_ag, i_dr, i_mr = FEATURES.index("amount_rank"), FEATURES.index("amount_gap_minus_best"), \
        FEATURES.index("date_rank"), FEATURES.index("merchant_rank")
    for i, row in enumerate(rows):
        row[i_ar] = arank[i]
        row[i_ag] = NAN if math.isnan(gaps[i]) or math.isnan(best) else gaps[i] - best
        row[i_dr] = drank[i]
        row[i_mr] = mrank[i]
    return rows


def slots_present(slots: Mapping[str, Any] | None) -> int:
    slots = slots or {}
    return sum(1 for k in ("amount", "date", "merchant_text", "card_last4") if slots.get(k))


_WORD = re.compile(r"[a-z0-9]+")
