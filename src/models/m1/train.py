"""Train M1 (``make train``): LightGBM LambdaRank over queries generated from gold transactions.

Ground truth is planted (plan v2 sections 5 and 11): real complaints can't be linked to real
transactions (the v1.4 analysis showed every complaint cites another customer's product), so each
training query takes a real charge of a real customer and generates what a customer would say about
it, with realistic noise: rounded amounts ("como 450"), +-1..5 day errors, merchant nicknames
("el super"), 35-40% missing fields, a wrong card now and then. Hard negatives are added (same
merchant on another day, same amount at another merchant) and 12% of the queries describe a charge
that is NOT among the candidates ("no-match"). Everything generated is synthetic.

Time split by the charge date: train < 2025-12-01 <= validation < 2026-03-01 <= test. Calibration
("none" logit + isotonic) and the band thresholds are chosen on validation; the frozen test period
compares M1 with the v1.4 rule (m1-rule-0, baseline B1). The rule stays primary unless M1 is at
least as good on wrong confirmations and on top-1.

    python scripts/train.py m1          # or: make train
"""

from __future__ import annotations

import math
import random
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import numpy as np
import yaml

from models.common import ARTIFACTS_DIR, Isotonic, LgbmModel, write_threshold_section
from models.m1 import features

VERSION = "m1-lgbm-1"
REPO_ROOT = Path(__file__).resolve().parents[3]
GOLD_DIR = REPO_ROOT / "data" / "gold"
REPORT_DIR = REPO_ROOT / "data" / "eval"
DOC = REPO_ROOT / "docs" / "modelos" / "m1.md"
TRAIN_END = date(2025, 12, 1)
VAL_END = date(2026, 3, 1)
SEARCH_WINDOW_DAYS = 7     # as the orchestrator: +-7 days around a said date
LOOKBACK_DAYS = 30         # otherwise the last 30 days up to the reference date
CHARGE_TYPES = ("purchase", "payment", "withdrawal", "fee", "adjustment", "transfer")
N_QUERIES = {"train": 30000, "calib": 12000, "val": 8000, "test": 8000}
NO_MATCH_RATE = 0.12
HARD_NEG_RATE = 0.4
SEED = 20260929


# ---------------------------------------------------------------- data

def load_charges(gold_dir: Path = GOLD_DIR) -> tuple[dict[str, list[dict]], dict[str, str]]:
    con = duckdb.connect()
    g = lambda t: (gold_dir / t / "data.parquet").as_posix()  # noqa: E731
    cols = ("txn_id, customer_id, product_id, card_last4, local_date, local_time, amount, currency, merchant_id, "
            "merchant_name, merchant_category, status, txn_type")
    types = ", ".join(f"'{t}'" for t in CHARGE_TYPES)
    rows = con.execute(f"""
        SELECT {cols} FROM read_parquet('{g('transactions_enriched')}')
        WHERE txn_type IN ({types}) AND status <> 'declined'
        UNION ALL
        SELECT {cols} FROM read_parquet('{g('fee_charges')}')
        ORDER BY customer_id, local_date, local_time, txn_id
    """).fetchall()
    names = [c.strip() for c in cols.split(",")]
    by_customer: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = dict(zip(names, r))
        d["amount"] = Decimal(str(d["amount"]))
        by_customer[d["customer_id"]].append(d)
    country = dict(con.execute(f"SELECT customer_id, country FROM read_parquet('{g('customer_profile')}')").fetchall())
    con.close()
    return by_customer, country


# ---------------------------------------------------------------- what a customer would say

def _fmt_amount(x: float) -> str:
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else f"{x:.2f}"


def _say_amount(rng: random.Random, amount: float) -> str | None:
    r = rng.random()
    if r < 0.35:
        return None
    if r < 0.60:
        return _fmt_amount(amount)
    if r < 0.85:
        step = 10 if amount < 1000 else 100 if amount < 10000 else 1000 if amount < 200000 else 10000
        return _fmt_amount(max(step, round(amount / step) * step))
    noisy = amount * (1 + rng.choice([-1, 1]) * rng.uniform(0.01, 0.06))
    step = 1 if noisy < 100 else 10 if noisy < 10000 else 100
    return _fmt_amount(max(step, round(noisy / step) * step))


