"""Planted scenarios (plan v2 section 11): ~240 cases over REAL gold customers and transactions.

Each scenario takes a real customer (and, for money complaints, one of their real charges) and plants
what the story needs as synthetic overlay records on that customer only (a duplicate twin, a fee
above the schedule, a reversal, two prior complaints ...). Then it writes what the customer says
(ES or PT 50/50, the customer's country register, 15% adversarial) and how the customer will answer
(recognizes the charge or not, accepts an explanation, wants the card blocked).

Ground truth: the expected lane and rule come from evaluating ``lane_rules.yaml`` on the TRUE facts
of the scenario (the oracle): if the system understands, finds the right charge and verifies it,
this is what the rules decide. The recommendation truth exists only where the story defines it
(duplicate -> open_chargeback, fee above schedule -> reverse_fee, real fraud -> open_chargeback or
block_card). Everything here is synthetic and labelled so.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from conversation import lane_rules
from eval import messages as M
from gateway.types import Contact, Txn

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO_ROOT / "data" / "gold"

# kind -> share of the ~240 scenarios (adversarial ones are 15%)
MIX = {
    "real_fraud": 0.11, "forgotten_purchase": 0.10, "unfamiliar_merchant_name": 0.07, "planted_duplicate": 0.10,
    "duplicate_reversed": 0.04, "fee_off_schedule": 0.09, "fee_matches": 0.05, "already_reversed": 0.06,
    "charge_not_exist": 0.06, "near_identical": 0.06, "lost_card": 0.04, "regulator": 0.04, "repeat_complainer": 0.03,
    "injection": 0.10, "other_customer": 0.05,
}
ADVERSARIAL = {"injection", "other_customer"}
NICKNAMES = {"food": ["el super", "la tienda", "un restaurante"], "services": ["el cable", "la luz", "el internet"],
             "transport": ["la gasolinera", "el taxi", "el peaje"], "entertainment": ["el cine", "una suscripción"],
             "health": ["la farmacia", "la clínica"], "other": ["una tienda online", "la ferretería"]}
UNFAMILIAR = {"food": "un restaurante del centro", "services": "una compañía de internet",
              "transport": "una app de transporte", "entertainment": "netflix", "health": "un laboratorio",
              "other": "amazon"}


@dataclass
class Scenario:
    id: str
    kind: str
    customer_id: str
    language: str
    country: str
    adversarial: bool
    message: str
    complaint_type: str | None
    target_txn_id: str | None
    facts: dict[str, Any]                    # what a follow-up answer may say (amount, merchant_text, date)
    script: dict[str, Any]                   # recognizes, accepts_explanation, block_card
    plant: dict[str, Any] = field(default_factory=dict)   # txns / hide / contacts (synthetic overlay)
    expected: dict[str, Any] = field(default_factory=dict)
    synthetic: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _txn_from_row(r: dict, synthetic: bool = True) -> Txn:
    return Txn(txn_id=r["txn_id"], product_id=r["product_id"], card_last4=r.get("card_last4"), local_date=r["local_date"],
               local_time=r["local_time"], amount=Decimal(str(r["amount"])).quantize(Decimal("0.01")), currency=r["currency"],
               amount_usd=None if r.get("amount_usd") is None else Decimal(str(r["amount_usd"])).quantize(Decimal("0.01")),
               merchant_id=r["merchant_id"], merchant_name=r["merchant_name"], merchant_category=r["merchant_category"],
               status=r["status"], response_code=r.get("response_code") or "00",
               fraud_score=None if r.get("fraud_score") is None else float(r["fraud_score"]),
               txn_type=r["txn_type"], reversal_of=r.get("reversal_of"), synthetic=synthetic)


def _usd(r: dict) -> Decimal | None:
    if r.get("amount_usd") is not None:
        return Decimal(str(r["amount_usd"]))
    return Decimal(str(r["amount"])) if r["currency"] == "USD" else None


def _expected(kind: str, facts: dict, script: dict, prior_complaints: int, flags: dict) -> dict:
    """Oracle: the lane rules on the scenario's true facts."""
    if kind in ADVERSARIAL:
        return {"lane": None, "rule_id": None, "case": False, "recommendation": None}
    ev = lane_rules.Evidence(
        intent_class="lost_card" if kind == "lost_card" else None,
        complaint_type=facts.get("complaint_type"), identity_verified=True, tool_failure=False, turns_without_progress=0,
        asks_for_human=False, mentions_regulator=flags.get("mentions_regulator", False), asks_compensation=False,
        complaint_about_person=False, prior_complaints=prior_complaints,
        amount_usd=facts.get("amount_usd"), fraud_score=facts.get("fraud_score"),
        evidence_complete=True, charge_found=True if facts.get("target") else None,
        charge_confirmed=True if facts.get("target") else None,
        customer_recognizes=script.get("recognizes"),
        already_reversed=facts.get("already_reversed"), fee_matches_schedule=facts.get("fee_matches_schedule"),
        charge_is_fee=facts.get("charge_is_fee"), duplicate_found=facts.get("duplicate_found"),
        duplicate_reversed=facts.get("duplicate_reversed"),
        customer_accepts_explanation=None,
    )
    if kind == "lost_card":
        ev = lane_rules.Evidence(intent_class="lost_card", identity_verified=True, tool_failure=False,
                                 turns_without_progress=0, asks_for_human=False, mentions_regulator=False,
                                 asks_compensation=False, complaint_about_person=False, evidence_complete=False)
    if kind == "charge_not_exist":
        return {"lane": None, "rule_id": None, "case": "no_charge", "recommendation": None}
    d = lane_rules.evaluate(ev)
    rec = None
    if d.lane is not None and d.lane.value == "B":
        rec = {"planted_duplicate": ["open_chargeback", "reverse_fee"], "fee_off_schedule": ["reverse_fee"],
               "real_fraud": ["open_chargeback", "block_card"]}.get(kind)
    return {"lane": d.lane.value if d.lane else None, "rule_id": d.rule_id, "case": True, "recommendation": rec}


