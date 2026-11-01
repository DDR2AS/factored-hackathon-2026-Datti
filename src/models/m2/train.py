"""Train, calibrate, choose thresholds and evaluate M2. Training data is SYNTHETIC.

Run from the repo root (Python 3.12, numpy, scipy, scikit-learn, PyYAML)::

    PYTHONPATH=src python -m models.m2.train          # Git Bash / Linux / macOS
    $env:PYTHONPATH="src"; python -m models.m2.train  # PowerShell

Steps (seeded and deterministic; under a minute on a laptop):

1. Generate the training messages with the two independent generators (``textgen``), drop the
   few that are exact or near copies of a team-written test message (``evaluate.training_data``;
   the run stops if there are more than ``MAX_DECONTAMINATED``, which would mean the generators
   leak the test set), and split each generator by template: validation messages use phrasings
   never seen in training.
2. Features with the same pure-Python code the Lambda runs (``features.embed``).
3. Regularization chosen by cross-generator macro-F1 on the train splits (train on A and score
   B, train on B and score A), with the same per-message strength for every model
   (``C = 1 / (lambda * n)``) so their logits have comparable scale.
4. Calibration and thresholds on CROSS-FITTED validation: the model trained on A's train split
   scores B's validation split and vice versa. Every validation message is new in phrasing and
   in style, the closest proxy we have to text from a new author (synthetic in-distribution
   validation would make the model look surer than it is on human text). Temperature scaling
   by negative log-likelihood; thresholds: accept keeps wrong routing among accepted messages
   <= 2%; ask keeps the true intent among the top two for >= 90% of asked messages.
5. Final model on A train + B train. Artifact (``weights.bin`` + ``meta.yaml``) under
   ``src/models/artifacts/m2/<version>/``, thresholds in the ``m2`` key of
   ``src/models/thresholds.yaml``.
6. Evaluation on the team-written test set with the pure-Python runtime that ships (exactly the
   production behavior), against the keyword stand-in (B1). B2 (zero-shot LLM) needs a model and
   is reported as not run. Writes ``data/eval/m2_report.yaml`` and ``docs/modelos/m2.md``.

``--dev`` stops before the artifact and the evaluation (it reads the test set only to drop
generated copies of its messages): used while designing, so that design choices are made on
validation data only.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import random
import sys
import time
from pathlib import Path

import numpy as np
import scipy
import scipy.sparse as sp
import sklearn
import yaml
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression

from models.m2 import evaluate as ev
from models.m2 import features as F
from models.m2 import textgen as tg
from models.m2.model import (
    ARTIFACTS_ROOT, CLASSES, META_FILE, MODEL_VERSION, THRESHOLDS_FILE, WEIGHTS_FILE, gate_for, load,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_FILE = REPO_ROOT / "data" / "eval" / "m2_report.yaml"
DOC_FILE = REPO_ROOT / "docs" / "modelos" / "m2.md"

LAMBDA_GRID = (3e-6, 1e-5, 3e-5)
WORD_DROPOUT = 0.15           # augmentation: one copy of each training message without ~15% of its words
AUGMENT_SEED = 11
N_JOBS = 6                    # parallel fits (threads: scikit-learn/scipy release the GIL in the heavy parts)
MAX_DECONTAMINATED = 10       # more generated copies of test messages than this means the generators leak it
TARGET_WRONG_ROUTING = 0.02   # accept: wrong routing among accepted messages
TARGET_TOP2_ASK = 0.80        # ask: true intent among the two options offered, 4 times out of 5
MIN_ACCEPTED = 30             # thresholds with fewer accepted messages can't be judged
MIN_ASK_BAND = 20             # messages per confidence window when choosing the ask threshold
ASK_WINDOW = 0.05
GRID_ACCEPT = [round(float(x), 2) for x in np.arange(0.30, 0.995, 0.01)]
CIDX = {c: i for i, c in enumerate(CLASSES)}


# ---------------------------------------------------------------- features and model

def featurize(texts: list[str]) -> sp.csr_matrix:
    indptr, indices, data = [0], [], []
    for t in texts:
        vec = F.embed(t)
        for b in sorted(vec):
            indices.append(b)
            data.append(vec[b])
        indptr.append(len(indices))
    return sp.csr_matrix((np.asarray(data, dtype=np.float64), np.asarray(indices, dtype=np.int32),
                          np.asarray(indptr, dtype=np.int64)), shape=(len(texts), F.N_BUCKETS))


def word_dropout(text: str, rng: random.Random, p: float = WORD_DROPOUT) -> str:
    """The message without each whitespace-separated word with probability ``p`` (never empty)."""
    words = text.split()
    keep = [w for w in words if rng.random() >= p]
    return " ".join(keep) if keep else text


def fit(X: sp.csr_matrix, y: np.ndarray, lam: float) -> LogisticRegression:
    """Multinomial logistic regression, L2 with per-message strength ``lam``."""
    clf = LogisticRegression(C=1.0 / (lam * X.shape[0]), max_iter=5000, tol=1e-4)
    clf.fit(X, y)
    if list(clf.classes_) != list(range(len(CLASSES))):
        raise RuntimeError(f"training data lacks some class: {clf.classes_}")
    return clf


def fit_many(jobs: list[tuple[sp.csr_matrix, np.ndarray, float]]) -> list[LogisticRegression]:
    """Several independent fits in parallel threads; results in the order of ``jobs``."""
    from joblib import Parallel, delayed

    return Parallel(n_jobs=min(N_JOBS, len(jobs)), prefer="threads")(delayed(fit)(X, y, lam) for X, y, lam in jobs)


def softmax(z: np.ndarray, t: float = 1.0) -> np.ndarray:
    zz = z / t
    zz = zz - zz.max(axis=1, keepdims=True)
    e = np.exp(zz)
    return e / e.sum(axis=1, keepdims=True)


def nll(z: np.ndarray, y: np.ndarray, t: float) -> float:
    p = softmax(z, t)
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-15, None))))


def fit_temperature(z: np.ndarray, y: np.ndarray) -> float:
    res = minimize_scalar(lambda lt: nll(z, y, float(np.exp(lt))), bounds=(-4.0, 4.0), method="bounded",
                          options={"xatol": 1e-6})
    return float(np.exp(res.x))


def top_stats(p: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(top-1 probability rounded like the runtime, top-1 index, correct, true class in top 2)."""
    order = np.argsort(-p, axis=1, kind="stable")
    top = order[:, 0]
    conf = np.round(p[np.arange(len(y)), top], 3)
    correct = top == y
    top2 = correct | (order[:, 1] == y)
    return conf, top, correct, top2


