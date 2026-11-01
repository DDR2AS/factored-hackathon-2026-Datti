"""Backend ``dynamodb`` of the gateway tools (INTERFACES.md #2) over the serving table.

Same public surface as ``gateway.duckdb_backend.DuckDBGateway`` (tools, error classes and helpers
as attributes, failure switch and card blocks in ``gateway.state``), reading the items written by
``pipeline/serving.py`` into ``TABLE_SERVING``:

    pk = CUST#<customer_id>
    sk = PROFILE | PRODUCT#<product_id> | TXN#<local_date>#<txn_id> | BASELINE
         | CATBASE#<category> | FEE#<product_id>#<fee_code> | CONTACT#<date>#<contact_id>
    pk = MERCHANT#<merchant_id>, sk = STATS   (aggregates across the bank)

Scope and ownership
- Every read of customer data is a Query on ``pk = CUST#<ctx.customer_id>``: the partition key IS
  the scope, there is no Scan, and a record of another customer can never be returned (an IAM
  condition on ``dynamodb:LeadingKeys`` could enforce the same in the role).
- ``ctx.customer_id`` comes from the verified session or the stored case, never from text.
- A transaction id that is not in the caller's partition: the gateway asks the sparse KEYS_ONLY
  GSI ``by_txn`` (partition key ``txn_id``, only TXN items have it) who owns it. The index holds
  keys only, so the other customer's record is never read: the answer only decides between
  ``NotOwned`` (exists, someone else's: traced as ``notowned``, counted by the investigator as a
  refused query) and ``NotFound``. If the index is missing or fails, the answer is ``NotFound``;
  the API answers both with the same 403, so nothing leaks either way. Products and cards of
  other customers are ``NotFound`` (no index for them).
- Derived reversals: like the duckdb backend, a charge the data marks ``reversed`` gets a reversal
  record ``REV-<txn_id>`` (``origin=derived_from_status``); the serving export has no reversal rows.
- ``get_digital_sessions`` returns ``[]``: the serving export has no digital sessions yet, so M3
  sees no login/new-device signals in the cloud (they are evidence only; nothing decides on them).

Reads are cached per process: one paginated Query loads a customer's partition (~50-100 items)
and every tool answers from it for ``cache_seconds`` (300). The serving table is a read-only
snapshot (a new export is a new table, ``TABLE_SERVING``), so the cache never serves stale data
that the table has newer. AWS failures become ``ToolUnavailable`` (callers retry twice, then lane C).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from typing import Any, Callable

from gateway import state
from gateway.types import (
    Baseline, Card, CardStatus, Contact, CustomerProfile, DigitalSession, FeeItem, FeeRule, GatewayContext,
    GatewayError, MerchantStats, NotConfirmed, NotFound, NotOwned, RiskEvidence, ToolUnavailable, Txn,
    fee_item_for_txn,
)

FRAUD_SCORE_HIGH = 30.0
CONTACT_WINDOW_DAYS = 365
CANDIDATE_EXCLUDED_TYPES = ("reversal", "deposit")
DEFAULT_TXN_INDEX = "by_txn"
DEFAULT_CACHE_SECONDS = 300.0
DEFAULT_CACHE_SIZE = 256


def _d(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _i(value: Any) -> int:
    return int(value or 0)


@dataclass
class _Partition:
    """One customer's serving items, parsed."""

    profile: dict[str, Any] | None = None
    products: list[dict[str, Any]] = field(default_factory=list)
    txns: list[Txn] = field(default_factory=list)
    txn_attrs: dict[str, dict[str, Any]] = field(default_factory=dict)  # txn_id -> {channel, txn_country}
    baseline: dict[str, Any] | None = None
    catbase: list[dict[str, Any]] = field(default_factory=list)
    fees: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)


