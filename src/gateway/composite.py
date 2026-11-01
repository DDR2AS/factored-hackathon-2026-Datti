"""Backend ``composite``: judge-mode demo customers from the demo store, everyone else from gold.

Plan v2 section 12: "All customers are synthetic and live in the demo store, never in the gold
tables." The composite keeps that separation and still lets the same app serve customers of the
dataset (evaluation, a "real customer" session): the backend is chosen by the customer id that the
SERVER put in the context (``DEMO-*`` -> demo), never by anything the customer wrote. Asking one
backend for a record of the other gives NotFound, which the API answers like NotOwned (same 403).
"""

from __future__ import annotations

from typing import Any

from gateway import state
from gateway.types import (
    GatewayContext, GatewayError, NotConfirmed, NotFound, NotOwned, ToolUnavailable, fee_item_for_txn,
)

TOOLS = ("get_customer_profile", "find_candidate_txns", "get_transaction", "get_txn_history", "find_duplicates",
         "get_reversals", "get_fee_schedule", "get_customer_baseline", "get_digital_sessions", "get_prior_contacts",
         "get_merchant_stats", "get_risk_evidence", "get_cards", "block_card")


class CompositeGateway:
    name = "composite"
    GatewayContext = GatewayContext
    GatewayError = GatewayError
    NotOwned = NotOwned
    NotFound = NotFound
    ToolUnavailable = ToolUnavailable
    NotConfirmed = NotConfirmed
    fee_item_for_txn = staticmethod(fee_item_for_txn)

    def __init__(self, demo: Any, duck: Any) -> None:
        self.demo = demo
        self.duck = duck

    def _for(self, customer_id: str) -> Any:
        return self.demo if str(customer_id).startswith("DEMO-") else self.duck

    def __getattr__(self, name: str):
        if name in TOOLS:
            def call(ctx, *args, **kwargs):
                backend = self._for(ctx.customer_id)
                fn = getattr(backend, name, None)
                if fn is None:  # the demo backend has no risk/session/merchant tools
                    raise NotFound(f"{name} is not available for this customer")
                return fn(ctx, *args, **kwargs)
            call.__name__ = name
            return call
        raise AttributeError(name)

    # server-side helpers
    def customer_id_for_demo_key(self, demo_key: str) -> str:
        return self.demo.customer_id_for_demo_key(demo_key)

    def profile_for_demo_key(self, demo_key: str):
        return self.demo.profile_for_demo_key(demo_key)

    def profile_for_customer_id(self, customer_id: str):
        return self._for(customer_id).profile_for_customer_id(customer_id)

    def data_version(self) -> str:
        parts = [self.demo.data_version()]
        try:
            parts.append(self.duck.data_version())
        except GatewayError:
            parts.append("gold:unavailable")
        return " | ".join(parts)

    set_tool_failure = staticmethod(state.set_tool_failure)
    tool_failure_on = staticmethod(state.tool_failure_on)

    @staticmethod
    def reset_demo_state(session_id: str | None = None) -> None:
        state.reset(session_id)