def labels_of(idx: np.ndarray) -> list[str]:
    return [CLASSES[i] for i in idx]


def plain(obj):
    """The same structure with numpy scalars and tuples turned into plain Python (for YAML)."""
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


# ---------------------------------------------------------------- thresholds

def choose_accept(conf: np.ndarray, correct: np.ndarray) -> tuple[float, list[dict]]:
    """Smallest threshold t such that, for t and every higher threshold that accepts at least
    MIN_ACCEPTED messages, wrong routing among accepted messages is <= TARGET_WRONG_ROUTING."""
    rows, ok = [], []
    for t in GRID_ACCEPT:
        acc = conf >= t
        n = int(acc.sum())
        wrong = int((~correct[acc]).sum())
        rate = wrong / n if n else 0.0
        rows.append({"threshold": t, "accepted": n, "wrong": wrong, "wrong_rate": round(rate, 4)})
        ok.append(n < MIN_ACCEPTED or rate <= TARGET_WRONG_ROUTING)
    chosen = GRID_ACCEPT[-1]
    for i in range(len(GRID_ACCEPT) - 1, -1, -1):
        if not ok[i]:
            break
        chosen = GRID_ACCEPT[i]
    return chosen, rows


def choose_ask(conf: np.ndarray, top2: np.ndarray, accept: float) -> tuple[float, list[dict]]:
    """Walk down from ``accept`` in windows 0.05 wide (widened until they hold MIN_ASK_BAND
    messages) and keep asking while a two-option clarifying question would contain the true
    intent for >= TARGET_TOP2_ASK of the window's messages. The first window below that level
    starts the human band. Returns (ask threshold, the windows examined)."""
    upper, t, rows = accept, round(accept - ASK_WINDOW, 2), []
    while t >= 0.10 - 1e-9:
        window = (conf >= t) & (conf < upper)
        n = int(window.sum())
        if n >= MIN_ASK_BAND:
            cov = float(top2[window].mean())
            rows.append({"from": t, "to": upper, "n": n, "top2_coverage": round(cov, 4)})
            if cov < TARGET_TOP2_ASK:
                return upper, rows
            upper = t
        t = round(t - ASK_WINDOW, 2)
    return 0.10, rows  # every window passed, or too few low-confidence messages to judge