class DynamoDBGateway:
    """The #2 tools over the DynamoDB serving table. Thread safe."""

    name = "dynamodb"
    GatewayContext = GatewayContext
    GatewayError = GatewayError
    NotOwned = NotOwned
    NotFound = NotFound
    ToolUnavailable = ToolUnavailable
    NotConfirmed = NotConfirmed
    fee_item_for_txn = staticmethod(fee_item_for_txn)

    def __init__(self, table: str, client: Any = None, txn_index: str | None = DEFAULT_TXN_INDEX,
                 cache_seconds: float = DEFAULT_CACHE_SECONDS, cache_size: int = DEFAULT_CACHE_SIZE,
                 contact_window_days: int = CONTACT_WINDOW_DAYS, risk_fn: Any = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.table = table
        self._client = client
        self.txn_index = txn_index or None
        self.cache_seconds = max(0.0, float(cache_seconds))
        self.cache_size = max(1, int(cache_size))
        self.contact_window_days = contact_window_days
        self._risk_fn = risk_fn
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, _Partition | None]] = {}
        self.queries = 0  # partitions loaded (tests check the cache)

    @classmethod
    def from_env(cls, env: dict[str, str], client: Any = None) -> "DynamoDBGateway":
        table = (env.get("TABLE_SERVING") or "").strip()
        if not table:
            raise ValueError("GATEWAY_BACKEND=dynamodb needs TABLE_SERVING (DataStack.table_environment, #9)")
        raw_cache = env.get("SERVING_CACHE_SECONDS")
        try:
            cache = float(raw_cache) if raw_cache not in (None, "") else DEFAULT_CACHE_SECONDS
        except ValueError:
            cache = DEFAULT_CACHE_SECONDS
        index = env.get("SERVING_TXN_INDEX")
        return cls(table, client=client, txn_index=DEFAULT_TXN_INDEX if index is None else index, cache_seconds=cache)

    # ------------------------------------------------------------ AWS access

    def _ddb(self) -> Any:
        if self._client is None:
            from cloud import aws

            self._client = aws.client("dynamodb")
        return self._client

    def _query(self, **kwargs: Any) -> list[dict[str, Any]]:
        from boto3.dynamodb.types import TypeDeserializer

        des = TypeDeserializer()
        out: list[dict[str, Any]] = []
        kwargs = {"TableName": self.table, **kwargs}
        try:
            while True:
                page = self._ddb().query(**kwargs)
                out += [{k: des.deserialize(v) for k, v in it.items()} for it in page.get("Items", [])]
                if not page.get("LastEvaluatedKey"):
                    return out
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        except Exception as e:  # a broken source is a tool failure, never a crash
            raise ToolUnavailable(f"dynamodb gateway: {type(e).__name__}") from e

    def _partition(self, customer_id: str) -> _Partition | None:
        now = self._monotonic()
        with self._lock:
            hit = self._cache.get(customer_id)
            if hit is not None and now - hit[0] < self.cache_seconds:
                return hit[1]
        items = self._query(KeyConditionExpression="pk = :pk",
                            ExpressionAttributeValues={":pk": {"S": f"CUST#{customer_id}"}})
        part = self._parse(items) if items else None
        with self._lock:
            self.queries += 1
            if self.cache_seconds > 0:
                if len(self._cache) >= self.cache_size:
                    for k in sorted(self._cache, key=lambda k: self._cache[k][0])[: max(1, self.cache_size // 4)]:
                        self._cache.pop(k, None)
                self._cache[customer_id] = (now, part)
        return part

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _owner_of_txn(self, txn_id: str) -> str | None:
        """Who owns a txn id, from the keys-only index (never the record). None if unknown."""
        if not self.txn_index:
            return None
        try:
            rows = self._ddb().query(TableName=self.table, IndexName=self.txn_index, Limit=1,
                                     KeyConditionExpression="txn_id = :t",
                                     ExpressionAttributeValues={":t": {"S": str(txn_id)}}).get("Items", [])
        except Exception:
            return None  # no index (or it failed): NotFound, which the API answers like NotOwned
        if not rows:
            return None
        pk = (rows[0].get("pk") or {}).get("S") or ""
        return pk[len("CUST#"):] if pk.startswith("CUST#") else None

    # ------------------------------------------------------------ parsing

    def _parse(self, items: list[dict[str, Any]]) -> _Partition:
        p = _Partition()
        base: list[dict[str, Any]] = []
        for it in items:
            sk = str(it.get("sk") or "")
            if sk == "PROFILE":
                p.profile = it
            elif sk.startswith("PRODUCT#"):
                p.products.append(it)
            elif sk.startswith("TXN#"):
                base.append(it)
            elif sk == "BASELINE":
                p.baseline = it
            elif sk.startswith("CATBASE#"):
                p.catbase.append(it)
            elif sk.startswith("FEE#"):
                p.fees.append(it)
            elif sk.startswith("CONTACT#"):
                p.contacts.append(it)
        for r in base:
            t = self._txn(r)
            p.txns.append(t)
            p.txn_attrs[t.txn_id] = {"channel": r.get("channel"), "txn_country": r.get("txn_country")}
            if t.status == "reversed":  # the data marks it reversed but has no reversal record
                rev = Txn(txn_id=f"REV-{t.txn_id}", product_id=t.product_id, card_last4=t.card_last4,
                          local_date=t.local_date, local_time=t.local_time, amount=-t.amount, currency=t.currency,
                          amount_usd=-t.amount_usd if t.amount_usd is not None else None, merchant_id=t.merchant_id,
                          merchant_name=f"REVERSO {t.merchant_name}", merchant_category=t.merchant_category,
                          status="approved", response_code="00", fraud_score=None, txn_type="reversal",
                          reversal_of=t.txn_id, synthetic=True)
                p.txns.append(rev)
                p.txn_attrs[rev.txn_id] = dict(p.txn_attrs[t.txn_id])
        p.txns.sort(key=lambda t: (t.local_date, t.local_time, t.txn_id))
        p.products.sort(key=lambda r: str(r.get("product_id")))
        return p

    @staticmethod
    def _txn(r: dict[str, Any]) -> Txn:
        return Txn(
            txn_id=str(r["txn_id"]), product_id=str(r["product_id"]), card_last4=r.get("card_last4"),
            local_date=_d(r["local_date"]), local_time=str(r.get("local_time") or "00:00")[:8],
            amount=_money(r["amount"]), currency=str(r["currency"]), amount_usd=_money(r.get("amount_usd")),
            merchant_id=str(r["merchant_id"]), merchant_name=str(r["merchant_name"]),
            merchant_category=r.get("merchant_category") or "other", status=str(r["status"]),
            response_code=str(r.get("response_code") or ""), fraud_score=_f(r.get("fraud_score")),
            txn_type=r.get("txn_type") or "purchase", reversal_of=r.get("reversal_of"), synthetic=True,
        )

    # ------------------------------------------------------------ helpers

    def _check(self, ctx: GatewayContext) -> _Partition:
        if state.failing_for(ctx.session_id, ctx.actor):
            raise ToolUnavailable("gateway: failure switch is on")
        part = self._partition(ctx.customer_id)
        if part is None or part.profile is None:
            raise NotFound("customer")
        return part

    @staticmethod
    def _reference_date(part: _Partition) -> date:
        return _d(part.profile.get("reference_date")) or date.today()

    def _owned_txn(self, ctx: GatewayContext, txn_id: str) -> tuple[_Partition, Txn]:
        part = self._check(ctx)
        for t in part.txns:
            if t.txn_id == txn_id:
                return part, t
        owner = self._owner_of_txn(txn_id.removeprefix("REV-"))
        if owner is not None and owner != ctx.customer_id:
            raise NotOwned("transaction")
        raise NotFound("transaction")

    @staticmethod
    def _dt(t: Txn) -> datetime:
        return datetime.combine(t.local_date, dtime.fromisoformat(t.local_time))

    def _profile(self, part: _Partition) -> CustomerProfile:
        r = part.profile
        prods = tuple({"product_id": p.get("product_id"), "kind": p.get("kind"), "name": p.get("name"),
                       "currency": p.get("currency"), "last4": p.get("card_last4")}
                      for p in part.products if p.get("status") in ("active", "blocked"))
        return CustomerProfile(display_name=r["display_name"], language=r["language"], locale=r["locale"],
                               country=r["country"], currency=r["currency"], segment=r.get("segment") or "",
                               reference_date=self._reference_date(part), products=prods, synthetic=True)

    # ------------------------------------------------------------ server-side helpers (not #2 tools)

    def customer_id_for_demo_key(self, demo_key: str) -> str:
        raise NotFound("demo_key")  # judge-mode customers live in the demo backend only

    def profile_for_demo_key(self, demo_key: str) -> CustomerProfile:
        raise NotFound("demo_key")

    def profile_for_customer_id(self, customer_id: str) -> CustomerProfile | None:
        part = self._partition(customer_id)
        return self._profile(part) if part is not None and part.profile is not None else None

    def data_version(self) -> str:
        return f"serving:{self.table}"

    set_tool_failure = staticmethod(state.set_tool_failure)
    tool_failure_on = staticmethod(state.tool_failure_on)

    @staticmethod
    def reset_demo_state(session_id: str | None = None) -> None:
        state.reset(session_id)

    # ------------------------------------------------------------ #2 tools

    def get_customer_profile(self, ctx: GatewayContext) -> CustomerProfile:
        return self._profile(self._check(ctx))

    def find_candidate_txns(self, ctx: GatewayContext, date_from: date, date_to: date, amount_hint=None,
                            currency: str | None = None, limit: int = 200) -> list[Txn]:
        """Charges between date_from and date_to (inclusive) that could be the one the customer means:
        no reversals, no deposits, no declined movements. Same order as the duckdb backend."""
        part = self._check(ctx)
        date_from = _d(date_from)
        date_to = _d(date_to)
        cur = currency.upper() if currency else None
        rows = [t for t in part.txns if date_from <= t.local_date <= date_to
                and t.txn_type not in CANDIDATE_EXCLUDED_TYPES and t.status != "declined"
                and (cur is None or t.currency == cur)]
        if amount_hint is not None:
            hint = abs(Decimal(str(amount_hint)))
            rows.sort(key=lambda t: (abs(abs(t.amount) - hint), -t.local_date.toordinal(), t.txn_id))
        else:
            rows.sort(key=lambda t: (t.local_date, t.local_time, t.txn_id), reverse=True)
        return rows[: max(0, int(limit))]

    def get_transaction(self, ctx: GatewayContext, txn_id: str) -> Txn:
        return self._owned_txn(ctx, str(txn_id))[1]

    def get_txn_history(self, ctx: GatewayContext, days: int) -> list[Txn]:
        part = self._check(ctx)
        ref = self._reference_date(part)
        start = ref - timedelta(days=int(days))
        rows = [t for t in part.txns if start < t.local_date <= ref]
        return sorted(rows, key=lambda t: (t.local_date, t.local_time, t.txn_id), reverse=True)

    def find_duplicates(self, ctx: GatewayContext, txn_id: str) -> list[Txn]:
        """Other charges of the same product with the same merchant_id, amount and currency within 24 hours."""
        part, txn = self._owned_txn(ctx, str(txn_id))
        t0 = self._dt(txn)
        out = []
        for t in part.txns:
            if t.txn_id == txn.txn_id or t.txn_type == "reversal":
                continue
            if (t.product_id, t.merchant_id, t.amount, t.currency) != (txn.product_id, txn.merchant_id, txn.amount,
                                                                       txn.currency):
                continue
            if abs(self._dt(t) - t0) <= timedelta(hours=24):
                out.append(t)
        return sorted(out, key=lambda t: (t.local_date, t.local_time, t.txn_id))

    def get_reversals(self, ctx: GatewayContext, txn_id: str) -> list[Txn]:
        part, txn = self._owned_txn(ctx, str(txn_id))
        return [t for t in part.txns if t.txn_type == "reversal" and t.reversal_of == txn.txn_id]

    def get_fee_schedule(self, ctx: GatewayContext, product_id: str) -> FeeRule:
        part = self._check(ctx)
        rows = sorted((r for r in part.fees if r.get("product_id") == product_id), key=lambda r: str(r.get("fee_code")))
        if not rows:
            raise NotFound("fee schedule")  # another customer's product is not in this partition either
        return FeeRule(product_id=product_id, product_name=rows[0].get("product_name") or "",
                       currency=rows[0].get("currency") or "",
                       fees=tuple(FeeItem(fee_code=str(r["fee_code"]), merchant_id=str(r["merchant_id"]),
                                          description=str(r.get("description") or ""), amount=_money(r["amount"]),
                                          frequency=str(r.get("frequency") or "")) for r in rows),
                       synthetic=True)

    def get_customer_baseline(self, ctx: GatewayContext) -> Baseline:
        part = self._check(ctx)
        r = part.baseline
        if r is None:
            raise NotFound("baseline")
        cats = sorted(part.catbase, key=lambda c: (-_i(c.get("n")), str(c.get("merchant_category"))))
        hours = None
        if r.get("usual_hour_from") is not None and r.get("usual_hour_to") is not None:
            hours = (int(r["usual_hour_from"]), int(r["usual_hour_to"]))
        return Baseline(
            window_from=_d(r.get("window_from")), window_to=_d(r["window_to"]), n_txns=_i(r.get("n_txns")),
            currency=str(r.get("currency") or ""), median_amount=_money(r.get("median_amount")),
            p90_amount=_money(r.get("p90_amount")), top_categories=tuple(r.get("top_categories") or ()),
            top_merchants=tuple(r.get("top_merchants") or ()), usual_hours=hours, synthetic=True,
            median_amount_usd=_f(r.get("median_amount_usd")), p95_amount_usd=_f(r.get("p95_amount_usd")),
            known_merchant_ids=tuple(r.get("known_merchant_ids") or ()),
            usual_channels=tuple(r.get("usual_channels") or ()), usual_countries=tuple(r.get("usual_countries") or ()),
            category_stats=tuple((str(c["merchant_category"]), _i(c.get("n")), _f(c.get("median_usd")),
                                  _f(c.get("p95_usd"))) for c in cats),
        )

    def get_digital_sessions(self, ctx: GatewayContext, around_ts: datetime | str, minutes: int = 60) -> list[DigitalSession]:
        """Not in the serving export yet (``pipeline/serving.py``): always empty in the cloud."""
        self._check(ctx)
        return []

    def get_prior_contacts(self, ctx: GatewayContext) -> list[Contact]:
        """Contacts of the last ``contact_window_days`` up to the reference date, newest first."""
        part = self._check(ctx)
        ref = self._reference_date(part)
        since = ref - timedelta(days=self.contact_window_days)
        rows = [r for r in part.contacts if since < _d(r.get("date")) <= ref]
        rows.sort(key=lambda r: str(r.get("sk")), reverse=True)  # CONTACT#<date>#<id>, newest first
        return [Contact(contact_id=str(r["contact_id"]), date=_d(r["date"]), channel=str(r.get("channel") or ""),
                        contact_type=str(r.get("contact_type") or ""), subcategory=r.get("subcategory"),
                        status=str(r.get("status") or ""), synthetic=True) for r in rows]

    def get_merchant_stats(self, ctx: GatewayContext, merchant_id: str) -> MerchantStats:
        self._check(ctx)
        rows = self._query(KeyConditionExpression="pk = :pk AND sk = :sk",
                           ExpressionAttributeValues={":pk": {"S": f"MERCHANT#{merchant_id}"}, ":sk": {"S": "STATS"}})
        if not rows:
            raise NotFound("merchant")
        r = rows[0]
        return MerchantStats(merchant_id=str(r["merchant_id"]), merchant_name=str(r.get("merchant_name") or ""),
                             merchant_category=str(r.get("merchant_category") or ""), n_txns=_i(r.get("n_txns")),
                             n_customers=_i(r.get("n_customers")), fraud_rate=_f(r.get("fraud_rate")),
                             high_score_rate=_f(r.get("high_score_rate")), decline_rate=_f(r.get("decline_rate")),
                             reversal_rate=_f(r.get("reversal_rate")), dispute_rate=_f(r.get("dispute_rate")),
                             median_amount_usd=_f(r.get("median_amount_usd")), synthetic=True)

    def get_risk_evidence(self, ctx: GatewayContext, txn_id: str) -> RiskEvidence:
        """M3 evidence for one charge (comparison with the customer's own history). Evidence only."""
        part, txn = self._owned_txn(ctx, str(txn_id))
        baseline = self.get_customer_baseline(ctx)
        t0 = self._dt(txn)
        recent = [t for t in part.txns if txn.local_date - timedelta(days=1) <= t.local_date <= txn.local_date
                  and t.txn_id != txn.txn_id and t.txn_type != "reversal"
                  and timedelta(0) <= t0 - self._dt(t) <= timedelta(hours=24)]
        sessions = self.get_digital_sessions(ctx, t0 - timedelta(minutes=30), minutes=30)
        attrs = part.txn_attrs.get(txn.txn_id) or {}
        fn = self._risk_fn
        if fn is None:
            try:
                from models.m3 import risk_features as fn  # noqa: PLC0415
            except Exception:  # models not packaged: rule-only evidence
                fn = None
        rule = txn.fraud_score is not None and txn.fraud_score > FRAUD_SCORE_HIGH
        if fn is None:
            return RiskEvidence(txn_id=txn.txn_id, fraud_score=txn.fraud_score, rule_fired=rule, features={},
                                lift_score=None, model_version="m3-unavailable", synthetic=True)
        out = fn(txn, baseline, recent_txns=recent, sessions=sessions, channel=attrs.get("channel"),
                 txn_country=attrs.get("txn_country"), customer_country=part.profile.get("country"))
        return RiskEvidence(txn_id=txn.txn_id, fraud_score=txn.fraud_score, rule_fired=rule,
                            features=out.get("features") or {}, lift_score=out.get("lift_score"),
                            model_version=out.get("model_version") or "", synthetic=True)

    def get_cards(self, ctx: GatewayContext) -> list[Card]:
        """Cards of the customer with the status as seen by this session (simulated blocks applied)."""
        part = self._check(ctx)
        blocked = state.blocked_cards(ctx.session_id)
        out = []
        for r in part.products:
            if not r.get("is_card"):
                continue
            card_id = str(r.get("card_id") or "")
            status = str(r.get("status") or "")
            out.append(Card(card_id=card_id, product_id=str(r["product_id"]), card_last4=r.get("card_last4") or "----",
                            status="blocked" if card_id in blocked else ("active" if status == "active" else status),
                            synthetic=True))
        return out

    def block_card(self, ctx: GatewayContext, card_id: str, confirmed_by_customer: bool) -> CardStatus:
        """Simulated block. Refuses (NotConfirmed) unless ``confirmed_by_customer is True``. Idempotent;
        returns the status read back. There is no unblock. Nothing moves money."""
        part = self._check(ctx)
        if not any(r.get("is_card") and str(r.get("card_id")) == str(card_id) for r in part.products):
            raise NotFound("card")
        if confirmed_by_customer is not True:
            raise NotConfirmed("block_card needs the customer's explicit yes")
        changed_at = state.block(ctx.session_id, card_id)
        current = next(k for k in self.get_cards(ctx) if k.card_id == card_id)
        return CardStatus(card_id=card_id, card_last4=current.card_last4, status=current.status, changed_at=changed_at,
                          synthetic=True)