def _say_date(rng: random.Random, d: date) -> tuple[str | None, bool | None]:
    r = rng.random()
    if r < 0.35:
        return None, None
    if r < 0.75:
        shift = 0
    elif r < 0.92:
        shift = rng.choice([-1, 1])
    else:
        shift = rng.choice([-1, 1]) * rng.randint(2, 5)
    return (d + timedelta(days=shift)).isoformat(), rng.random() < 0.5


def _nickname(rng: random.Random, name: str, category: str) -> str:
    fam = features.FAMILY.get((category or "").lower(), "retail")
    words = [w for w in features.fold(name).split() if len(w) >= 3 and w not in ("del", "las", "los", "sas", "ltda")]
    r = rng.random()
    if r < 0.35 or not words:
        hints = features.HINTS.get(fam) or ("tienda",)
        text = rng.choice(hints)
        return rng.choice(["", "el ", "la ", "en el ", "na ", "no "]) + text
    if r < 0.75:
        return rng.choice(words)
    return " ".join(words[: rng.randint(2, max(2, len(words)))])


def _typo(rng: random.Random, text: str) -> str:
    if len(text) < 5:
        return text
    i = rng.randrange(1, len(text) - 1)
    return text[:i] + text[i + 1:] if rng.random() < 0.5 else text[:i] + text[i + 1] + text[i] + text[i + 2:]


def _say_merchant(rng: random.Random, t: dict) -> str | None:
    r = rng.random()
    if r < 0.40:
        return None
    name = t["merchant_name"] or ""
    if r < 0.65:
        text = features.fold(name) if rng.random() < 0.7 else name.title()
    else:
        text = _nickname(rng, name, t["merchant_category"])
    return _typo(rng, text) if rng.random() < 0.10 else text


def make_slots(rng: random.Random, t: dict) -> dict:
    amount, (said_date, rel) = _say_amount(rng, float(t["amount"])), _say_date(rng, t["local_date"])
    merchant = _say_merchant(rng, t)
    r = rng.random()
    last4 = None
    if t.get("card_last4") and r < 0.12:
        last4 = t["card_last4"]
    elif r < 0.15:
        last4 = f"{rng.randint(0, 9999):04d}"
    if not (amount or said_date or merchant):
        amount = _fmt_amount(float(t["amount"]))
    return {"amount": amount, "currency": None, "date": said_date, "date_is_relative": rel,
            "merchant_text": merchant, "card_last4": last4}


def _window(slots: dict, ref: date) -> tuple[date, date]:
    if slots.get("date"):
        said = date.fromisoformat(slots["date"])
        return said - timedelta(days=SEARCH_WINDOW_DAYS), min(said + timedelta(days=SEARCH_WINDOW_DAYS), ref)
    return ref - timedelta(days=LOOKBACK_DAYS), ref


def _hard_negatives(rng: random.Random, t: dict, merchants: list[tuple[str, str, str]], lo: date, hi: date) -> list[dict]:
    out = []
    span = (hi - lo).days
    for k in range(rng.choice([1, 1, 2])):
        neg = dict(t)
        neg["txn_id"] = f"HN-{t['txn_id']}-{k}"
        if rng.random() < 0.5:  # same merchant, another day, another amount
            shift = rng.choice([-1, 1]) * rng.randint(2, 10)
            neg["local_date"] = min(max(t["local_date"] + timedelta(days=shift), lo), hi) if span else t["local_date"]
            neg["amount"] = (t["amount"] * Decimal(str(round(rng.uniform(0.7, 1.3), 3)))).quantize(Decimal("0.01"))
        else:  # same amount (+-1%), another merchant, close date
            mid, mname, mcat = rng.choice(merchants)
            neg["merchant_id"], neg["merchant_name"], neg["merchant_category"] = mid, mname, mcat
            neg["amount"] = (t["amount"] * Decimal(str(round(rng.uniform(0.99, 1.01), 4)))).quantize(Decimal("0.01"))
            neg["local_date"] = min(max(t["local_date"] + timedelta(days=rng.randint(-3, 3)), lo), hi)
        neg["synthetic_negative"] = True
        out.append(neg)
    return out


