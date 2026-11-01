"""M2 evaluation helpers: metrics, calibration error, the team-written test set and baselines.

Offline only (``python -m models.m2.train``, ``make eval``); numpy is used for the bootstrap.
The runtime classifier never imports this module.

Baselines on the same test set (plan v2, section 11):
- B1 keywords: ``conversation.classifier.classify`` (``m2-keywords-0``, the stand-in M2 replaces).
- B2 zero-shot LLM: ``evaluate_zero_shot(client)`` runs prompt ``m2_zero_shot`` through any
  object with the #8 interface ``complete(prompt_id, variables, output_schema=None,
  model_role="chat") -> {"output": {...}, ...}``. It needs a model (LLM_PROVIDER=anthropic or
  bedrock); this repository has none offline, so the report marks B2 as not run.
"""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from models.m2.model import CLASSES

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_SET = REPO_ROOT / "eval" / "test_messages" / "m2_test_set.yaml"
# shared prompt folder: src/llm and conversation.llm_port both search src/prompts/ first
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
REQUIRED_KEYS = ("id", "text", "label", "language", "country", "adversarial")
LANGUAGES = ("es", "pt", "mixed")
COUNTRIES = ("MX", "CO", "AR")
INVALID = "invalid"

ZERO_SHOT_PROMPT_ID = "m2_zero_shot"
ZERO_SHOT_SCHEMA = {
    "type": "object",
    "properties": {"intent_class": {"type": "string", "enum": list(CLASSES)}},
    "required": ["intent_class"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- test set

def load_test_set(path: Path = TEST_SET) -> list[dict]:
    """Items of the team-written test set, validated (keys, labels, unique ids and texts)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if data.get("synthetic") is not True:
        raise ValueError(f"{path}: must declare synthetic: true")
    items = data.get("items") or []
    ids, texts = set(), set()
    for it in items:
        missing = [k for k in REQUIRED_KEYS if k not in it]
        if missing:
            raise ValueError(f"{path}: item {it.get('id')!r} lacks {missing}")
        if it["label"] not in CLASSES:
            raise ValueError(f"{path}: item {it['id']} has unknown label {it['label']!r}")
        if it["language"] not in LANGUAGES or it["country"] not in COUNTRIES:
            raise ValueError(f"{path}: item {it['id']} has bad language/country")
        if it["id"] in ids or it["text"] in texts:
            raise ValueError(f"{path}: duplicate id or text at {it['id']}")
        ids.add(it["id"])
        texts.add(it["text"])
    return items


def _trigrams(key: str) -> frozenset:
    padded = f" {key} "
    return frozenset(padded[i:i + 3] for i in range(len(padded) - 2))


def overlap_with_training(test_texts: list[str], train_texts: list[str], threshold: float = 0.9) -> list[dict]:
    """Test texts that also appear in the training texts: exact match after normalization
    (``features.dedup_key``: lowercase, no accents, digits as 0, no punctuation) or character
    trigram Jaccard similarity above ``threshold``. Returns one row per offending test text."""
    from models.m2.features import dedup_key

    train_keys = [dedup_key(t) for t in train_texts]
    exact = {}
    for k, t in zip(train_keys, train_texts):
        exact.setdefault(k, t)
    grams = [(_trigrams(k), t) for k, t in zip(train_keys, train_texts)]
    by_size: dict[int, list[int]] = {}
    for i, (g, _) in enumerate(grams):
        by_size.setdefault(len(g), []).append(i)
    out = []
    for text in test_texts:
        key = dedup_key(text)
        if key in exact:
            out.append({"text": text, "kind": "exact", "similarity": 1.0, "training_text": exact[key]})
            continue
        g = _trigrams(key)
        n = len(g)
        lo, hi = int(math.floor(n * threshold)), int(math.ceil(n / threshold))
        best, best_text = 0.0, None
        for size in range(lo, hi + 1):
            for i in by_size.get(size, ()):
                other = grams[i][0]
                inter = len(g & other)
                if inter:
                    j = inter / (n + len(other) - inter)
                    if j > best:
                        best, best_text = j, grams[i][1]
        if best > threshold:
            out.append({"text": text, "kind": "near", "similarity": round(best, 4), "training_text": best_text})
    return out


def decontaminate(records: list[dict], test_texts: list[str], threshold: float = 0.9) -> tuple[list[dict], list[dict]]:
    """(kept, removed): drop generated messages that are exact (after normalization) or near
    (trigram Jaccard > ``threshold``) copies of a test text. Short canonical phrasings ("quiero
    hablar con un humano") can be written independently by the generators and by the test
    writer; the test set stays untouched and the training data loses those few messages."""
    from models.m2.features import dedup_key

    tests = [(dedup_key(t), _trigrams(dedup_key(t))) for t in test_texts]
    keys = {k for k, _ in tests}
    kept, removed = [], []
    for r in records:
        key = dedup_key(r["text"])
        g = _trigrams(key)
        hit = key in keys
        if not hit:
            n = len(g)
            for _, tg_ in tests:
                m = len(tg_)
                if threshold * m <= n <= m / threshold:
                    inter = len(g & tg_)
                    if inter and inter / (n + m - inter) > threshold:
                        hit = True
                        break
        (removed if hit else kept).append(r)
    return kept, removed


def training_data(config: dict | None = None, test_path: Path = TEST_SET) -> tuple[list[dict], list[dict], list[dict]]:
    """(records of A, records of B, removed): the generated training data exactly as
    ``models.m2.train`` uses it, decontaminated against the team-written test set."""
    from models.m2 import textgen as tg

    a, b = tg.build_dataset(config)
    test_texts = [it["text"] for it in load_test_set(test_path)] if Path(test_path).exists() else []
    a, removed_a = decontaminate(a, test_texts)
    b, removed_b = decontaminate(b, test_texts)
    return a, b, removed_a + removed_b


# ---------------------------------------------------------------- metrics

def classification_metrics(y_true: list[str], y_pred: list[str], labels: Iterable[str] = CLASSES) -> dict:
    """Accuracy, macro-F1 (over the classes present in ``y_true``), per-class precision /
    recall / F1 / support and the confusion matrix (rows = true class, columns = predicted,
    in ``labels`` order plus "invalid" when some prediction is outside ``labels``)."""
    labels = list(labels)
    cols = labels + ([INVALID] if any(p not in labels for p in y_pred) else [])
    idx = {c: i for i, c in enumerate(cols)}
    cm = [[0] * len(cols) for _ in labels]
    for t, p in zip(y_true, y_pred):
        cm[labels.index(t)][idx.get(p, idx.get(INVALID, 0))] += 1
    per_class = {}
    f1s = []
    for i, c in enumerate(labels):
        tp = cm[i][i]
        support = sum(cm[i])
        predicted = sum(cm[r][i] for r in range(len(labels)))
        prec = tp / predicted if predicted else 0.0
        rec = tp / support if support else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[c] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4), "support": support}
        if support:
            f1s.append(f1)
    n = len(y_true)
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    return {
        "n": n,
        "accuracy": round(correct / n, 4) if n else None,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "per_class": per_class,
        "confusion": {"labels": labels, "columns": cols, "matrix": cm},
    }


def subset_metrics(items: list[dict], preds: list[str], key: str, values: Iterable[str]) -> dict:
    out = {}
    for v in values:
        sel = [i for i, it in enumerate(items) if it[key] == v]
        if not sel:
            continue
        m = classification_metrics([items[i]["label"] for i in sel], [preds[i] for i in sel])
        out[v] = {"n": m["n"], "accuracy": m["accuracy"], "macro_f1": m["macro_f1"]}
    return out


def ece(confidences: list[float], correct: list[bool], n_bins: int = 10) -> float:
    """Expected calibration error of the top-1 probability, ``n_bins`` equal-width bins."""
    n = len(confidences)
    if not n:
        return float("nan")
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for c, ok in zip(confidences, correct):
        b = min(n_bins - 1, max(0, int(math.ceil(c * n_bins)) - 1))
        bins[b].append((c, ok))
    total = 0.0
    for members in bins:
        if members:
            conf = sum(c for c, _ in members) / len(members)
            acc = sum(1 for _, ok in members if ok) / len(members)
            total += len(members) / n * abs(acc - conf)
    return total


def reliability_table(confidences: list[float], correct: list[bool], n_bins: int = 10) -> list[dict]:
    rows = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        members = [(c, ok) for c, ok in zip(confidences, correct)
                   if (lo < c <= hi) or (b == 0 and c <= hi)]
        if members:
            rows.append({"bin": f"({lo:.1f}, {hi:.1f}]", "n": len(members),
                         "confidence": round(sum(c for c, _ in members) / len(members), 4),
                         "accuracy": round(sum(1 for _, ok in members if ok) / len(members), 4)})
    return rows


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson interval of a proportion k/n."""
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def gate_metrics(correct: list[bool], gates: list[str], top2: list[bool] | None = None) -> dict:
    """Routing outcome of a gate: rates of accept / ask / human, wrong routing among accepted
    (top-1 wrong but accepted), and how often the true class is in the top 2 when asking."""
    n = len(correct)
    acc = [i for i, g in enumerate(gates) if g == "accept"]
    ask = [i for i, g in enumerate(gates) if g == "ask"]
    hum = [i for i, g in enumerate(gates) if g == "human"]
    wrong = sum(1 for i in acc if not correct[i])
    out = {
        "n": n,
        "accept_rate": round(len(acc) / n, 4) if n else None,
        "ask_rate": round(len(ask) / n, 4) if n else None,
        "human_rate": round(len(hum) / n, 4) if n else None,
        "n_accepted": len(acc),
        "wrong_accepted": wrong,
        "wrong_routing_accepted": round(wrong / len(acc), 4) if acc else None,
        "wrong_routing_accepted_ci95": wilson(wrong, len(acc)),
        "accuracy_ask_band": round(sum(1 for i in ask if correct[i]) / len(ask), 4) if ask else None,
        "accuracy_human_band": round(sum(1 for i in hum if correct[i]) / len(hum), 4) if hum else None,
    }
    if top2 is not None:
        out["top2_coverage_ask"] = round(sum(1 for i in ask if top2[i]) / len(ask), 4) if ask else None
    return out


def bootstrap(y_true: list[str], systems: dict[str, list[str]], n_boot: int = 2000, seed: int = 0) -> dict:
    """95% percentile intervals of accuracy and macro-F1 per system, and of the paired
    difference between the first two systems (same resampled items for every system)."""
    import numpy as np

    labels = list(CLASSES)
    k = len(labels) + 1  # + invalid
    code = {c: i for i, c in enumerate(labels)}
    yt = np.array([code[t] for t in y_true])
    n = len(yt)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    out: dict[str, Any] = {"n_boot": n_boot, "seed": seed}
    draws = {}
    for name, preds in systems.items():
        yp = np.array([code.get(p, k - 1) for p in preds])
        t, p = yt[idx], yp[idx]
        flat = (np.arange(n_boot)[:, None] * k * k + t * k + p).ravel()
        cm = np.bincount(flat, minlength=n_boot * k * k).reshape(n_boot, k, k)[:, :k - 1, :]
        tp = cm[:, np.arange(k - 1), np.arange(k - 1)]
        support = cm.sum(axis=2)
        predicted = cm.sum(axis=1)[:, :k - 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            prec = np.where(predicted > 0, tp / np.maximum(predicted, 1), 0.0)
            rec = np.where(support > 0, tp / np.maximum(support, 1), 0.0)
            f1 = np.where(prec + rec > 0, 2 * prec * rec / np.maximum(prec + rec, 1e-12), 0.0)
        present = support > 0
        macro = (f1 * present).sum(axis=1) / present.sum(axis=1)
        acc = (t == p).mean(axis=1)
        draws[name] = (acc, macro)
        out[name] = {"accuracy_ci95": [round(float(np.percentile(acc, 2.5)), 4), round(float(np.percentile(acc, 97.5)), 4)],
                     "macro_f1_ci95": [round(float(np.percentile(macro, 2.5)), 4),
                                       round(float(np.percentile(macro, 97.5)), 4)]}
    names = list(systems)
    if len(names) >= 2:
        a, b = draws[names[0]], draws[names[1]]
        d_acc, d_f1 = a[0] - b[0], a[1] - b[1]
        out["difference"] = {
            "systems": [names[0], names[1]],
            "accuracy_ci95": [round(float(np.percentile(d_acc, 2.5)), 4), round(float(np.percentile(d_acc, 97.5)), 4)],
            "macro_f1_ci95": [round(float(np.percentile(d_f1, 2.5)), 4), round(float(np.percentile(d_f1, 97.5)), 4)],
            "p_macro_f1_not_better": round(float((d_f1 <= 0).mean()), 4),
        }
    return out


def safety_metrics(items: list[dict], preds: list[str], gates: list[str] | None = None) -> dict:
    """Manipulation recall, benign messages flagged as manipulation, and manipulation that
    would be accepted as a benign intent (the dangerous error)."""
    manip = [i for i, it in enumerate(items) if it["label"] == "manipulation"]
    benign = [i for i, it in enumerate(items) if it["label"] != "manipulation"]
    caught = sum(1 for i in manip if preds[i] == "manipulation")
    flagged = sum(1 for i in benign if preds[i] == "manipulation")
    out = {
        "n_manipulation": len(manip),
        "manipulation_recall": round(caught / len(manip), 4) if manip else None,
        "missed_manipulation_ids": [items[i]["id"] for i in manip if preds[i] != "manipulation"],
        "n_benign": len(benign),
        "benign_flagged_as_manipulation": flagged,
        "benign_flagged_ids": [items[i]["id"] for i in benign if preds[i] == "manipulation"],
    }
    if gates is not None:
        out["missed_manipulation_accepted"] = sum(1 for i in manip if preds[i] != "manipulation" and gates[i] == "accept")
    return out


def evaluate_predictions(items: list[dict], preds: list[str]) -> dict:
    """Metrics of one system on the test set: overall, regular vs adversarial, per language
    and per country, per class, confusion matrix and safety."""
    y = [it["label"] for it in items]
    overall = classification_metrics(y, preds)
    out = {"overall": overall}
    for name, flag in (("regular", False), ("adversarial", True)):
        sel = [i for i, it in enumerate(items) if bool(it["adversarial"]) is flag]
        m = classification_metrics([y[i] for i in sel], [preds[i] for i in sel])
        out[name] = {"n": m["n"], "accuracy": m["accuracy"], "macro_f1": m["macro_f1"]}
    out["by_language"] = subset_metrics(items, preds, "language", LANGUAGES)
    out["by_country"] = subset_metrics(items, preds, "country", COUNTRIES)
    out["safety"] = safety_metrics(items, preds)
    return out


# ---------------------------------------------------------------- B2: zero-shot LLM

def render_zero_shot_prompt(text: str, language: str | None = None, version: str = "v1") -> str:
    """The zero-shot prompt with the variables filled (for inspection and for clients that
    don't render prompts themselves). The customer text goes inside <texto_cliente> as data;
    any copy of those tags inside the text is removed first."""
    import json

    raw = (PROMPTS_DIR / ZERO_SHOT_PROMPT_ID / f"{version}.md").read_text(encoding="utf-8")
    template = re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S)
    clean = re.sub(r"<\s*/?\s*texto_cliente\s*>", " ", text or "", flags=re.I).strip()
    values = {"text": clean, "language": language or "desconocido",
              "output_schema": json.dumps(ZERO_SHOT_SCHEMA, ensure_ascii=False, sort_keys=True)}
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: values.get(m.group(1), ""), template)


def evaluate_zero_shot(client: Any, items: list[dict] | None = None, model_role: str = "chat",
                       prompt_id: str = ZERO_SHOT_PROMPT_ID) -> dict:
    """Baseline B2: an LLM classifying with no training, through the #8 client interface.

    ``client.complete(prompt_id, {"text", "language"}, output_schema=ZERO_SHOT_SCHEMA,
    model_role=model_role)`` must return ``{"output": {"intent_class": ...}, ...}``. An error
    or an answer outside the nine classes counts as a wrong prediction ("invalid"). Returns the
    same metrics as the other systems plus cost and latency when the client reports them.

    The prompt lives in ``src/prompts/m2_zero_shot/`` (the shared folder ``src/llm`` and
    ``conversation.llm_port`` search first), so a real #8 client renders it as is.
    """
    items = load_test_set() if items is None else items
    preds: list[str] = []
    latencies: list[float] = []
    cost = 0.0
    errors = 0
    versions = set()
    for it in items:
        t0 = time.perf_counter()
        try:
            res = client.complete(prompt_id, {"text": it["text"], "language": it.get("language") or "es"},
                                  output_schema=ZERO_SHOT_SCHEMA, model_role=model_role)
            label = ((res or {}).get("output") or {}).get("intent_class")
            cost += float(((res or {}).get("usage") or {}).get("cost_usd") or 0.0)
            if res and res.get("prompt_version"):
                versions.add(str(res["prompt_version"]))
        except Exception:  # noqa: BLE001 - a failed call is a wrong answer for the baseline
            label = None
            errors += 1
        latencies.append((time.perf_counter() - t0) * 1000.0)
        preds.append(label if label in CLASSES else INVALID)
    out = evaluate_predictions(items, preds)
    lat = sorted(latencies)
    out["calls"] = {"n": len(items), "errors": errors, "invalid": sum(p == INVALID for p in preds),
                    "cost_usd": round(cost, 6), "prompt_versions": sorted(versions),
                    "latency_ms_p50": round(lat[len(lat) // 2], 1) if lat else None,
                    "latency_ms_p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None}
    out["predictions"] = preds
    return out


def time_calls(fn: Callable[[str], Any], texts: list[str], repeat: int = 3) -> dict:
    """p50 / p95 / mean latency in ms of ``fn(text)`` over ``texts`` (after one warm-up pass)."""
    for t in texts[:20]:
        fn(t)
    samples = []
    for _ in range(repeat):
        for t in texts:
            t0 = time.perf_counter()
            fn(t)
            samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    return {"n_calls": len(samples), "p50_ms": round(samples[len(samples) // 2], 3),
            "p95_ms": round(samples[int(0.95 * (len(samples) - 1))], 3),
            "mean_ms": round(sum(samples) / len(samples), 3), "max_ms": round(samples[-1], 3)}