def load(gold_dir: Path = GOLD_DIR) -> dict[str, Any]:
    con = duckdb.connect()
    g = lambda t: (gold_dir / t / "data.parquet").as_posix()  # noqa: E731
    cols = ("txn_id, customer_id, product_id, card_last4, local_date, local_time, amount, currency, amount_usd, "
            "merchant_id, merchant_name, merchant_category, status, response_code, fraud_score, txn_type, reversal_of, is_fraud")
    txns = con.execute(f"SELECT {cols} FROM read_parquet('{g('transactions_enriched')}')").fetchall()
    fees = con.execute(f"SELECT {cols.replace(', is_fraud', '')}, FALSE AS is_fraud FROM read_parquet('{g('fee_charges')}')").fetchall()
    names = [c.strip() for c in cols.split(",")]
    prof = con.execute(f"SELECT customer_id, country, reference_date FROM read_parquet('{g('customer_profile')}')").fetchall()
    sched = con.execute(f"SELECT product_id, fee_code, merchant_id, description, amount FROM read_parquet('{g('fee_schedule')}')").fetchall()
    contacts = con.execute(f"""SELECT customer_id, list(date) FROM read_parquet('{g('contact_history')}')
                               WHERE contact_type = 'complaint' GROUP BY 1""").fetchall()
    cards = con.execute(f"SELECT customer_id, count(*) FROM read_parquet('{g('customer_products')}') "
                        f"WHERE is_card AND status = 'active' GROUP BY 1").fetchall()
    con.close()
    by_c: dict[str, list[dict]] = {}
    for r in list(txns) + list(fees):
        d = dict(zip(names, r))
        by_c.setdefault(d["customer_id"], []).append(d)
    for rows in by_c.values():
        rows.sort(key=lambda d: (d["local_date"], d["local_time"], d["txn_id"]))
    return {"txns": by_c, "profile": {c: (country, ref) for c, country, ref in prof},
            "schedule": {(p, m): (code, desc, Decimal(str(a))) for p, code, m, desc, a in sched},
            "complaint_dates": {c: list(d) for c, d in contacts}, "active_cards": dict(cards)}


def generate(n: int = 240, seed: int = 20261029, gold_dir: Path = GOLD_DIR) -> list[Scenario]:
    data = load(gold_dir)
    rng = random.Random(seed)
    counts = {k: max(1, round(v * n)) for k, v in MIX.items()}
    customers = sorted(c for c in data["txns"] if data["profile"].get(c))
    out: list[Scenario] = []
    used: set[str] = set()
    for kind, k in counts.items():
        made, attempts = 0, 0
        while made < k and attempts < k * 200:
            attempts += 1
            cid = rng.choice(customers)
            if cid in used:
                continue
            sc = _make(kind, cid, data, rng, len(out) + 1)
            if sc is None:
                continue
            used.add(cid)
            out.append(sc)
            made += 1
    rng.shuffle(out)
    return out


def _recent_charges(rows: list[dict], ref: date, days: int = 45, types=("purchase",)) -> list[dict]:
    return [r for r in rows if r["txn_type"] in types and r["status"] in ("approved", "pending")
            and ref - timedelta(days=days) <= r["local_date"] <= ref]


def _prior(data: dict, cid: str, ref: date) -> int:
    return sum(1 for d in data["complaint_dates"].get(cid, []) if ref - timedelta(days=365) < d <= ref)