def build_queries(split: str, n: int, by_customer: dict[str, list[dict]], country: dict[str, str], seed: int) -> list[dict]:
    rng = random.Random(f"{seed}-{split}")
    lo_date, hi_date = {"train": (date(2023, 7, 1), TRAIN_END), "calib": (date(2023, 7, 1), TRAIN_END),
                        "val": (TRAIN_END, VAL_END), "test": (VAL_END, date(2026, 6, 18))}[split]
    targets = [(cid, i) for cid, rows in by_customer.items() for i, t in enumerate(rows)
               if lo_date <= t["local_date"] < hi_date]
    merchants = sorted({(t["merchant_id"], t["merchant_name"], t["merchant_category"])
                        for rows in by_customer.values() for t in rows if str(t["merchant_id"]).startswith("M-")})
    queries = []
    attempts = 0
    while len(queries) < n and attempts < n * 5:
        attempts += 1
        cid, i = rng.choice(targets)
        rows = by_customer[cid]
        t = rows[i]
        slots = make_slots(rng, t)
        ref = t["local_date"] + timedelta(days=rng.randint(0, 10))
        lo, hi = _window(slots, ref)
        cands = [dict(r) for r in rows if lo <= r["local_date"] <= hi]
        no_match = rng.random() < NO_MATCH_RATE
        if no_match:
            cands = [c for c in cands if c["txn_id"] != t["txn_id"]]
        elif rng.random() < HARD_NEG_RATE:
            cands += _hard_negatives(rng, t, merchants, lo, hi)
        if not cands:
            continue
        rng.shuffle(cands)
        queries.append({"customer_id": cid, "country": country.get(cid, "?"), "slots": slots, "target": t["txn_id"],
                        "no_match": no_match, "candidates": cands, "n_slots": features.slots_present(slots)})
    return queries


def matrix(queries: list[dict]) -> tuple[np.ndarray, np.ndarray, list[int]]:
    xs, ys, groups = [], [], []
    for q in queries:
        rows = features.build(q["slots"], q["candidates"])
        xs.extend(rows)
        ys.extend(1 if c["txn_id"] == q["target"] and not q["no_match"] else 0 for c in q["candidates"])
        groups.append(len(rows))
    return np.array(xs, dtype=float), np.array(ys, dtype=int), groups


# ---------------------------------------------------------------- calibration and thresholds

def _split_scores(scores: np.ndarray, groups: list[int]) -> list[np.ndarray]:
    out, k = [], 0
    for g in groups:
        out.append(scores[k:k + g])
        k += g
    return out


def _raw_probs(s: np.ndarray, c: float) -> np.ndarray:
    m = max(s.max(), c)
    e = np.exp(s - m)
    return e / (e.sum() + math.exp(c - m))


def fit_none_logit(per_query: list[np.ndarray], labels: list[np.ndarray]) -> float:
    best, best_c = float("inf"), 0.0
    for c in np.linspace(-8, 8, 161):
        nll = 0.0
        for s, y in zip(per_query, labels):
            p = _raw_probs(s, c)
            nll -= math.log(max(1e-12, float(p[y.argmax()]) if y.any() else 1 - float(p.sum())))
        if nll < best:
            best, best_c = nll, float(c)
    return best_c


def fit_isotonic(raw: list[np.ndarray], labels: list[np.ndarray]) -> Isotonic:
    from sklearn.isotonic import IsotonicRegression

    x = np.concatenate(raw)
    y = np.concatenate(labels)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip").fit(x, y)
    return Isotonic(list(iso.X_thresholds_), list(iso.y_thresholds_))


def calibrated(raw: np.ndarray, iso: Isotonic) -> np.ndarray:
    cal = np.array([iso(float(p)) for p in raw])
    total = cal.sum()
    return cal * (0.999 / total) if total > 0.999 else cal


MAX_WRONG_CONFIRMATIONS = 0.009   # of ALL queries (plan v2: "wrong confirmations under 1%"), with margin
MAX_WRONG_AMONG_CONFIRMED = 0.05  # and when it says "sure", it is right at least 95% of the time
MIN_CHOOSE_THRESHOLD = 0.15