def gates_for(conf: np.ndarray, accept: float, ask: float) -> list[str]:
    return [gate_for(float(c), accept, ask) for c in conf]


def eval_block(z: np.ndarray, y: np.ndarray, t: float, accept: float | None = None, ask: float | None = None) -> dict:
    """Accuracy, macro-F1, NLL and ECE (raw and calibrated) of logits ``z``; gate metrics
    when thresholds are given."""
    p_raw, p_cal = softmax(z), softmax(z, t)
    conf_raw, top, correct, top2 = top_stats(p_raw, y)
    conf_cal = top_stats(p_cal, y)[0]
    m = ev.classification_metrics(labels_of(y), labels_of(top))
    out = {"n": int(len(y)), "accuracy": m["accuracy"], "macro_f1": m["macro_f1"],
           "nll_raw": round(nll(z, y, 1.0), 4), "nll_calibrated": round(nll(z, y, t), 4),
           "ece_raw": round(ev.ece(conf_raw.tolist(), correct.tolist()), 4),
           "ece_calibrated": round(ev.ece(conf_cal.tolist(), correct.tolist()), 4),
           "top2_accuracy": round(float(top2.mean()), 4)}
    if accept is not None:
        out["gate"] = ev.gate_metrics(correct.tolist(), gates_for(conf_cal, accept, ask), top2.tolist())
    return out


# ---------------------------------------------------------------- artifact

