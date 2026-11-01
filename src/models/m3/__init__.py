"""M3 · risk evidence (INTERFACES.md #4): compare a charge with the customer's own history.

``risk_features(txn, baseline, recent_txns=(), sessions=(), channel=None, txn_country=None,
customer_country=None) -> {features: {name: value}, lift_score: float | None, model_version}``

Features (plan v2 section 5): amount vs the customer's median and the p95 of the category, first time
at the merchant, unusual hour or channel, country mismatch, velocity in the last 24 hours, and a
login or a new device in the 30 minutes before the charge. They are EVIDENCE for the investigator and
the analyst ("8x your usual amount, first time at this merchant"); the lane rules decide.

``lift_score`` stays None unless the one-day lift experiment (``models/m3/train.py``) found a
significant lift over ``fraud_score`` on the transactions the rule does not cover; the result is in
``src/models/thresholds.yaml`` (key ``m3``) and ``docs/modelos/m3.md``. Pure Python.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Iterable

from models.common import read_thresholds, to_usd

MODEL_VERSION = "m3-evidence-1"
FAMILY_FALLBACK = "other"


def _usd(txn: Any) -> float | None:
    if getattr(txn, "amount_usd", None) is not None:
        return abs(float(txn.amount_usd))
    return to_usd(abs(float(txn.amount)), txn.currency)


def _dt(txn: Any) -> datetime:
    return datetime.combine(txn.local_date, time.fromisoformat(txn.local_time))


def risk_features(txn: Any, baseline: Any, recent_txns: Iterable[Any] = (), sessions: Iterable[Any] = (),
                  channel: str | None = None, txn_country: str | None = None,
                  customer_country: str | None = None) -> dict:
    amount_usd = _usd(txn)
    f: dict[str, Any] = {"amount_usd": round(amount_usd, 2) if amount_usd is not None else None}
    has_history = bool(getattr(baseline, "n_txns", 0))
    med = getattr(baseline, "median_amount_usd", None)
    if amount_usd is not None and med:
        f["amount_vs_median"] = round(amount_usd / float(med), 2)
    cats = {c[0]: c for c in (getattr(baseline, "category_stats", ()) or ())}
    cat = cats.get((txn.merchant_category or FAMILY_FALLBACK).lower())
    if amount_usd is not None and cat and cat[3]:
        f["amount_vs_cat_p95"] = round(amount_usd / float(cat[3]), 2)
    known = tuple(getattr(baseline, "known_merchant_ids", ()) or ())
    if has_history and known and str(txn.merchant_id).startswith("M-"):
        f["first_time_merchant"] = txn.merchant_id not in known
    hours = getattr(baseline, "usual_hours", None)
    if hours:
        h = int(txn.local_time[:2])
        f["unusual_hour"] = not (hours[0] <= h <= hours[1])
    channels = tuple(getattr(baseline, "usual_channels", ()) or ())
    if channel and channels:
        f["unusual_channel"] = channel not in channels
    if txn_country and customer_country:
        f["foreign"] = txn_country != customer_country
    t0 = _dt(txn)
    recent = [r for r in recent_txns if timedelta(0) <= t0 - _dt(r) <= timedelta(hours=24)]
    f["velocity_24h"] = len(recent)
    amounts = [a for a in (_usd(r) for r in recent) if a is not None]
    f["amount_24h_usd"] = round(sum(amounts), 2)
    before = [s for s in sessions if timedelta(0) <= t0 - s.started_at.replace(tzinfo=None) <= timedelta(minutes=30)
              or s.started_at.replace(tzinfo=None) <= t0 <= s.ended_at.replace(tzinfo=None)]
    f["login_before_30m"] = any(s.has_login for s in before)
    f["new_device_before"] = any(s.first_seen_device for s in before)
    thr = read_thresholds().get("m3") or {}
    return {"features": f, "lift_score": None, "model_version": thr.get("model_version") or MODEL_VERSION}