def choose_thresholds(p_top: np.ndarray, correct: np.ndarray, in_top3: np.ndarray) -> dict:
    """confirm: the smallest t such that, on validation, confirming every query with p_top >= t gives
    wrong confirmations <= 0.9% of all queries and <= 5% of the confirmed ones (the customer still
    confirms the charge before anything is filed). choose: below confirm, showing up to 3 charges
    must contain the right one in at least half of the queries (never below 0.15); else ask."""
    n = len(p_top)
    confirm = 0.99
    for t in np.arange(0.50, 0.995, 0.01):
        sel = p_top >= t
        if sel.sum() < 30:
            continue
        wrong_overall = float(((~correct) & sel).sum()) / n
        wrong_among = 1 - float(correct[sel].mean())
        if wrong_overall <= MAX_WRONG_CONFIRMATIONS and wrong_among <= MAX_WRONG_AMONG_CONFIRMED:
            confirm = float(round(t, 2))
            break
    choose = MIN_CHOOSE_THRESHOLD
    for t in np.arange(MIN_CHOOSE_THRESHOLD, confirm, 0.01):
        sel = (p_top >= t) & (p_top < confirm)
        if sel.sum() >= 30 and in_top3[sel].mean() >= 0.5:
            choose = float(round(t, 2))
            break
    return {"confirm_threshold": confirm, "choose_threshold": choose}


CONF_FEATURES = tuple(features.FEATURES) + ("p_raw_top", "score_margin", "n_cands")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def _split_rows(X: np.ndarray, groups: list[int]) -> list[np.ndarray]:
    out, k = [], 0
    for g in groups:
        out.append(X[k:k + g])
        k += g
    return out


def conf_row(scores: np.ndarray, feats: np.ndarray, c: float) -> list[float]:
    p = _raw_probs(scores, c)
    top = int(np.argmax(scores))
    srt = np.sort(scores)[::-1]
    margin = float(srt[0] - (srt[1] if len(srt) > 1 else c))
    return list(map(float, feats[top])) + [float(p[top]), margin, float(len(scores))]


def conf_matrix(per_scores, per_labels, per_feats, c: float) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for sc, lab, f in zip(per_scores, per_labels, per_feats):
        xs.append(conf_row(sc, f, c))
        ys.append(int(bool(lab.any()) and int(np.argmax(sc)) == int(lab.argmax())))
    return np.array(xs, dtype=float), np.array(ys, dtype=int)


def calibrated_query(scores: np.ndarray, conf_prob: float, c: float, iso: Isotonic) -> np.ndarray:
    """Calibrated P per candidate: the top one gets iso(confidence); the others keep their share of
    the softmax relative to the top one; the total stays below 1 ("none of these")."""
    raw = _raw_probs(scores, c)
    top = int(np.argmax(scores))
    p_top = iso(float(conf_prob))
    out = raw * (p_top / max(raw[top], 1e-12))
    total = out.sum()
    return out * (0.999 / total) if total > 0.999 else out


# ---------------------------------------------------------------- evaluation

def evaluate(queries: list[dict], ranked_list: list[dict], bands_conf: tuple[float, float] | None = None) -> dict:
    """Metrics of one ranker over queries. ``ranked_list[i]`` = rank_candidates output."""
    rows = []
    for q, out in zip(queries, ranked_list):
        ranked = [r["txn_id"] for r in out["ranked"]]
        band = out["band"]
        hit1 = (not q["no_match"]) and ranked[:1] == [q["target"]]
        hit3 = (not q["no_match"]) and q["target"] in ranked[:3]
        rows.append({"country": q["country"], "n_slots": q["n_slots"], "no_match": q["no_match"], "hit1": hit1,
                     "hit3": hit3, "confirm": band == "confirm", "ask": band == "ask",
                     "wrong_confirm": band == "confirm" and not hit1})
    return summarize(rows)


def _rate(vals: list[bool]) -> float | None:
    return round(sum(vals) / len(vals), 4) if vals else None


def _ci(vals: list[bool], rng: np.random.Generator, n_boot: int = 1000) -> list[float] | None:
    if not vals:
        return None
    a = np.array(vals, dtype=float)
    boots = [a[rng.integers(0, len(a), len(a))].mean() for _ in range(n_boot)]
    return [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)]


