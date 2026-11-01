"""Backend ``duckdb`` of the gateway tools (INTERFACES.md #2) over the gold Parquet tables.

Every tool takes ``ctx: GatewayContext`` first; ``ctx.customer_id`` comes from the verified session
(or, for the analyst console and the investigator, from the stored case), never from text. Tools
only return records owned by ``ctx.customer_id``: a record of another customer raises NotOwned, an
unknown one NotFound, a failing source ToolUnavailable. No tool moves money; ``block_card`` only
changes the simulated core and needs the customer's explicit yes.

Data: ``data/gold`` (``make pipeline``), loaded once into an in-process DuckDB with an index on
``customer_id`` and ``txn_id`` (the local stand-in for the DynamoDB serving table, whose partition
key is the customer). Transactions = ``transactions_enriched`` + the synthetic monthly
``fee_charges`` + a reversal entry derived for each charge the dataset marks ``reversed`` (the data
has no reversal records; the derived entry keeps the original's date and says so in
``merchant_name``).

Planted records (evaluation only): ``plant(customer_id, txns=..., hide=..., contacts=...)`` overlays
synthetic records on one customer (a duplicate, a fee above the schedule, a reversal ...) and
``clear_plants()`` removes them. They are marked ``synthetic=True`` and never written to disk.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import duckdb
import yaml

from gateway import state
from gateway.types import (
    Baseline, Card, CardStatus, Contact, CustomerProfile, DigitalSession, FeeItem, FeeRule, GatewayContext,
    GatewayError, MerchantStats, NotConfirmed, NotFound, NotOwned, RiskEvidence, ToolUnavailable, Txn,
    fee_item_for_txn,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD_DIR = REPO_ROOT / "data" / "gold"
FRAUD_SCORE_HIGH = 30.0
CONTACT_WINDOW_DAYS = 365
CANDIDATE_EXCLUDED_TYPES = ("reversal", "deposit")

TXN_COLUMNS = ("txn_id", "customer_id", "product_id", "card_last4", "local_date", "local_time", "amount", "currency",
               "amount_usd", "merchant_id", "merchant_name", "merchant_category", "status", "response_code",
               "fraud_score", "txn_type", "reversal_of", "channel", "txn_country", "origin")


@dataclass
class _Overlay:
    txns: list[Txn] = field(default_factory=list)
    hide: set[str] = field(default_factory=set)
    contacts: list[Contact] = field(default_factory=list)
    channel: dict[str, str] = field(default_factory=dict)   # txn_id -> channel of planted txns
    country: dict[str, str] = field(default_factory=dict)   # txn_id -> country of planted txns
    reference_date: date | None = None  # evaluation: "today" for this customer (records after it are hidden)


def _posix(p: Path) -> str:
    return Path(p).as_posix().replace("'", "''")


class DuckDBGateway:
    """The #2 tools over gold. Thread safe (one DuckDB cursor per call)."""

    name = "duckdb"
    # Error classes and helpers as attributes: callers write gw.NotOwned, gw.GatewayContext(...)
    GatewayContext = GatewayContext
    GatewayError = GatewayError
    NotOwned = NotOwned
    NotFound = NotFound
    ToolUnavailable = ToolUnavailable
    NotConfirmed = NotConfirmed
    fee_item_for_txn = staticmethod(fee_item_for_txn)

    def __init__(self, gold_dir: Path | str | None = None, contact_window_days: int = CONTACT_WINDOW_DAYS,
                 risk_fn: Any = None) -> None:
        self.gold_dir = Path(gold_dir or DEFAULT_GOLD_DIR)
        self.contact_window_days = contact_window_days
        self._risk_fn = risk_fn
        self._con: duckdb.DuckDBPyConnection | None = None
        self._load_lock = threading.Lock()
        self._plant_lock = threading.Lock()
        self._overlays: dict[str, _Overlay] = {}
        self.meta: dict = {}

    # ------------------------------------------------------------ loading

    def available(self) -> bool:
        return (self.gold_dir / "transactions_enriched" / "data.parquet").exists()

    def _db(self) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            return self._con
        with self._load_lock:
            if self._con is None:
                self._con = self._load()
        return self._con

    def _load(self) -> duckdb.DuckDBPyConnection:
        if not self.available():
            raise ToolUnavailable(f"gold tables not found in {self.gold_dir}: run `make pipeline`")
        meta_file = self.gold_dir / "_meta.yaml"
        self.meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        con = duckdb.connect()
        g = lambda t: _posix(self.gold_dir / t / "data.parquet")  # noqa: E731
        cols = ", ".join(TXN_COLUMNS)
        con.execute(f"""
            CREATE TABLE txns AS
            WITH base AS (
                SELECT {cols} FROM read_parquet('{g('transactions_enriched')}')
                UNION ALL
                SELECT {cols} FROM read_parquet('{g('fee_charges')}')
            ),
            derived AS (  -- the dataset marks a charge 'reversed' but has no reversal record
                SELECT 'REV-' || txn_id AS txn_id, customer_id, product_id, card_last4, local_date, local_time,
                       -amount AS amount, currency, -amount_usd AS amount_usd, merchant_id,
                       'REVERSO ' || merchant_name AS merchant_name, merchant_category, 'approved' AS status,
                       '00' AS response_code, NULL::DOUBLE AS fraud_score, 'reversal' AS txn_type,
                       txn_id AS reversal_of, channel, txn_country, 'derived_from_status' AS origin
                FROM base WHERE status = 'reversed'
            )
            SELECT * FROM base UNION ALL SELECT * FROM derived
            ORDER BY customer_id, local_date, local_time, txn_id
        """)
        con.execute("CREATE INDEX txns_customer ON txns(customer_id)")
        con.execute("CREATE INDEX txns_id ON txns(txn_id)")
        con.execute(f"CREATE TABLE profile AS SELECT * FROM read_parquet('{g('customer_profile')}')")
        con.execute("CREATE INDEX profile_customer ON profile(customer_id)")
        con.execute(f"CREATE TABLE products AS SELECT * FROM read_parquet('{g('customer_products')}')")
        con.execute("CREATE INDEX products_customer ON products(customer_id)")
        con.execute(f"CREATE TABLE fees AS SELECT * FROM read_parquet('{g('fee_schedule')}')")
        con.execute(f"CREATE TABLE baseline AS SELECT * FROM read_parquet('{g('customer_baseline')}')")
        con.execute(f"CREATE TABLE catbase AS SELECT * FROM read_parquet('{g('customer_category_baseline')}')")
        con.execute(f"CREATE TABLE contacts AS SELECT * FROM read_parquet('{g('contact_history')}')")
        con.execute("CREATE INDEX contacts_customer ON contacts(customer_id)")
        con.execute(f"CREATE TABLE merchants AS SELECT * FROM read_parquet('{g('merchant_stats')}')")
        sess = self.gold_dir / "digital_sessions" / "data.parquet"
        if sess.exists():
            con.execute(f"CREATE TABLE sessions AS SELECT * FROM read_parquet('{_posix(sess)}')")
        else:
            con.execute("CREATE TABLE sessions (customer_id VARCHAR, session_id VARCHAR, started_at TIMESTAMP, "
                        "ended_at TIMESTAMP, channel VARCHAR, platform VARCHAR, app_version VARCHAR, ip_country VARCHAR, "
                        "n_events BIGINT, has_login BOOLEAN, has_error BOOLEAN, has_transaction_page BOOLEAN, "
                        "first_seen_device BOOLEAN)")
        con.execute("CREATE INDEX sessions_customer ON sessions(customer_id)")
        return con

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        cur = self._db().cursor()
        try:
            res = cur.execute(sql, list(params))
            names = [d[0] for d in res.description]
            return [dict(zip(names, r)) for r in res.fetchall()]
        except duckdb.Error as e:  # a broken source is a tool failure, never a crash
            raise ToolUnavailable(f"duckdb gateway: {type(e).__name__}") from e
        finally:
            cur.close()

    @property
    def synthetic_data(self) -> bool:
        self._db()
        return bool(self.meta.get("synthetic", True))

    # ------------------------------------------------------------ planted records (evaluation only)

    def plant(self, customer_id: str, txns: Iterable[Txn] = (), hide: Iterable[str] = (),
              contacts: Iterable[Contact] = (), channel: dict[str, str] | None = None,
              country: dict[str, str] | None = None, reference_date: date | None = None) -> None:
        """Evaluation only. ``reference_date`` makes the customer's "today" that day: relative dates,
        windows, history and contacts are as of it, and later records are hidden."""
        with self._plant_lock:
            ov = self._overlays.setdefault(customer_id, _Overlay())
            ov.txns.extend(txns)
            ov.hide.update(hide)
            ov.contacts.extend(contacts)
            ov.channel.update(channel or {})
            ov.country.update(country or {})
            if reference_date is not None:
                ov.reference_date = reference_date

    def clear_plants(self, customer_id: str | None = None) -> None:
        with self._plant_lock:
            if customer_id is None:
                self._overlays.clear()
            else:
                self._overlays.pop(customer_id, None)

    def _overlay(self, customer_id: str) -> _Overlay:
        with self._plant_lock:
            ov = self._overlays.get(customer_id)
            if ov is None:
                return _Overlay()
            return _Overlay(list(ov.txns), set(ov.hide), list(ov.contacts), dict(ov.channel), dict(ov.country),
                            ov.reference_date)

    # ------------------------------------------------------------ helpers

    def _check(self, ctx: GatewayContext) -> dict:
        if state.failing_for(ctx.session_id, ctx.actor):
            raise ToolUnavailable("gateway: failure switch is on")
        rows = self._rows("SELECT * FROM profile WHERE customer_id = ?", [ctx.customer_id])
        if not rows:
            raise NotFound("customer")
        return self._as_of(rows[0])

    def _as_of(self, row: dict) -> dict:
        ref = self._overlay(row["customer_id"]).reference_date
        if ref is not None:
            row = {**row, "reference_date": ref}
        return row

    def _visible(self, customer_id: str, txns: list[Txn]) -> list[Txn]:
        ref = self._overlay(customer_id).reference_date
        return txns if ref is None else [t for t in txns if t.local_date <= ref]

    def _txn(self, r: dict) -> Txn:
        return Txn(
            txn_id=r["txn_id"], product_id=r["product_id"], card_last4=r.get("card_last4"),
            local_date=r["local_date"], local_time=r["local_time"],
            amount=Decimal(str(r["amount"])).quantize(Decimal("0.01")), currency=r["currency"],
            amount_usd=None if r.get("amount_usd") is None else Decimal(str(r["amount_usd"])).quantize(Decimal("0.01")),
            merchant_id=r["merchant_id"], merchant_name=r["merchant_name"],
            merchant_category=r.get("merchant_category") or "other", status=r["status"],
            response_code=r.get("response_code") or "", fraud_score=None if r.get("fraud_score") is None else float(r["fraud_score"]),
            txn_type=r.get("txn_type") or "purchase", reversal_of=r.get("reversal_of"),
            synthetic=self.synthetic_data or (r.get("origin") or "dataset") != "dataset",
        )

    def _customer_txns(self, ctx: GatewayContext, where: str = "", params: Iterable[Any] = ()) -> list[Txn]:
        rows = self._rows(f"SELECT * FROM txns WHERE customer_id = ? {where}", [ctx.customer_id, *params])
        ov = self._overlay(ctx.customer_id)
        out = [self._txn(r) for r in rows if r["txn_id"] not in ov.hide]
        return out

    def _all_customer_txns(self, ctx: GatewayContext) -> list[Txn]:
        ov = self._overlay(ctx.customer_id)
        return self._customer_txns(ctx) + [t for t in ov.txns if t.txn_id not in ov.hide]

    def _owned_txn(self, ctx: GatewayContext, txn_id: str) -> Txn:
        self._check(ctx)
        ov = self._overlay(ctx.customer_id)
        if txn_id in ov.hide:
            raise NotFound("transaction")
        for t in ov.txns:
            if t.txn_id == txn_id:
                return t
        rows = self._rows("SELECT * FROM txns WHERE txn_id = ?", [txn_id])
        if rows:
            if rows[0]["customer_id"] != ctx.customer_id:
                raise NotOwned("transaction")
            return self._txn(rows[0])
        with self._plant_lock:
            if any(t.txn_id == txn_id for c, o in self._overlays.items() if c != ctx.customer_id for t in o.txns):
                raise NotOwned("transaction")
        raise NotFound("transaction")

    @staticmethod
    def _dt(t: Txn) -> datetime:
        return datetime.combine(t.local_date, time.fromisoformat(t.local_time))

    def _profile(self, row: dict) -> CustomerProfile:
        prods = self._rows("SELECT product_id, kind, name, currency, card_last4 AS last4 FROM products "
                           "WHERE customer_id = ? AND status IN ('active', 'blocked') ORDER BY product_id",
                           [row["customer_id"]])
        return CustomerProfile(
            display_name=row["display_name"], language=row["language"], locale=row["locale"], country=row["country"],
            currency=row["currency"], segment=row["segment"], reference_date=row["reference_date"],
            products=tuple(prods), synthetic=self.synthetic_data,
        )

    # ------------------------------------------------------------ server-side helpers (not #2 tools)

    def customer_id_for_demo_key(self, demo_key: str) -> str:
        raise NotFound("demo_key")  # judge-mode customers live in the demo backend only

    def profile_for_demo_key(self, demo_key: str) -> CustomerProfile:
        raise NotFound("demo_key")

    def profile_for_customer_id(self, customer_id: str) -> CustomerProfile | None:
        rows = self._rows("SELECT * FROM profile WHERE customer_id = ?", [customer_id])
        return self._profile(self._as_of(rows[0])) if rows else None

    def data_version(self) -> str:
        self._db()
        return f"gold:{self.meta.get('data_source', '?')}:{self.meta.get('snapshot_date', '?')}:{self.meta.get('built_at', '?')}"

    def customer_ids(self, limit: int | None = None) -> list[str]:
        sql = "SELECT customer_id FROM profile ORDER BY customer_id" + (f" LIMIT {int(limit)}" if limit else "")
        return [r["customer_id"] for r in self._rows(sql)]

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
        """Charges of the customer between date_from and date_to (inclusive) that could be the one
        they mean: no reversals, no deposits, no declined movements (they never posted).
        ``currency`` filters when given; ``amount_hint`` only orders by closeness (else newest first)."""
        self._check(ctx)
        date_from = date.fromisoformat(str(date_from)) if not isinstance(date_from, date) else date_from
        date_to = date.fromisoformat(str(date_to)) if not isinstance(date_to, date) else date_to
        params: list[Any] = [date_from, date_to]
        where = "AND local_date BETWEEN ? AND ? AND txn_type NOT IN ('reversal', 'deposit') AND status <> 'declined'"
        if currency:
            where += " AND currency = ?"
            params.append(currency.upper())
        rows = self._customer_txns(ctx, where, params)
        ov = self._overlay(ctx.customer_id)
        rows += [t for t in ov.txns if t.txn_id not in ov.hide and date_from <= t.local_date <= date_to
                 and t.txn_type not in CANDIDATE_EXCLUDED_TYPES and t.status != "declined"
                 and (not currency or t.currency == currency.upper())]
        rows = self._visible(ctx.customer_id, rows)
        if amount_hint is not None:
            hint = abs(Decimal(str(amount_hint)))
            rows.sort(key=lambda t: (abs(abs(t.amount) - hint), -t.local_date.toordinal(), t.txn_id))
        else:
            rows.sort(key=lambda t: (t.local_date, t.local_time, t.txn_id), reverse=True)
        return rows[: max(0, int(limit))]

    def get_transaction(self, ctx: GatewayContext, txn_id: str) -> Txn:
        return self._owned_txn(ctx, txn_id)

    def get_txn_history(self, ctx: GatewayContext, days: int) -> list[Txn]:
        prof = self._check(ctx)
        ref = prof["reference_date"]
        start = ref - timedelta(days=int(days))
        rows = [t for t in self._all_customer_txns(ctx) if start < t.local_date <= ref]
        return sorted(rows, key=lambda t: (t.local_date, t.local_time, t.txn_id), reverse=True)

    def find_duplicates(self, ctx: GatewayContext, txn_id: str) -> list[Txn]:
        """Other charges of the same product with the same merchant_id, amount and currency within 24 hours."""
        txn = self._owned_txn(ctx, txn_id)
        t0 = self._dt(txn)
        near = self._customer_txns(ctx, "AND local_date BETWEEN ? AND ?",
                                   [txn.local_date - timedelta(days=1), txn.local_date + timedelta(days=1)])
        ov = self._overlay(ctx.customer_id)
        near += [t for t in ov.txns if t.txn_id not in ov.hide]
        out = []
        for t in near:
            if t.txn_id == txn.txn_id or t.txn_type == "reversal":
                continue
            # same product (card or account), merchant, amount and currency: two cards charged the same
            # monthly fee are not a duplicate
            if (t.product_id, t.merchant_id, t.amount, t.currency) != (txn.product_id, txn.merchant_id, txn.amount, txn.currency):
                continue
            if abs(self._dt(t) - t0) <= timedelta(hours=24):
                out.append(t)
        return sorted(out, key=lambda t: (t.local_date, t.local_time, t.txn_id))

    def get_reversals(self, ctx: GatewayContext, txn_id: str) -> list[Txn]:
        txn = self._owned_txn(ctx, txn_id)
        rows = self._customer_txns(ctx, "AND txn_type = 'reversal' AND reversal_of = ?", [txn.txn_id])
        ov = self._overlay(ctx.customer_id)
        rows += [t for t in ov.txns if t.txn_type == "reversal" and t.reversal_of == txn.txn_id and t.txn_id not in ov.hide]
        return rows

    def get_fee_schedule(self, ctx: GatewayContext, product_id: str) -> FeeRule:
        self._check(ctx)
        rows = self._rows("SELECT * FROM fees WHERE product_id = ? ORDER BY fee_code", [product_id])
        if rows and rows[0]["customer_id"] != ctx.customer_id:
            raise NotOwned("product")
        if not rows:
            owner = self._rows("SELECT customer_id FROM products WHERE product_id = ?", [product_id])
            if owner and owner[0]["customer_id"] != ctx.customer_id:
                raise NotOwned("product")
            raise NotFound("fee schedule")
        return FeeRule(product_id=product_id, product_name=rows[0]["product_name"], currency=rows[0]["currency"],
                       fees=tuple(FeeItem(fee_code=r["fee_code"], merchant_id=r["merchant_id"], description=r["description"],
                                          amount=Decimal(str(r["amount"])).quantize(Decimal("0.01")), frequency=r["frequency"])
                                  for r in rows))

    def get_customer_baseline(self, ctx: GatewayContext) -> Baseline:
        self._check(ctx)
        rows = self._rows("SELECT * FROM baseline WHERE customer_id = ?", [ctx.customer_id])
        if not rows:
            raise NotFound("baseline")
        r = rows[0]
        cats = self._rows("SELECT merchant_category, n, median_usd, p95_usd FROM catbase WHERE customer_id = ? "
                          "ORDER BY n DESC, merchant_category", [ctx.customer_id])
        hours = None
        if r.get("usual_hour_from") is not None and r.get("usual_hour_to") is not None:
            hours = (int(r["usual_hour_from"]), int(r["usual_hour_to"]))
        dec = lambda v: None if v is None else Decimal(str(v)).quantize(Decimal("0.01"))  # noqa: E731
        return Baseline(
            window_from=r.get("window_from"), window_to=r["window_to"], n_txns=int(r["n_txns"]), currency=r["currency"],
            median_amount=dec(r.get("median_amount")), p90_amount=dec(r.get("p90_amount")),
            top_categories=tuple(r.get("top_categories") or ()), top_merchants=tuple(r.get("top_merchants") or ()),
            usual_hours=hours, synthetic=self.synthetic_data,
            median_amount_usd=r.get("median_amount_usd"), p95_amount_usd=r.get("p95_amount_usd"),
            known_merchant_ids=tuple(r.get("known_merchant_ids") or ()),
            usual_channels=tuple(r.get("usual_channels") or ()), usual_countries=tuple(r.get("usual_countries") or ()),
            category_stats=tuple((c["merchant_category"], int(c["n"]), c["median_usd"], c["p95_usd"]) for c in cats),
        )

    def get_digital_sessions(self, ctx: GatewayContext, around_ts: datetime | str, minutes: int = 60) -> list[DigitalSession]:
        self._check(ctx)
        around = datetime.fromisoformat(str(around_ts)) if not isinstance(around_ts, datetime) else around_ts
        around = around.replace(tzinfo=None)
        lo, hi = around - timedelta(minutes=int(minutes)), around + timedelta(minutes=int(minutes))
        rows = self._rows("SELECT * FROM sessions WHERE customer_id = ? AND started_at <= ? AND ended_at >= ? "
                          "ORDER BY started_at", [ctx.customer_id, hi, lo])
        return [DigitalSession(session_id=r["session_id"], started_at=r["started_at"], ended_at=r["ended_at"],
                               channel=r["channel"], platform=r.get("platform"), ip_country=r.get("ip_country"),
                               n_events=int(r["n_events"]), has_login=bool(r["has_login"]), has_error=bool(r["has_error"]),
                               first_seen_device=bool(r["first_seen_device"]), synthetic=self.synthetic_data)
                for r in rows]

    def get_prior_contacts(self, ctx: GatewayContext) -> list[Contact]:
        """Contacts of the last ``contact_window_days`` up to the reference date, newest first.
        Count ``contact_type == "complaint"`` for ``Evidence.prior_complaints``."""
        prof = self._check(ctx)
        ref = prof["reference_date"]
        rows = self._rows("SELECT * FROM contacts WHERE customer_id = ? AND date > ? AND date <= ? ORDER BY ts DESC",
                          [ctx.customer_id, ref - timedelta(days=self.contact_window_days), ref])
        out = [Contact(contact_id=r["contact_id"], date=r["date"], channel=r["channel"], contact_type=r["contact_type"],
                       subcategory=r.get("subcategory"), status=r["status"], synthetic=self.synthetic_data) for r in rows]
        out += self._overlay(ctx.customer_id).contacts
        return sorted(out, key=lambda c: c.date, reverse=True)

    def get_merchant_stats(self, ctx: GatewayContext, merchant_id: str) -> MerchantStats:
        self._check(ctx)
        rows = self._rows("SELECT * FROM merchants WHERE merchant_id = ?", [merchant_id])
        if not rows:
            raise NotFound("merchant")
        r = rows[0]
        return MerchantStats(merchant_id=r["merchant_id"], merchant_name=r["merchant_name"],
                             merchant_category=r["merchant_category"], n_txns=int(r["n_txns"]),
                             n_customers=int(r["n_customers"]), fraud_rate=r["fraud_rate"],
                             high_score_rate=r["high_score_rate"], decline_rate=r["decline_rate"],
                             reversal_rate=r["reversal_rate"], dispute_rate=r["dispute_rate"],
                             median_amount_usd=r["median_amount_usd"], synthetic=self.synthetic_data)

    def get_risk_evidence(self, ctx: GatewayContext, txn_id: str) -> RiskEvidence:
        """M3 evidence for one charge: comparison with the customer's own history (baseline, recent
        velocity, sessions before the charge). Evidence only; the lane rules decide."""
        txn = self._owned_txn(ctx, txn_id)
        baseline = self.get_customer_baseline(ctx)
        t0 = self._dt(txn)
        recent = [t for t in self._customer_txns(ctx, "AND local_date BETWEEN ? AND ?",
                                                 [txn.local_date - timedelta(days=1), txn.local_date])
                  if t.txn_id != txn.txn_id and t.txn_type != "reversal" and timedelta(0) <= t0 - self._dt(t) <= timedelta(hours=24)]
        sessions = self.get_digital_sessions(ctx, t0 - timedelta(minutes=30), minutes=30)
        prof = self._rows("SELECT country FROM profile WHERE customer_id = ?", [ctx.customer_id])[0]
        ov = self._overlay(ctx.customer_id)
        rows = self._rows("SELECT channel, txn_country FROM txns WHERE txn_id = ?", [txn.txn_id])
        channel = ov.channel.get(txn.txn_id) or (rows[0]["channel"] if rows else None)
        country = ov.country.get(txn.txn_id) or (rows[0]["txn_country"] if rows else None)
        fn = self._risk_fn
        if fn is None:
            try:
                from models.m3 import risk_features as fn  # noqa: PLC0415
            except Exception:  # models not trained: rule-only evidence
                fn = None
        rule = txn.fraud_score is not None and txn.fraud_score > FRAUD_SCORE_HIGH
        if fn is None:
            return RiskEvidence(txn_id=txn.txn_id, fraud_score=txn.fraud_score, rule_fired=rule, features={},
                                lift_score=None, model_version="m3-unavailable", synthetic=self.synthetic_data)
        out = fn(txn, baseline, recent_txns=recent, sessions=sessions, channel=channel, txn_country=country,
                 customer_country=prof["country"])
        return RiskEvidence(txn_id=txn.txn_id, fraud_score=txn.fraud_score, rule_fired=rule,
                            features=out.get("features") or {}, lift_score=out.get("lift_score"),
                            model_version=out.get("model_version") or "", synthetic=self.synthetic_data)

    def get_cards(self, ctx: GatewayContext) -> list[Card]:
        """Cards of the customer with the status as seen by this session (simulated blocks applied)."""
        self._check(ctx)
        rows = self._rows("SELECT card_id, product_id, card_last4, status FROM products "
                          "WHERE customer_id = ? AND is_card ORDER BY product_id", [ctx.customer_id])
        blocked = state.blocked_cards(ctx.session_id)
        return [Card(card_id=r["card_id"], product_id=r["product_id"], card_last4=r["card_last4"] or "----",
                     status="blocked" if r["card_id"] in blocked else ("active" if r["status"] == "active" else r["status"]),
                     synthetic=self.synthetic_data) for r in rows]

    def block_card(self, ctx: GatewayContext, card_id: str, confirmed_by_customer: bool) -> CardStatus:
        """Simulated block. Refuses (NotConfirmed) unless ``confirmed_by_customer is True``.
        Idempotent; returns the status read back. There is no unblock. Nothing moves money."""
        self._check(ctx)
        rows = self._rows("SELECT customer_id FROM products WHERE card_id = ?", [card_id])
        if not rows:
            raise NotFound("card")
        if rows[0]["customer_id"] != ctx.customer_id:
            raise NotOwned("card")
        if confirmed_by_customer is not True:
            raise NotConfirmed("block_card needs the customer's explicit yes")
        changed_at = state.block(ctx.session_id, card_id)
        current = next(k for k in self.get_cards(ctx) if k.card_id == card_id)
        return CardStatus(card_id=card_id, card_last4=current.card_last4, status=current.status, changed_at=changed_at,
                          synthetic=self.synthetic_data)