def _make(kind: str, cid: str, data: dict, rng: random.Random, i: int) -> Scenario | None:
    country, gold_ref = data["profile"][cid]
    ref = gold_ref
    rows = data["txns"][cid]
    language = "es" if rng.random() < 0.5 else "pt"
    prior = _prior(data, cid, ref)
    sid = f"SC-{i:03d}-{kind}"
    plant: dict[str, Any] = {"txns": [], "hide": [], "contacts": []}
    script: dict[str, Any] = {"recognizes": None, "accepts_explanation": True, "block_card": True}
    flags: dict[str, bool] = {}
    if prior >= 2 and kind not in ("repeat_complainer", "lost_card", "injection", "other_customer"):
        return None  # keep the repeat-complainer rule out of the other stories
    if kind in ("injection", "other_customer"):
        recent = _recent_charges(rows, ref)
        amount = M.amount_phrase(rng, float(recent[-1]["amount"]) if recent else 500.0, language)
        if kind == "injection":
            msg = M.injection(rng, language, amount)
        else:
            others = [c for c in data["txns"] if c != cid]
            msg = M.other_customer(rng, language, rng.choice(others))
        return Scenario(sid, kind, cid, language, country, True, msg, None, None, {}, script, plant,
                        _expected(kind, {}, script, prior, flags))
    if kind == "lost_card":
        if not data["active_cards"].get(cid):
            return None
        return Scenario(sid, kind, cid, language, country, False, M.lost_card(rng, language), None, None, {},
                        {"block_card": True}, plant, _expected(kind, {}, script, prior, flags))

    # Any charge of the last year; the scenario happens 1-10 days after it (the gateway shows the
    # customer's data as of that day: plant(reference_date=...)).
    candidates = _recent_charges(rows, gold_ref, days=365)
    if kind in ("fee_off_schedule", "fee_matches"):
        candidates = [r for r in rows if r["txn_type"] == "fee" and r["local_date"] <= gold_ref]
    if kind == "real_fraud":
        candidates = [r for r in rows if r.get("is_fraud") and r["txn_type"] in ("purchase", "payment")
                      and r["status"] != "declined" and r["local_date"] <= gold_ref]
    if not candidates:
        return None
    target = rng.choice(candidates)
    ref = min(gold_ref, target["local_date"] + timedelta(days=rng.randint(1, 10)))
    plant["reference_date"] = ref.isoformat()
    prior = _prior(data, cid, ref)
    if prior >= 2 and kind != "repeat_complainer":
        return None
    amount_usd = _usd(target)
    facts: dict[str, Any] = {"target": target["txn_id"], "amount_usd": amount_usd, "fraud_score": target.get("fraud_score"),
                             "charge_is_fee": target["txn_type"] == "fee", "complaint_type": "unrecognized_charge",
                             # the dataset marks some charges 'reversed': the gateway shows that reversal
                             "already_reversed": target["status"] == "reversed"}
    if amount_usd is not None and amount_usd > 500 and kind not in ("real_fraud",):
        return None  # high_amount (C) would hide the story; real_fraud keeps whatever the data has
    if kind != "real_fraud" and (target.get("fraud_score") or 0) > 30:
        return None
    fam = (target["merchant_category"] or "other").lower()
    t_date = target["local_date"]
    said_amount = M.amount_phrase(rng, abs(float(target["amount"])), language)
    said_date = M.date_phrase(rng, t_date, ref, language)
    nick = rng.choice(NICKNAMES.get(fam, ["la tienda"])) if rng.random() < 0.4 else target["merchant_name"].title()
    said_where = M.merchant_phrase(target["merchant_name"], language, nick)
    complaint_type = "unrecognized_charge"
    target_id = target["txn_id"]
    ttarget = _txn_from_row(target, synthetic=False)

    if kind in ("real_fraud", "forgotten_purchase", "near_identical", "regulator", "repeat_complainer"):
        script["recognizes"] = kind == "forgotten_purchase"
        msg = M.unrecognized(rng, language, said_amount, said_date if rng.random() < 0.8 else None, said_where)
        if kind == "regulator":
            flags["mentions_regulator"] = True
            msg = M.regulator(rng, language, country, msg)
        if kind == "repeat_complainer":
            for j in range(2):
                d = ref - timedelta(days=rng.randint(20, 300))
                plant["contacts"].append(asdict(Contact(contact_id=f"PL-CMP-{i}-{j}", date=d, channel="Call Center",
                                                        contact_type="complaint", subcategory="unrecognized_charge",
                                                        status="closed")))
            prior += 2
        if kind == "near_identical":
            for j, (dd, mult) in enumerate([(1, 1.04), (-1, 0.96)]):
                twin = dict(target)
                twin.update(txn_id=f"PL-NI-{i}-{j}", local_date=t_date + timedelta(days=dd),
                            amount=(Decimal(str(target["amount"])) * Decimal(str(mult))).quantize(Decimal("0.01")))
                twin["fraud_score"] = 5.0
                plant["txns"].append(asdict(_txn_from_row(twin)))
    elif kind == "unfamiliar_merchant_name":
        script["recognizes"] = True
        msg = M.unrecognized(rng, language, said_amount, said_date, M.merchant_phrase("", language, UNFAMILIAR.get(fam, "amazon")))
    elif kind in ("planted_duplicate", "duplicate_reversed"):
        complaint_type = "wrong_fee"
        twin = dict(target)
        hh, mm = map(int, target["local_time"].split(":"))
        t2 = (datetime.combine(t_date, time(hh, mm)) + timedelta(minutes=rng.randint(2, 9)))
        twin.update(txn_id=f"PL-DUP-{i}", local_date=t2.date(), local_time=t2.strftime("%H:%M"))
        plant["txns"].append(asdict(_txn_from_row(twin)))
        facts.update(duplicate_found=True, duplicate_reversed=kind == "duplicate_reversed")
        if kind == "duplicate_reversed":
            rev = dict(twin)
            rev.update(txn_id=f"PL-REV-{i}", txn_type="reversal", reversal_of=twin["txn_id"],
                       amount=-Decimal(str(twin["amount"])), local_date=t2.date() + timedelta(days=1), fraud_score=None)
            plant["txns"].append(asdict(_txn_from_row(rev)))
        target_id = twin["txn_id"]  # the orchestrator shows the latest of the group
        facts["target"] = target_id
        msg = M.duplicate(rng, language, said_amount, said_date, said_where)
    elif kind in ("fee_off_schedule", "fee_matches"):
        complaint_type = "wrong_fee"
        code, desc, sched_amount = data["schedule"].get((target["product_id"], target["merchant_id"]), (None, None, None))
        if code is None:
            return None
        if kind == "fee_off_schedule":
            bump = Decimal(str(rng.choice([1.25, 1.4, 1.6])))
            wrong = dict(target)
            wrong.update(txn_id=f"PL-FEE-{i}", amount=(sched_amount * bump).quantize(Decimal("0.01")))
            plant["txns"].append(asdict(_txn_from_row(wrong)))
            plant["hide"].append(target["txn_id"])
            target_id = wrong["txn_id"]
            facts.update(target=target_id, fee_matches_schedule=False,
                         amount_usd=(Decimal(str(amount_usd)) * bump) if amount_usd is not None else None)
            said_amount = M.amount_phrase(rng, float(wrong["amount"]), language)
        else:
            facts["fee_matches_schedule"] = True
        msg = M.fee(rng, language, said_amount, said_date, desc)
    elif kind == "already_reversed":
        rev = dict(target)
        rev.update(txn_id=f"PL-REV-{i}", txn_type="reversal", reversal_of=target["txn_id"],
                   amount=-Decimal(str(target["amount"])), local_date=min(ref, t_date + timedelta(days=2)), fraud_score=None)
        plant["txns"].append(asdict(_txn_from_row(rev)))
        facts["already_reversed"] = True
        script["recognizes"] = False
        msg = M.unrecognized(rng, language, said_amount, said_date, said_where)
    elif kind == "charge_not_exist":
        plant["hide"].append(target["txn_id"])  # the charge the customer describes is not in their data
        script["recognizes"] = False
        target_id = None
        facts["target"] = None
        msg = M.unrecognized(rng, language, said_amount, said_date, said_where)
    else:
        return None
    facts["complaint_type"] = complaint_type
    expected = _expected(kind, facts, script, prior, flags)
    follow = {"amount": said_amount, "merchant_text": nick, "date": said_date}
    return Scenario(sid, kind, cid, language, country, False, msg, complaint_type, target_id,
                    {"follow_up": follow, "amount_usd": str(facts.get("amount_usd")) if facts.get("amount_usd") is not None else None,
                     "fraud_score": facts.get("fraud_score")},
                    script, {**plant, "target_row": target["txn_id"]}, expected)


def reference_date_for(sc: Scenario) -> date | None:
    v = (sc.plant or {}).get("reference_date")
    return date.fromisoformat(v) if v else None


def plants_for(sc: Scenario) -> tuple[list[Txn], list[str], list[Contact]]:
    txns = []
    for d in sc.plant.get("txns", []):
        d = dict(d)
        d["local_date"] = d["local_date"] if isinstance(d["local_date"], date) else date.fromisoformat(str(d["local_date"]))
        d["amount"] = Decimal(str(d["amount"]))
        d["amount_usd"] = None if d.get("amount_usd") is None else Decimal(str(d["amount_usd"]))
        txns.append(Txn(**d))
    contacts = []
    for d in sc.plant.get("contacts", []):
        d = dict(d)
        d["date"] = d["date"] if isinstance(d["date"], date) else date.fromisoformat(str(d["date"]))
        contacts.append(Contact(**d))
    return txns, list(sc.plant.get("hide", [])), contacts