def summarize(rows: list[dict]) -> dict:
    rng = np.random.default_rng(7)
    match = [r for r in rows if not r["no_match"]]
    nomatch = [r for r in rows if r["no_match"]]
    confirmed = [r for r in rows if r["confirm"]]
    out = {
        "n_queries": len(rows), "n_match": len(match), "n_no_match": len(nomatch),
        "top1": _rate([r["hit1"] for r in match]), "top1_ci95": _ci([r["hit1"] for r in match], rng),
        "recall_at_3": _rate([r["hit3"] for r in match]),
        "wrong_confirmation_rate": _rate([r["wrong_confirm"] for r in rows]),
        "wrong_confirmation_ci95": _ci([r["wrong_confirm"] for r in rows], rng),
        "wrong_among_confirmed": _rate([r["wrong_confirm"] for r in confirmed]),
        "confirm_rate": _rate([r["confirm"] for r in rows]),
        "clarification_rate": _rate([not r["confirm"] for r in rows]),
        "no_match_detection": _rate([not r["confirm"] for r in nomatch]),
    }
    out["by_country"] = {c: {"n": len(g), "top1": _rate([r["hit1"] for r in g if not r["no_match"]]),
                             "wrong_confirmation_rate": _rate([r["wrong_confirm"] for r in g])}
                         for c in sorted({r["country"] for r in rows}) for g in [[r for r in rows if r["country"] == c]]}
    out["by_slots"] = {str(k): {"n": len(g), "top1": _rate([r["hit1"] for r in g if not r["no_match"]]),
                                "wrong_confirmation_rate": _rate([r["wrong_confirm"] for r in g])}
                       for k in sorted({r["n_slots"] for r in rows}) for g in [[r for r in rows if r["n_slots"] == k]]}
    return out


# ---------------------------------------------------------------- main

