"""Demo backend of the gateway tools (INTERFACES.md #2). SYNTHETIC DATA ONLY.

Owner of #2 is Andrés. This module is Arturo's PROPOSAL to Andrés for a `demo` backend that
lets M1 deploy without DynamoDB: the six judge-mode customers live in
``demo/customers.yaml`` (committed, packaged with src/, never in data/ and never JSON).
Andrés decides whether it becomes ``src/gateway`` backend ``demo`` or is loaded into the
Demo table. Function names, arguments and errors follow #2; callers must not depend on
anything else here.

Rules this module enforces:
- Every tool takes ``ctx: GatewayContext`` first. The server builds the context from the
  verified session (``customer_id_for_demo_key``); never from message text or arguments.
- Tools only return records owned by ``ctx.customer_id``. A txn or card of another
  customer raises ``NotOwned``; an unknown id raises ``NotFound``.
- ``block_card`` refuses unless ``confirmed_by_customer`` is True and returns the read-back
  status. Block state lives in process memory, per session, so two judges using the same
  demo customer do not see each other's blocks. Nothing here moves money.
- ``set_tool_failure`` makes every tool raise ``ToolUnavailable`` (for tests and for
  ``local_api.py --fail tools``).
- Calls are not traced here: the orchestrator writes each call to the trace (#7).

Deviations from #2, all proposals to Andrés: ``Txn`` adds ``txn_type`` (purchase|fee|
reversal) and ``reversal_of``; ``get_customer_profile`` does not exist in #2;
``fee_item_for_txn`` is a pure helper, not a tool. ``get_risk_evidence``,
``get_digital_sessions`` and ``get_merchant_stats`` are not implemented in the demo.
"""

from __future__ import annotations

import random
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from conversation.fx import to_usd

DATA_PATH = Path(__file__).parent / "demo" / "customers.yaml"


# ---------------------------------------------------------------- errors

class GatewayError(Exception):
    """Base class of the #2 errors."""


class NotOwned(GatewayError):
    """The record exists but belongs to another customer. The API answers 403 as if absent."""


class NotFound(GatewayError):
    """No such record."""


class ToolUnavailable(GatewayError):
    """The data source failed. Callers retry at most twice, then take the safe path (lane C)."""


class NotConfirmed(GatewayError):
    """``block_card`` was called without the customer's explicit yes."""


# ---------------------------------------------------------------- types

@dataclass(frozen=True)
class GatewayContext:
    """Who is asking. Built ONLY by the server from a verified session, never from text.

    customer_id: owner of every record the tools may return (server-side, never sent out).
    session_id: scopes demo state (card blocks, failure switch).
    trace_id: lets the caller tie tool calls to the trace (#7).
    """

    customer_id: str
    session_id: str
    trace_id: str
    # "customer" (the chat and the lifecycle of its case) or "analyst" (the console reading
    # a case). The per-session judge switch ``tools_down`` belongs to the customer's chat: it
    # never empties what the analyst sees. The process-wide ``--fail tools`` applies to both.
    actor: str = "customer"

    @classmethod
    def for_analyst(cls, customer_id: str, session_id: str, trace_id: str) -> "GatewayContext":
        """Context of the analyst console for a stored case (PROPOSAL to Andrés): owner and
        session come from the stored case, never from the request."""
        return cls(customer_id=customer_id, session_id=session_id, trace_id=trace_id, actor="analyst")


