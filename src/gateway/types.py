"""Types and errors of the gateway tools (INTERFACES.md #2), shared by every backend.

Moved here from ``conversation/demo_gateway.py`` (which re-exports them) so the ``demo`` and
``duckdb`` backends raise the SAME exception classes and return the same records: the
orchestrator catches ``gw.ToolUnavailable`` / ``gw.NotOwned`` whichever backend answered.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


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


# ---------------------------------------------------------------- context

@dataclass(frozen=True)
class GatewayContext:
    """Who is asking. Built ONLY by the server from a verified session, never from text.

    customer_id: owner of every record the tools may return (server-side, never sent out).
    session_id: scopes simulated state (card blocks, failure switch).
    trace_id: lets the caller tie tool calls to the trace (#7).
    actor: "customer" (the chat and the lifecycle of its case) or "analyst" (the console reading a
        case). The per-session judge switch ``tools_down`` belongs to the customer's chat: it never
        empties what the analyst sees. The process-wide switch applies to both.
    """

    customer_id: str
    session_id: str
    trace_id: str
    actor: str = "customer"

    @classmethod
    def for_analyst(cls, customer_id: str, session_id: str, trace_id: str) -> "GatewayContext":
        """Context of the analyst console for a stored case: owner and session come from the
        stored case, never from the request."""
        return cls(customer_id=customer_id, session_id=session_id, trace_id=trace_id, actor="analyst")


# ---------------------------------------------------------------- records

@dataclass(frozen=True)
class Txn:
    """A transaction with the #2 fields plus ``txn_type`` and ``reversal_of``."""

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
    """Usual behaviour of the customer, from purchases older than 14 days before the reference
    date (so the disputed recent charges do not define "normal")."""

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
    # duckdb backend extras (empty in the demo backend)
    median_amount_usd: float | None = None
    p95_amount_usd: float | None = None
    known_merchant_ids: tuple[str, ...] = ()
    usual_channels: tuple[str, ...] = ()
    usual_countries: tuple[str, ...] = ()
    category_stats: tuple[tuple[str, int, float | None, float | None], ...] = ()  # (category, n, median_usd, p95_usd)


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
    """Server-side profile. First name only, no PII."""

    display_name: str
    language: str
    locale: str
    country: str
    currency: str
    segment: str
    reference_date: date
    products: tuple[dict, ...]
    synthetic: bool = True


@dataclass(frozen=True)
class DigitalSession:
    """A digital-banking session near a time (G2 ``get_digital_sessions``)."""

    session_id: str
    started_at: datetime
    ended_at: datetime
    channel: str
    platform: str | None
    ip_country: str | None
    n_events: int
    has_login: bool
    has_error: bool
    first_seen_device: bool
    synthetic: bool = True


@dataclass(frozen=True)
class MerchantStats:
    """Aggregates of one merchant across the bank (never another customer's records)."""

    merchant_id: str
    merchant_name: str
    merchant_category: str
    n_txns: int
    n_customers: int
    fraud_rate: float | None
    high_score_rate: float | None
    decline_rate: float | None
    reversal_rate: float | None
    dispute_rate: float | None
    median_amount_usd: float | None
    synthetic: bool = True


@dataclass(frozen=True)
class RiskEvidence:
    """M3 risk evidence for one charge (#4 ``risk_features`` through the gateway)."""

    txn_id: str
    fraud_score: float | None
    rule_fired: bool  # fraud_score > 30 (the data's exact rule)
    features: dict = field(default_factory=dict)
    lift_score: float | None = None
    model_version: str = ""
    synthetic: bool = True


def fee_item_for_txn(rule: FeeRule, txn: Txn) -> FeeItem | None:
    """Pure helper (not a #2 tool): the schedule entry a fee txn corresponds to, by merchant_id,
    or None if the txn is not a scheduled fee of that product. Whether the amount matches
    (``item.amount == txn.amount``) is the caller's evidence."""
    if txn.product_id != rule.product_id:
        return None
    return next((i for i in rule.fees if i.merchant_id == txn.merchant_id), None)