def train(gold_dir: Path = GOLD_DIR, n_queries: dict | None = None, write: bool = True, verbose: bool = True,
          extra_train: list[dict] | None = None, version: str = VERSION) -> dict:
    """``extra_train``: labelled queries from the learning loop (confirmed charges), appended to the
    training split only; validation and the frozen test stay the same so versions are comparable."""
    import warnings

    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import lightgbm as lgb

    t0 = time.time()
    n_queries = n_queries or N_QUERIES
    log = (lambda m: print(f"[m1 {time.time() - t0:5.1f}s] {m}", flush=True)) if verbose else (lambda m: None)
    by_customer, country = load_charges(gold_dir)
    meta_file = gold_dir / "_meta.yaml"
    data_meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    log(f"{sum(map(len, by_customer.values())):,} charges of {len(by_customer):,} customers "
        f"({data_meta.get('data_source', '?')})")
    q = {s: build_queries(s, n_queries[s], by_customer, country, SEED) for s in ("train", "calib", "val", "test")}
    if extra_train:
        q["train"] = q["train"] + list(extra_train)
        log(f"+ {len(extra_train)} labelled queries from the learning loop")
    log("queries: " + ", ".join(f"{s} {len(v):,}" for s, v in q.items()))
    X, y, g = {}, {}, {}
    for s in q:
        X[s], y[s], g[s] = matrix(q[s])
    log("feature rows: " + ", ".join(f"{k} {len(v):,}" for k, v in y.items()))

    model = lgb.LGBMRanker(objective="lambdarank", n_estimators=600, learning_rate=0.05, num_leaves=15,
                           min_child_samples=25, subsample=0.8, subsample_freq=1, colsample_bytree=0.9,
                           label_gain=[0, 1], random_state=7, verbose=-1)
    model.fit(X["train"], y["train"], group=g["train"], eval_set=[(X["val"], y["val"])], eval_group=[g["val"]],
              eval_at=[1, 3], feature_name=list(features.FEATURES),
              callbacks=[lgb.early_stopping(50, verbose=False)])
    booster = model.booster_
    best_iter = model.best_iteration_ or booster.current_iteration()
    log(f"LambdaRank: {best_iter} trees")

    art = ARTIFACTS_DIR / "m1" / version if write else Path(tempfile.mkdtemp(prefix="m1-candidate-"))
    art.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(art / "model.txt"), num_iteration=best_iter)
    pure = LgbmModel.load(art / "model.txt")
    # parity: the pure-Python evaluator must give LightGBM's scores
    sample = X["val"][:500]
    ref_scores = booster.predict(sample, num_iteration=best_iter)
    ours = np.array([pure.predict(list(x)) for x in sample])
    parity = float(np.max(np.abs(ref_scores - ours)))
    if parity > 1e-6:
        raise RuntimeError(f"pure-Python LightGBM evaluator differs from lightgbm by {parity}")

    def per_query(split):
        sc = booster.predict(X[split], num_iteration=best_iter)
        labels = [np.array(v).astype(int) for v in _split_scores(y[split].astype(float), g[split])]
        feats = _split_rows(X[split], g[split])
        return _split_scores(sc, g[split]), labels, feats

    # "none of these" logit, fitted on the calibration split (never seen by LambdaRank)
    s_cal, l_cal, f_cal = per_query("calib")
    c = fit_none_logit(s_cal, l_cal)
    # Confidence model: P(top candidate is the charge) from its ABSOLUTE fit (amount gap, date gap,
    # merchant similarity ...) plus the score margin. A constant "none" logit can't tell a bad best
    # candidate from a good one.
    Xc, yc = conf_matrix(s_cal, l_cal, f_cal, c)
    conf = lgb.LGBMClassifier(objective="binary", n_estimators=250, learning_rate=0.05, num_leaves=15,
                              min_child_samples=30, subsample=0.8, subsample_freq=1, colsample_bytree=0.9,
                              random_state=13, verbose=-1)
    conf.fit(Xc, yc, feature_name=list(CONF_FEATURES))
    conf.booster_.save_model(str(art / "confidence.txt"))
    conf_pure = LgbmModel.load(art / "confidence.txt")
    ref_c = conf.predict_proba(Xc[:300])[:, 1]
    ours_c = np.array([_sigmoid(conf_pure.predict(list(x))) for x in Xc[:300]])
    parity = max(parity, float(np.max(np.abs(ref_c - ours_c))))
    if parity > 1e-6:
        raise RuntimeError(f"pure-Python evaluator differs from lightgbm by {parity}")

    s_val, l_val, f_val = per_query("val")
    Xv, yv = conf_matrix(s_val, l_val, f_val, c)
    conf_val = conf.predict_proba(Xv)[:, 1]
    iso = fit_isotonic([conf_val], [yv])
    cal_val = [calibrated_query(sv, conf_v, c, iso) for sv, conf_v in zip(s_val, conf_val)]
    p_top = np.array([p.max() for p in cal_val])
    correct = np.array([bool(lab.any()) and int(p.argmax()) == int(lab.argmax()) for p, lab in zip(cal_val, l_val)])
    in_top3 = np.array([bool(lab.any()) and int(lab.argmax()) in list(np.argsort(-p)[:3]) for p, lab in zip(cal_val, l_val)])
    thr = choose_thresholds(p_top, correct, in_top3)
    raw_top = np.array([_raw_probs(sv, c).max() for sv in s_val])
    ece_raw = _ece(raw_top, correct.astype(float))
    ece = _ece(p_top, correct.astype(float))
    log(f"calibration: none_logit {c:.2f}, ECE(top-1) raw {ece_raw:.4f} -> calibrated {ece:.4f}; thresholds {thr}")
    (art / "calibration.yaml").write_text(yaml.safe_dump(
        {"version": version, "features": list(features.FEATURES), "conf_features": list(CONF_FEATURES),
         "none_logit": round(c, 4), "isotonic": iso.to_dict()}, sort_keys=False), encoding="utf-8")

    # ---- frozen test: M1 vs the v1.4 rule (B1), same queries
    from conversation import ranker as rule

    s_test, l_test, f_test = per_query("test")
    Xt, _yt = conf_matrix(s_test, l_test, f_test, c)
    conf_test = conf.predict_proba(Xt)[:, 1]
    m1_out = []
    for qq, st, cp in zip(q["test"], s_test, conf_test):
        p = calibrated_query(st, cp, c, iso)
        ranked = sorted(({"txn_id": cand["txn_id"], "p": round(float(pp), 4)} for cand, pp in zip(qq["candidates"], p)),
                        key=lambda r: (-r["p"], r["txn_id"]))
        top = ranked[0]["p"]
        band = "confirm" if top >= thr["confirm_threshold"] else "choose" if top >= thr["choose_threshold"] else "ask"
        m1_out.append({"ranked": ranked, "band": band})
    rule_out = [rule.rank_candidates(qq["slots"], [_as_rule_cand(cand) for cand in qq["candidates"]]) for qq in q["test"]]
    res_m1, res_rule = evaluate(q["test"], m1_out), evaluate(q["test"], rule_out)
    model_wins = (res_m1["wrong_confirmation_rate"] <= res_rule["wrong_confirmation_rate"]
                  and res_m1["top1"] >= res_rule["top1"])
    primary = "model" if model_wins else "rule"
    log(f"test M1: top1 {res_m1['top1']} wrong-conf {res_m1['wrong_confirmation_rate']} | "
        f"rule: top1 {res_rule['top1']} wrong-conf {res_rule['wrong_confirmation_rate']} -> primary {primary}")

    importance = sorted(zip(features.FEATURES, booster.feature_importance("gain", iteration=best_iter)),
                        key=lambda kv: -kv[1])
    report = {
        "model_version": version, "primary": primary, "data_source": data_meta.get("data_source"),
        "synthetic_queries": True, "seconds": round(time.time() - t0, 1),
        "split": {"train_before": TRAIN_END.isoformat(), "val_before": VAL_END.isoformat(), "test_from": VAL_END.isoformat()},
        "sizes": {s: {"queries": len(q[s]), "rows": int(len(y[s]))} for s in q},
        "trees": int(best_iter), "parity_max_abs_diff": parity,
        "calibration": {"none_logit": round(c, 4), "ece_raw": round(ece_raw, 4), "ece_calibrated": round(ece, 4),
                        "method": "LightGBM confidence model on a calibration split + isotonic on validation (top-1)"},
        "thresholds": thr, "test": {"m1": res_m1, "rule_m1_rule_0": res_rule},
        "feature_importance_gain": {k: round(float(v), 1) for k, v in importance},
        "decision_rule": "M1 is primary only if its wrong-confirmation rate <= the rule's and its top-1 >= the rule's",
    }
    if write:
        write_threshold_section("m1", {
            "model_version": version, "primary": primary, **thr,
            "chosen_on": "validation (2025-12-01..2026-02-28): wrong confirmations <= 0.9% of all queries and <= 5% of confirmed",
            "data_source": data_meta.get("data_source"),
        })
        (art / "meta.yaml").write_text(yaml.safe_dump({k: report[k] for k in (
            "model_version", "primary", "data_source", "split", "sizes", "trees", "calibration", "thresholds")},
            sort_keys=False), encoding="utf-8")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "m1_report.yaml").write_text(yaml.safe_dump(report, sort_keys=False, allow_unicode=True),
                                                   encoding="utf-8")
        write_doc(report)
    from models import m1 as runtime

    runtime.reload()
    return report