@dataclass(frozen=True)
class Txn:
    """A transaction with the #2 fields plus ``txn_type`` and ``reversal_of`` (proposal)."""

    txn_id: str
    product_id: str
    card_last4: str | None
    local_date: date
    local_time: str
    amount: Decimal
    currency: str
    amount_usd: Decimal | None
    merchant_id: str
    merchant_name: str
    merchant_category: str
    status: str
    response_code: str
    fraud_score: float | None
    txn_type: str = "purchase"
    reversal_of: str | None = None
    synthetic: bool = True

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict: dates ISO, money as decimal strings (the #3 evidence format)."""
        d = asdict(self)
        d["local_date"] = self.local_date.isoformat()
        d["amount"] = str(self.amount)
        d["amount_usd"] = str(self.amount_usd) if self.amount_usd is not None else None
        return d


@dataclass(frozen=True)
class FeeItem:
    fee_code: str
    merchant_id: str
    description: str
    amount: Decimal
    frequency: str


@dataclass(frozen=True)
class FeeRule:
    """Synthetic fee schedule of one product (#2 ``get_fee_schedule``)."""

    product_id: str
    product_name: str
    currency: str
    fees: tuple[FeeItem, ...]
    synthetic: bool = True


@dataclass(frozen=True)
class Baseline:
    """Usual behaviour of the customer, from purchases older than 14 days before the
    reference date (so the disputed recent charges do not define "normal")."""

    window_from: date | None
    window_to: date
    n_txns: int
    currency: str
    median_amount: Decimal | None
    p90_amount: Decimal | None
    top_categories: tuple[str, ...]
    top_merchants: tuple[str, ...]
    usual_hours: tuple[int, int] | None
    synthetic: bool = True


@dataclass(frozen=True)
class Contact:
    contact_id: str
    date: date
    channel: str
    contact_type: str  # complaint | inquiry
    subcategory: str | None
    status: str
    synthetic: bool = True


@dataclass(frozen=True)
class Card:
    card_id: str
    product_id: str
    card_last4: str
    status: str  # active | blocked
    synthetic: bool = True


@dataclass(frozen=True)
class CardStatus:
    """Read-back after ``block_card``: the state as stored, not as requested."""

    card_id: str
    card_last4: str
    status: str
    changed_at: datetime | None
    synthetic: bool = True


@dataclass(frozen=True)
class CustomerProfile:
    """Server-side profile (proposal to Andrés; not in #2). First name only, no PII."""

    display_name: str
    language: str
    locale: str
    country: str
    currency: str
    segment: str
    reference_date: date
    products: tuple[dict, ...]
    synthetic: bool = True


@dataclass
class _Customer:
    key: str
    customer_id: str
    profile: CustomerProfile
    txns: list[Txn]
    cards: list[Card]
    fees: dict[str, FeeRule]
    contacts: list[Contact]
    demonstrates: str = ""
    by_id: dict[str, Txn] = field(default_factory=dict)


@dataclass
class _Demo:
    version: str
    data_cutoff: date
    customers: dict[str, _Customer]  # by customer_id
    keys: dict[str, str]  # demo_key -> customer_id
    txn_owner: dict[str, str]  # txn_id -> customer_id
    card_owner: dict[str, str]  # card_id -> customer_id


# ---------------------------------------------------------------- loading

def _money(value) -> Decimal:
    return Decimal(str(value))


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _txn_from_row(row: dict, cust: dict, products: dict[str, dict]) -> Txn:
    product_id = row.get("product_id") or cust["products"][0]["product_id"]
    amount = _money(row["amount"])
    currency = row.get("currency") or products[product_id]["currency"]
    return Txn(
        txn_id=row["txn_id"],
        product_id=product_id,
        card_last4=products[product_id].get("last4"),
        local_date=_as_date(row["date"]),
        local_time=str(row.get("time", "12:00")),
        amount=amount,
        currency=currency,
        amount_usd=to_usd(amount, currency),
        merchant_id=row["merchant_id"],
        merchant_name=row["merchant_name"],
        merchant_category=row.get("category", "other"),
        status=row.get("status", "approved"),
        response_code=row.get("response_code", "00"),
        fraud_score=float(row["fraud_score"]) if row.get("fraud_score") is not None else None,
        txn_type=row.get("txn_type", "purchase"),
        reversal_of=row.get("reversal_of"),
    )


def _background(key: str, cust: dict, scenario: list[Txn], products: dict[str, dict]) -> list[Txn]:
    """Deterministic ordinary purchases (seeded RNG); never close to a scenario charge."""
    spec = cust.get("background") or {}
    if not spec:
        return []
    rng = random.Random(spec["seed"])
    d0, d1 = _as_date(spec["date_from"]), _as_date(spec["date_to"])
    lo, hi = float(spec["amount_min"]), float(spec["amount_max"])
    span = (d1 - d0).days
    prefix = scenario[0].txn_id.rsplit("-", 1)[0] if scenario else f"TX-{key.upper()[:3]}"
    product = cust["products"][0]
    out: list[Txn] = []
    while len(out) < spec["count"]:
        name, merchant_id, category = rng.choice(spec["merchants"])
        day = d0 + timedelta(days=rng.randint(0, span))
        amount = Decimal(str(round(rng.uniform(lo, hi), 2 if hi < 10000 else 0))).quantize(Decimal("0.01"))
        hh, mm = rng.randint(7, 22), rng.randint(0, 59)
        fraud = rng.randint(0, 12)
        near = any(
            abs((day - s.local_date).days) <= 3 and abs(amount - abs(s.amount)) <= abs(s.amount) * Decimal("0.15")
            for s in scenario
        )
        if near:
            continue
        out.append(_txn_from_row(
            {"txn_id": f"{prefix}-{1001 + len(out)}", "date": day, "time": f"{hh:02d}:{mm:02d}",
             "merchant_name": name, "merchant_id": merchant_id, "category": category,
             "amount": amount, "fraud_score": fraud, "product_id": product["product_id"]},
            cust, products))
    return out


@lru_cache(maxsize=None)
def _load(path: Path = DATA_PATH) -> _Demo:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw.get("synthetic") is not True:
        raise ValueError("demo data must be marked synthetic: true")
    cutoff = _as_date(raw["data_cutoff"])
    customers: dict[str, _Customer] = {}
    keys: dict[str, str] = {}
    txn_owner: dict[str, str] = {}
    card_owner: dict[str, str] = {}
    for key, c in raw["customers"].items():
        products = {p["product_id"]: p for p in c["products"]}
        scenario = [_txn_from_row(r, c, products) for r in c.get("transactions", [])]
        txns = sorted(scenario + _background(key, c, scenario, products),
                      key=lambda t: (t.local_date, t.local_time, t.txn_id))
        dated = [t.local_date for t in txns if t.local_date <= cutoff]
        ref = max(dated) if dated else cutoff
        profile = CustomerProfile(
            display_name=c["display_name"], language=c["language"], locale=c["locale"],
            country=c["country"], currency=c["currency"], segment=c.get("segment", ""),
            reference_date=ref,
            products=tuple({k: p[k] for k in ("product_id", "kind", "name", "currency", "last4")}
                           for p in c["products"]),
        )
        fees = {
            pid: FeeRule(
                product_id=pid, product_name=products[pid]["name"], currency=products[pid]["currency"],
                fees=tuple(FeeItem(fee_code=i["fee_code"], merchant_id=i["merchant_id"],
                                   description=i["description"], amount=_money(i["amount"]),
                                   frequency=i["frequency"]) for i in items),
            )
            for pid, items in (c.get("fees") or {}).items()
        }
        cards = [Card(card_id=k["card_id"], product_id=k["product_id"], card_last4=str(k["card_last4"]),
                      status=k.get("status", "active")) for k in c.get("cards", [])]
        contacts = [Contact(contact_id=x["contact_id"], date=_as_date(x["date"]), channel=x["channel"],
                            contact_type=x["contact_type"], subcategory=x.get("subcategory"),
                            status=x["status"]) for x in c.get("prior_contacts") or []]
        cust = _Customer(key=key, customer_id=c["customer_id"], profile=profile, txns=txns,
                         cards=cards, fees=fees, contacts=contacts,
                         demonstrates=c.get("demonstrates", ""), by_id={t.txn_id: t for t in txns})
        if cust.customer_id in customers or len(cust.by_id) != len(txns):
            raise ValueError(f"duplicate customer or txn id in demo data ({key})")
        customers[cust.customer_id] = cust
        keys[key] = cust.customer_id
        for t in txns:
            if t.txn_id in txn_owner:
                raise ValueError(f"txn id {t.txn_id} used by two customers")
            txn_owner[t.txn_id] = cust.customer_id
        for k in cards:
            card_owner[k.card_id] = cust.customer_id
    return _Demo(version=str(raw["version"]), data_cutoff=cutoff, customers=customers, keys=keys,
                 txn_owner=txn_owner, card_owner=card_owner)


# ---------------------------------------------------------------- demo state (in memory)

_lock = threading.Lock()
_blocks: dict[tuple[str, str], datetime] = {}  # (session_id, card_id) -> blocked_at
_fail_all = False
_fail_sessions: set[str] = set()


def set_tool_failure(session_id: str | None, on: bool) -> None:
    """Turn the tool-failure switch on or off.

    session_id=None switches the whole process (``local_api.py --fail tools``); a session id
    switches only that session. While on, every tool raises ``ToolUnavailable``.
    """
    global _fail_all
    with _lock:
        if session_id is None:
            _fail_all = on
        elif on:
            _fail_sessions.add(session_id)
        else:
            _fail_sessions.discard(session_id)


def tool_failure_on(session_id: str | None) -> bool:
    """Whether tools fail for this session (its own switch or the process-wide one)."""
    with _lock:
        return _fail_all or (session_id is not None and session_id in _fail_sessions)


def reset_demo_state(session_id: str | None = None) -> None:
    """Forget card blocks and failure switches: of one session, or of all when None.

    Tests and server restarts only. The judge switch ``expire_session`` does NOT call this: a
    card blocked with the customer's yes stays blocked after the session ends (SEC-01)."""
    global _fail_all
    with _lock:
        if session_id is None:
            _blocks.clear()
            _fail_sessions.clear()
            _fail_all = False
        else:
            for k in [k for k in _blocks if k[0] == session_id]:
                del _blocks[k]
            _fail_sessions.discard(session_id)


def _customer(ctx: GatewayContext) -> _Customer:
    with _lock:
        failing = _fail_all or (ctx.actor != "analyst" and ctx.session_id in _fail_sessions)
    if failing:
        raise ToolUnavailable("demo gateway: failure switch is on")
    cust = _load().customers.get(ctx.customer_id)
    if cust is None:
        raise NotFound("customer")
    return cust


def _owned_txn(ctx: GatewayContext, txn_id: str) -> tuple[_Customer, Txn]:
    cust = _customer(ctx)
    txn = cust.by_id.get(txn_id)
    if txn is not None:
        return cust, txn
    if txn_id in _load().txn_owner:
        raise NotOwned("transaction")
    raise NotFound("transaction")


# ---------------------------------------------------------------- server-side helpers

DEMO_KEYS: tuple[str, ...] = ("lucia", "sofia", "andres", "joao", "martina", "carlos")


def customer_id_for_demo_key(demo_key: str) -> str:
    """Allow-list lookup for ``POST /session``. Raises NotFound for any other key.

    The returned customer_id stays on the server (session store, GatewayContext).
    """
    cid = _load().keys.get(demo_key)
    if cid is None or demo_key not in DEMO_KEYS:
        raise NotFound("demo_key")
    return cid


def profile_for_demo_key(demo_key: str) -> CustomerProfile:
    """Server-side helper for ``POST /session`` (not a #2 tool): first name, language,
    locale, country and reference date of an allow-listed demo customer. Not affected by
    the tool-failure switch, so a session can open while tools are down (the chat then
    shows the lane C path). Raises NotFound for any key outside the allow list."""
    return _load().customers[customer_id_for_demo_key(demo_key)].profile


def profile_for_customer_id(customer_id: str) -> CustomerProfile | None:
    """Server-side helper for the analyst console (not a #2 tool): the profile of the case's
    customer, looked up by the ``customer_id`` stored in the case (never from a request).
    First name only, no PII. Not affected by the failure switch. None if unknown."""
    cust = _load().customers.get(customer_id)
    return cust.profile if cust else None


def data_version() -> str:
    """Version string of demo/customers.yaml (goes into traces)."""
    return _load().version


def fee_item_for_txn(rule: FeeRule, txn: Txn) -> FeeItem | None:
    """Pure helper (not a #2 tool): the schedule entry a fee txn corresponds to, by
    merchant_id, or None if the txn is not a scheduled fee of that product. Whether the
    amount matches (``item.amount == txn.amount``) is the caller's evidence."""
    if txn.product_id != rule.product_id:
        return None
    return next((i for i in rule.fees if i.merchant_id == txn.merchant_id), None)


# ---------------------------------------------------------------- #2 tools

def find_candidate_txns(ctx: GatewayContext, date_from: date, date_to: date, amount_hint=None,
                        currency: str | None = None, limit: int = 200) -> list[Txn]:
    """Charges of the customer between date_from and date_to (inclusive) that could be the
    one they mean. Reversals are excluded (see ``get_reversals``). ``currency`` filters when
    given. ``amount_hint`` never filters; it only orders by closeness (else newest first).
    Returns at most ``limit`` txns. Raises ToolUnavailable, NotFound (unknown customer)."""
    cust = _customer(ctx)
    date_from, date_to = _as_date(date_from), _as_date(date_to)
    rows = [t for t in cust.txns
            if date_from <= t.local_date <= date_to and t.txn_type != "reversal"
            and (currency is None or t.currency == currency.upper())]
    if amount_hint is not None:
        hint = abs(_money(amount_hint))
        rows.sort(key=lambda t: (abs(abs(t.amount) - hint), -t.local_date.toordinal(), t.txn_id))
    else:
        rows.sort(key=lambda t: (t.local_date, t.local_time, t.txn_id), reverse=True)
    return rows[: max(0, int(limit))]


def get_transaction(ctx: GatewayContext, txn_id: str) -> Txn:
    """One txn of the customer. Raises NotOwned (another customer's), NotFound, ToolUnavailable."""
    return _owned_txn(ctx, txn_id)[1]


def get_txn_history(ctx: GatewayContext, days: int) -> list[Txn]:
    """All txns (reversals included) in the last ``days`` days up to the customer's
    reference date, newest first."""
    cust = _customer(ctx)
    ref = cust.profile.reference_date
    start = ref - timedelta(days=int(days))
    rows = [t for t in cust.txns if start < t.local_date <= ref]
    return sorted(rows, key=lambda t: (t.local_date, t.local_time, t.txn_id), reverse=True)


def find_duplicates(ctx: GatewayContext, txn_id: str) -> list[Txn]:
    """Other charges of the customer with the same merchant_id, amount and currency within
    24 hours of ``txn_id`` (reversals excluded). Empty list if none."""
    cust, txn = _owned_txn(ctx, txn_id)
    t0 = datetime.combine(txn.local_date, time.fromisoformat(txn.local_time))
    out = []
    for t in cust.txns:
        if t.txn_id == txn.txn_id or t.txn_type == "reversal":
            continue
        if (t.merchant_id, t.amount, t.currency) != (txn.merchant_id, txn.amount, txn.currency):
            continue
        t1 = datetime.combine(t.local_date, time.fromisoformat(t.local_time))
        if abs(t1 - t0) <= timedelta(hours=24):
            out.append(t)
    return out


def get_reversals(ctx: GatewayContext, txn_id: str) -> list[Txn]:
    """Reversal txns whose ``reversal_of`` is ``txn_id``. Empty list if not reversed."""
    cust, txn = _owned_txn(ctx, txn_id)
    return [t for t in cust.txns if t.txn_type == "reversal" and t.reversal_of == txn.txn_id]


def get_fee_schedule(ctx: GatewayContext, product_id: str) -> FeeRule:
    """Synthetic fee schedule of one of the customer's products.
    Raises NotOwned (another customer's product), NotFound, ToolUnavailable."""
    cust = _customer(ctx)
    rule = cust.fees.get(product_id)
    if rule is not None:
        return rule
    if any(product_id in c.fees for c in _load().customers.values()):
        raise NotOwned("product")
    raise NotFound("fee schedule")


def get_customer_baseline(ctx: GatewayContext) -> Baseline:
    """Usual spending: purchases older than 14 days before the reference date."""
    cust = _customer(ctx)
    ref = cust.profile.reference_date
    window_to = ref - timedelta(days=14)
    rows = [t for t in cust.txns if t.txn_type == "purchase" and t.local_date <= window_to]
    amounts = sorted(t.amount for t in rows)
    q = Decimal("0.01")

    def top(values: list[str]) -> tuple[str, ...]:
        counts: dict[str, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        return tuple(k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3])

    hours = sorted(int(t.local_time[:2]) for t in rows)
    return Baseline(
        window_from=min((t.local_date for t in rows), default=None),
        window_to=window_to,
        n_txns=len(rows),
        currency=cust.profile.currency,
        median_amount=Decimal(str(median(amounts))).quantize(q, ROUND_HALF_UP) if amounts else None,
        p90_amount=amounts[min(len(amounts) - 1, int(0.9 * len(amounts)))] if amounts else None,
        top_categories=top([t.merchant_category for t in rows]),
        top_merchants=top([t.merchant_name for t in rows]),
        usual_hours=(hours[len(hours) // 10], hours[min(len(hours) - 1, (9 * len(hours)) // 10)]) if hours else None,
    )


def get_prior_contacts(ctx: GatewayContext) -> list[Contact]:
    """Previous contacts, newest first. Count ``contact_type == "complaint"`` for
    ``Evidence.prior_complaints``."""
    cust = _customer(ctx)
    return sorted(cust.contacts, key=lambda c: c.date, reverse=True)


def get_cards(ctx: GatewayContext) -> list[Card]:
    """Cards of the customer with the status as seen by this session (blocks applied)."""
    cust = _customer(ctx)
    with _lock:
        blocked = {cid for (sid, cid) in _blocks if sid == ctx.session_id}
    return [Card(card_id=k.card_id, product_id=k.product_id, card_last4=k.card_last4,
                 status="blocked" if k.card_id in blocked else k.status) for k in cust.cards]


def block_card(ctx: GatewayContext, card_id: str, confirmed_by_customer: bool) -> CardStatus:
    """Simulated block. Refuses (NotConfirmed) unless ``confirmed_by_customer is True``,
    which the orchestrator sets only from the server-issued "yes" button. Idempotent.
    Returns the status read back from the store. There is no unblock. Nothing moves money.
    Raises NotConfirmed, NotOwned, NotFound, ToolUnavailable."""
    cust = _customer(ctx)
    card = next((k for k in cust.cards if k.card_id == card_id), None)
    if card is None:
        raise NotOwned("card") if card_id in _load().card_owner else NotFound("card")
    if confirmed_by_customer is not True:
        raise NotConfirmed("block_card needs the customer's explicit yes")
    with _lock:
        _blocks.setdefault((ctx.session_id, card_id), datetime.now(timezone.utc))
        changed_at = _blocks[(ctx.session_id, card_id)]
    current = next(k for k in get_cards(ctx) if k.card_id == card_id)
    return CardStatus(card_id=card_id, card_last4=current.card_last4, status=current.status,
                      changed_at=changed_at)


def get_customer_profile(ctx: GatewayContext) -> CustomerProfile:
    """PROPOSAL to Andrés (not in #2): first name, language, locale, country, currency,
    segment, products and the reference date for relative dates. No PII by construction."""
    return _customer(ctx).profile
