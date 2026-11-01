"""Promised date and case clock (owner: arturo).

From the creation time, the complaint type and (for lane C) the queue, computes the
``Promise`` and ``Clock`` of the case record (#3, ``case.py``) using
``rules/promise_times.yaml`` (figures and their source are in that file):

- promise.expected_date = local creation date + the ``expected_basis`` days (p90 by default)
- promise.sla_days, promise.p90_days as configured
- clock.assigned_by = created_at + assign_hours (24 h; 2 h for the fraud queue)
- clock.first_response_by = created_at + first_response_hours (24 h; 2 h for fraud)
- clock.sla_alert_at = created_at + sla_alert_fraction * sla_days (80 % of the SLA)
- clock.breach_at = created_at + sla_days

Calendar days, not business days. Pure: the caller passes ``created_at`` (inject the clock).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from conversation.case import Clock, Promise
from conversation.templates import UTC_OFFSETS

PROMISE_PATH = Path(__file__).parent / "rules" / "promise_times.yaml"
_KEYS = {"median_days", "p90_days", "sla_days", "first_response_hours", "assign_hours", "sla_alert_fraction"}
_BASES = {"median_days", "p90_days", "sla_days"}


@dataclass(frozen=True)
class PromisePlan:
    promise: Promise
    clock: Clock
    times_version: str


@lru_cache(maxsize=None)
def load_times(path: Path = PROMISE_PATH) -> dict:
    """Load and validate the YAML (unknown keys or basis fail at load time)."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data.get("expected_basis") not in _BASES:
        raise ValueError(f"expected_basis must be one of {sorted(_BASES)}")
    if set(data["default"]) != _KEYS:
        raise ValueError(f"default must define exactly {sorted(_KEYS)}")
    for section in ("by_complaint_type", "by_queue"):
        for name, over in (data.get(section) or {}).items():
            unknown = set(over) - _KEYS
            if unknown:
                raise ValueError(f"{section}.{name}: unknown keys {sorted(unknown)}")
    return data


def times_for(complaint_type: str | None = None, queue: str | None = None) -> dict:
    """Effective figures: default, then complaint-type override, then queue override."""
    data = load_times()
    out = dict(data["default"])
    out.update((data.get("by_complaint_type") or {}).get(complaint_type or "", {}))
    out.update((data.get("by_queue") or {}).get(queue or "", {}))
    return out


def compute_promise(created_at: datetime, complaint_type: str | None = None, queue: str | None = None,
                    country: str | None = None) -> PromisePlan:
    """Promise and clock for a case created at ``created_at``.

    created_at: aware datetime (naive is taken as UTC). complaint_type: one of the
    lane_rules complaint types or None. queue: lane C queue ("fraud" shortens the first
    response to 2 h) or None. country: "MX"|"CO"|"AR" decides the local calendar day of the
    expected date (UTC if None).
    Returns ``PromisePlan(promise=case.Promise, clock=case.Clock, times_version)``; clock
    times are UTC. Never promises an outcome, only dates.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    t = times_for(complaint_type, queue)
    basis = load_times()["expected_basis"]
    offset = UTC_OFFSETS.get(country or "")
    local_day = (created_at + timedelta(hours=offset)).date() if offset is not None else created_at.date()
    promise = Promise(
        expected_date=local_day + timedelta(days=int(t[basis])),
        sla_days=int(t["sla_days"]),
        p90_days=int(t["p90_days"]),
    )
    clock = Clock(
        assigned_by=created_at + timedelta(hours=float(t["assign_hours"])),
        first_response_by=created_at + timedelta(hours=float(t["first_response_hours"])),
        sla_alert_at=created_at + timedelta(days=float(t["sla_days"]) * float(t["sla_alert_fraction"])),
        breach_at=created_at + timedelta(days=float(t["sla_days"])),
    )
    return PromisePlan(promise=promise, clock=clock, times_version=str(load_times()["version"]))