def write_artifact(clf: LogisticRegression, temperature: float, meta: dict) -> dict:
    folder = ARTIFACTS_ROOT / MODEL_VERSION
    folder.mkdir(parents=True, exist_ok=True)
    w = np.ascontiguousarray(clf.coef_.T, dtype="<f4")        # [n_buckets][n_classes]
    b = np.ascontiguousarray(clf.intercept_, dtype="<f4")
    raw = w.tobytes() + b.tobytes()
    (folder / WEIGHTS_FILE).write_bytes(raw)
    meta["calibration"]["temperature"] = round(float(temperature), 6)
    meta["weights"] = {"file": WEIGHTS_FILE, "dtype": "float32", "byte_order": "little",
                       "layout": "[n_buckets][n_classes] bucket-major, then bias[n_classes]",
                       "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    text = ("# M2 artifact metadata, written by `python -m models.m2.train`; do not edit by hand.\n"
            "# Trained ONLY on synthetic messages (models/m2/textgen.py). Report: docs/modelos/m2.md\n")
    text += yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=110)
    (folder / META_FILE).write_text(text, encoding="utf-8")
    files = []
    for f in sorted(folder.iterdir()):
        data = f.read_bytes()
        files.append({"name": f.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return {"path": str(folder.relative_to(REPO_ROOT)).replace("\\", "/"), "files": files,
            "total_bytes": sum(f["bytes"] for f in files)}


def write_thresholds(section: dict) -> None:
    """Replace only the ``m2`` key of src/models/thresholds.yaml (M1 and M3 share the file)."""
    try:
        from models.common import write_threshold_section  # shared helper (models/common.py)
    except ImportError:  # pragma: no cover - fallback if the shared helper is not there
        write_threshold_section = None
    if write_threshold_section is not None:
        write_threshold_section("m2", section, THRESHOLDS_FILE)
        return
    data = yaml.safe_load(THRESHOLDS_FILE.read_text(encoding="utf-8")) if THRESHOLDS_FILE.exists() else {}
    data = data or {}
    data["m2"] = section
    THRESHOLDS_FILE.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ---------------------------------------------------------------- main

def _counts(records: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in records:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dev", action="store_true", help="stop before the test set (design on validation only)")
    args = ap.parse_args(argv)
    t_start = time.perf_counter()
    log = lambda msg: print(f"[m2 {time.perf_counter() - t_start:6.1f}s] {msg}", flush=True)  # noqa: E731

    cfg = dict(tg.DEFAULT_CONFIG)
    a, b, removed = ev.training_data(cfg)  # generated, minus exact/near copies of test messages
    a_tr, a_va = tg.split_by_template(a, cfg["val_fraction"], cfg["split_seed"])
    b_tr, b_va = tg.split_by_template(b, cfg["val_fraction"], cfg["split_seed"] + 1)
    log(f"data: A {len(a)} ({len(a_tr)} train / {len(a_va)} val), B {len(b)} ({len(b_tr)} / {len(b_va)}); "
        f"{len(removed)} generated messages dropped as copies of test messages")
    if len(removed) > MAX_DECONTAMINATED:
        raise RuntimeError(f"{len(removed)} generated messages copy the test set: the generators leak it")

    sets = {"a_tr": a_tr, "a_va": a_va, "b_tr": b_tr, "b_va": b_va}
    X = {k: featurize([r["text"] for r in v]) for k, v in sets.items()}
    Y = {k: np.array([CIDX[r["label"]] for r in v]) for k, v in sets.items()}
    # Training sets get one word-dropout copy of every message (seeded): the model can't lean
    # on a single phrase of a template, which helps on phrasings it never saw.
    XT, YT = {}, {}
    for i, k in enumerate(("a_tr", "a_va", "b_tr", "b_va")):
        rng = random.Random(AUGMENT_SEED + i)
        aug = featurize([word_dropout(r["text"], rng) for r in sets[k]])
        XT[k] = sp.vstack([X[k], aug]).tocsr()
        YT[k] = np.concatenate([Y[k], Y[k]])
    log("features done")

    # 3. regularization by cross-generator macro-F1 (train splits only)
    jobs = [(XT[g], YT[g], lam) for lam in LAMBDA_GRID for g in ("a_tr", "b_tr")]
    fitted = fit_many(jobs)
    selection, models = [], {}
    for j, lam in enumerate(LAMBDA_GRID):
        m_a, m_b = fitted[2 * j], fitted[2 * j + 1]
        f_ab = ev.classification_metrics(labels_of(Y["b_tr"]), labels_of(m_a.predict(X["b_tr"])))["macro_f1"]
        f_ba = ev.classification_metrics(labels_of(Y["a_tr"]), labels_of(m_b.predict(X["a_tr"])))["macro_f1"]
        selection.append({"lambda": lam, "macro_f1_A_to_B": f_ab, "macro_f1_B_to_A": f_ba,
                          "mean": round((f_ab + f_ba) / 2, 4)})
        models[lam] = (m_a, m_b)
        log(f"lambda {lam:g}: A->B {f_ab:.4f}  B->A {f_ba:.4f}")
    best = max(selection, key=lambda r: (r["mean"], r["lambda"]))
    lam = best["lambda"]
    m_a, m_b = models[lam]

    # 4. cross-fitted validation: calibration and thresholds
    z_val = np.vstack([m_b.decision_function(X["a_va"]), m_a.decision_function(X["b_va"])])
    y_val = np.concatenate([Y["a_va"], Y["b_va"]])
    temperature = fit_temperature(z_val, y_val)
    conf_val, _, correct_val, top2_val = top_stats(softmax(z_val, temperature), y_val)
    accept_t, accept_curve = choose_accept(conf_val, correct_val)
    ask_t, ask_windows = choose_ask(conf_val, top2_val, accept_t)
    val_cross = eval_block(z_val, y_val, temperature, accept_t, ask_t)
    log(f"cross-fitted validation n={len(y_val)}: acc {val_cross['accuracy']} macroF1 {val_cross['macro_f1']} "
        f"T={temperature:.3f} ECE {val_cross['ece_raw']} -> {val_cross['ece_calibrated']}; "
        f"accept>={accept_t} ask>={ask_t}: {val_cross['gate']}")

    # 5. final model (and, in parallel, the cross-generator honesty check: all of A -> all of B)
    X_tr = sp.vstack([XT["a_tr"], XT["b_tr"]]).tocsr()
    y_tr = np.concatenate([YT["a_tr"], YT["b_tr"]])
    XA, XB = sp.vstack([X["a_tr"], X["a_va"]]).tocsr(), sp.vstack([X["b_tr"], X["b_va"]]).tocsr()
    YA, YB = np.concatenate([Y["a_tr"], Y["a_va"]]), np.concatenate([Y["b_tr"], Y["b_va"]])
    final, full_a, full_b = fit_many([
        (X_tr, y_tr, lam),
        (sp.vstack([XT["a_tr"], XT["a_va"]]).tocsr(), np.concatenate([YT["a_tr"], YT["a_va"]]), lam),
        (sp.vstack([XT["b_tr"], XT["b_va"]]).tocsr(), np.concatenate([YT["b_tr"], YT["b_va"]]), lam),
    ])
    X_va = sp.vstack([X["a_va"], X["b_va"]]).tocsr()
    z_in = final.decision_function(X_va)
    t_in = fit_temperature(z_in, y_val)
    conf_in, _, correct_in, top2_in = top_stats(softmax(z_in, t_in), y_val)
    acc_in, _ = choose_accept(conf_in, correct_in)
    val_in = eval_block(z_in, y_val, temperature, accept_t, ask_t)
    val_in["alternative_if_calibrated_here"] = {
        "temperature": round(t_in, 4), "accept_threshold": acc_in,
        "ask_threshold": choose_ask(conf_in, top2_in, acc_in)[0], "used": False}
    log(f"final model on {len(a_tr) + len(b_tr)} messages (+ word-dropout copies); in-distribution validation acc {val_in['accuracy']} "
        f"macroF1 {val_in['macro_f1']} (its own T would be {t_in:.3f}, accept {acc_in})")

    cross_gen = {"A_to_B": eval_block(full_a.decision_function(XB), YB, 1.0),
                 "B_to_A": eval_block(full_b.decision_function(XA), YA, 1.0)}
    for k in cross_gen:
        cross_gen[k].pop("nll_calibrated")
        cross_gen[k].pop("ece_calibrated")
    log(f"cross-generator: A->B acc {cross_gen['A_to_B']['accuracy']} macroF1 {cross_gen['A_to_B']['macro_f1']}; "
        f"B->A acc {cross_gen['B_to_A']['accuracy']} macroF1 {cross_gen['B_to_A']['macro_f1']}")

    data_info = {
        "synthetic": True,
        "generator_config": cfg,
        "fingerprint_a": tg.fingerprint(a),
        "fingerprint_b": tg.fingerprint(b),
        "decontaminated": {"rule": "generated messages equal (after normalization) or near (trigram Jaccard > 0.9) "
                                   "to a test message are dropped before training",
                           "removed": len(removed), "texts": [r["text"] for r in removed]},
        "n_a": len(a), "n_b": len(b),
        "n_train": len(a_tr) + len(b_tr), "n_train_rows_with_augmentation": int(X_tr.shape[0]),
        "n_validation": int(len(y_val)),
        "train_by_class": _counts(a_tr + b_tr, "label"),
        "train_by_language": _counts(a_tr + b_tr, "language"),
        "train_by_country": _counts(a_tr + b_tr, "country"),
        "validation_by_class": _counts(a_va + b_va, "label"),
        "templates_train": len({r["template"] for r in a_tr + b_tr}),
        "templates_validation": len({r["template"] for r in a_va + b_va}),
    }
    if args.dev:
        log("dev run: stopping before the artifact and the test set")
        return {"lambda": lam, "temperature": temperature, "val_cross": val_cross, "val_in": val_in,
                "cross_gen": cross_gen, "final": final, "data": data_info}

    # 6. artifact and thresholds
    meta = {
        "model_version": MODEL_VERSION,
        "created_by": "python -m models.m2.train",
        "synthetic_training_data": True,
        "classes": list(CLASSES),
        "features": {"version": F.FEATURES_VERSION, "n_buckets": F.N_BUCKETS,
                     "char_ngrams": [F.CHAR_NGRAM_MIN, F.CHAR_NGRAM_MAX], "word_ngrams": [1, 2],
                     "hash": "zlib.crc32 of the UTF-8 feature string; low 15 bits = bucket, top bit = sign",
                     "concepts": [name for name, _, _ in F.CONCEPTS],
                     "weighting": "sublinear tf (1 + ln count), L2 norm per block (characters, words, concepts)",
                     "max_chars": F.MAX_CHARS,
                     "embedding_seam": "features.embed(text); hashed n-grams + a concept lexicon stand in for a "
                                       "multilingual embedding model (no Bedrock embeddings available offline)"},
        "model": {"type": "multinomial logistic regression (scikit-learn lbfgs, L2)", "lambda": lam,
                  "C": round(1.0 / (lam * X_tr.shape[0]), 6), "n_train": len(a_tr) + len(b_tr),
                  "rows_with_augmentation": int(X_tr.shape[0]),
                  "augmentation": f"one word-dropout copy per message (p={WORD_DROPOUT}, seed {AUGMENT_SEED})",
                  "lambda_selected_by": "mean cross-generator macro-F1 on the train splits"},
        "calibration": {"method": "temperature scaling (NLL)", "temperature": None,
                        "fitted_on": f"cross-fitted validation, n={len(y_val)}: model trained on A-train "
                                     "scores B-validation and vice versa (held-out phrasings and style)",
                        "ece_10_bins_before": val_cross["ece_raw"], "ece_10_bins_after": val_cross["ece_calibrated"]},
        "training_data": {**{k: data_info[k] for k in ("synthetic", "generator_config", "fingerprint_a",
                                                       "fingerprint_b", "n_a", "n_b", "n_train", "n_validation")},
                          "decontaminated_removed": len(removed),
                          "fingerprints_are_after_decontamination": True},
        "libraries": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
                      "scikit_learn": sklearn.__version__},
        "metrics": {"validation_cross_fitted": {k: val_cross[k] for k in ("accuracy", "macro_f1")},
                    "cross_generator_macro_f1": {k: cross_gen[k]["macro_f1"] for k in cross_gen}},
    }
    artifact = write_artifact(final, temperature, plain(meta))
    thresholds = {
        "model_version": MODEL_VERSION,
        "accept_threshold": accept_t,
        "ask_threshold": ask_t,
        "rule": "p = calibrated top-1 probability rounded to 3 decimals; accept if p >= accept_threshold; "
                "ask if ask_threshold <= p < accept_threshold; human below ask_threshold",
        "chosen_on": f"cross-fitted validation (synthetic, n={len(y_val)}): model trained on generator A scores "
                     f"generator B's held-out phrasings and vice versa",
        "accept_criterion": f"smallest threshold with wrong routing among accepted <= {TARGET_WRONG_ROUTING:.0%} "
                            f"(also at every higher threshold accepting >= {MIN_ACCEPTED} messages)",
        "ask_criterion": f"walking down from accept_threshold in windows {ASK_WINDOW} wide (>= {MIN_ASK_BAND} "
                         f"messages), keep asking while the true intent is among the top two options for >= "
                         f"{TARGET_TOP2_ASK:.0%} of the window; the first window below that starts the human band",
        "temperature": round(temperature, 6),
        "validation": {k: val_cross["gate"][k] for k in ("n", "accept_rate", "ask_rate", "human_rate",
                                                          "wrong_routing_accepted", "wrong_routing_accepted_ci95",
                                                          "top2_coverage_ask")},
    }
    thresholds = plain(thresholds)
    write_thresholds(thresholds)
    log(f"artifact {artifact['path']} ({artifact['total_bytes']} bytes); thresholds written")

    # 7. evaluation with the shipped runtime
    model = load()
    sample = [r["text"] for r in (a_va + b_va)[:300]]
    z_rt = np.array([model.logits(t) for t in sample])
    parity = float(np.max(np.abs(z_rt - final.decision_function(featurize(sample)))))

    from models.m2 import report as rp  # local import: only for the full run

    rep = rp.evaluate_and_report(model=model, data_info=data_info, selection=selection, lam=lam,
                                 temperature=temperature, thresholds=thresholds, val_cross=val_cross,
                                 val_in=val_in, cross_gen=cross_gen, artifact=artifact, parity=parity,
                                 accept_curve=accept_curve, ask_windows=ask_windows,
                                 train_texts=[r["text"] for r in a + b],
                                 seconds=time.perf_counter() - t_start)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("# M2 evaluation report (SYNTHETIC data), written by `python -m models.m2.train`.\n"
                           + yaml.safe_dump(plain(rep), sort_keys=False, allow_unicode=True, width=110),
                           encoding="utf-8")
    DOC_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOC_FILE.write_text(rp.render_markdown(rep), encoding="utf-8")
    print(rp.summary(rep), flush=True)
    log(f"report {REPORT_FILE.relative_to(REPO_ROOT)} and {DOC_FILE.relative_to(REPO_ROOT)} written")
    return rep


if __name__ == "__main__":
    main(sys.argv[1:])