def _as_rule_cand(c: dict) -> dict:
    return {"txn_id": c["txn_id"], "amount": str(c["amount"]), "currency": c["currency"],
            "local_date": c["local_date"], "merchant_name": c["merchant_name"], "card_last4": c.get("card_last4")}


def _ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total, err = len(p), 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if sel.any():
            err += sel.sum() / total * abs(p[sel].mean() - y[sel].mean())
    return float(err)


def _pct(v) -> str:
    return "—" if v is None else f"{100 * v:.1f}%"


def write_doc(r: dict) -> None:
    m, b = r["test"]["m1"], r["test"]["rule_m1_rule_0"]
    lines = [
        "# M1 · Ranker de transacciones", "",
        f"Generado por `make train` (`src/models/m1/train.py`). Versión `{r['model_version']}`. "
        f"Fuente de datos: **{r['data_source']}**" + (" (réplica sintética: los números no son del dataset real)"
                                                       if r["data_source"] == "synthetic_replica" else "") + ".", "",
        "Las consultas son **sintéticas**: cada una toma un cargo real de un cliente (de la tabla gold) y genera lo que "
        "el cliente diría con ruido realista (montos redondeados, ±1–5 días, apodos del comercio, 35–40% de datos "
        "faltantes, tarjeta equivocada a veces). 12% describen un cargo que no está entre los candidatos.", "",
        f"- Partición temporal: entrenamiento < {r['split']['train_before']} ≤ validación < {r['split']['val_before']} ≤ prueba.",
        f"- Tamaños: " + ", ".join(f"{s} {v['queries']:,} consultas ({v['rows']:,} candidatos)" for s, v in r["sizes"].items()) + ".",
        f"- Modelo: LightGBM LambdaRank, {r['trees']} árboles; se evalúa en Python puro dentro de la Lambda "
        f"(diferencia máxima con lightgbm: {r['parity_max_abs_diff']:.1e}).",
        f"- Calibración: un modelo de confianza (LightGBM binario, entrenado en una partición de calibración que el "
        f"ranker no vio) estima P(el primero es el cargo) por su ajuste absoluto; isotónica en validación. "
        f"ECE del top-1 {r['calibration']['ece_raw']:.3f} (softmax crudo) → {r['calibration']['ece_calibrated']:.3f}.",
        f"- Umbrales elegidos en validación: confirmar si p ≥ {r['thresholds']['confirm_threshold']}, "
        f"ofrecer opciones si p ≥ {r['thresholds']['choose_threshold']}, si no preguntar.", "",
        "## Prueba congelada: M1 frente a la regla v1.4 (B1)", "",
        "| Métrica | M1 | Regla v1.4 |", "|---|---|---|",
        f"| Top-1 (consultas con cargo) | {_pct(m['top1'])} {m['top1_ci95']} | {_pct(b['top1'])} {b['top1_ci95']} |",
        f"| Recall@3 | {_pct(m['recall_at_3'])} | {_pct(b['recall_at_3'])} |",
        f"| Confirmaciones equivocadas (todas las consultas) | {_pct(m['wrong_confirmation_rate'])} {m['wrong_confirmation_ci95']} "
        f"| {_pct(b['wrong_confirmation_rate'])} {b['wrong_confirmation_ci95']} |",
        f"| Equivocadas entre las confirmadas | {_pct(m['wrong_among_confirmed'])} | {_pct(b['wrong_among_confirmed'])} |",
        f"| Tasa de confirmación directa | {_pct(m['confirm_rate'])} | {_pct(b['confirm_rate'])} |",
        f"| Tasa de aclaración (opciones o pregunta) | {_pct(m['clarification_rate'])} | {_pct(b['clarification_rate'])} |",
        f"| Detección de \"no existe\" | {_pct(m['no_match_detection'])} | {_pct(b['no_match_detection'])} |",
        "", f"**Decisión:** primario = `{r['primary']}`. Regla: {r['decision_rule']}.", "",
        "## Por país", "", "| País | n | Top-1 M1 | Top-1 regla | Conf. equivocadas M1 | Conf. equivocadas regla |",
        "|---|---|---|---|---|---|",
    ]
    for c, v in m["by_country"].items():
        bb = b["by_country"].get(c, {})
        lines.append(f"| {c} | {v['n']:,} | {_pct(v['top1'])} | {_pct(bb.get('top1'))} | "
                     f"{_pct(v['wrong_confirmation_rate'])} | {_pct(bb.get('wrong_confirmation_rate'))} |")
    lines += ["", "## Por cantidad de datos que dio el cliente", "", "| Datos | n | Top-1 M1 | Top-1 regla |", "|---|---|---|---|"]
    for k, v in m["by_slots"].items():
        lines.append(f"| {k} | {v['n']:,} | {_pct(v['top1'])} | {_pct(b['by_slots'].get(k, {}).get('top1'))} |")
    lines += ["", "## Importancia de variables (ganancia)", ""]
    lines += [f"- `{k}`: {v:,.0f}" for k, v in list(r["feature_importance_gain"].items())[:12]]
    lines += ["", "Limitaciones: consultas generadas (no hay quejas reales enlazadas a transacciones); en la réplica "
              "sintética hay pocos candidatos por ventana. Reentrenar con `make data` + `make pipeline` + `make train` "
              "sobre los datos reales antes de citar estos números."]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    train()
