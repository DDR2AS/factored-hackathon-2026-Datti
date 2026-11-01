"""M1 · transaction ranker (INTERFACES.md #4): LightGBM LambdaRank + a calibrated confidence model.

``rank_candidates(slots, candidates) -> {ranked: [{txn_id, p}], band, model_version}`` with the same
contract as the v1.4 rule stand-in (``conversation/ranker.py``, ``m1-rule-0``): every candidate,
highest p first (ties by txn_id), p rounded to 4 decimals, p values sum to at most 1 (there is a
"none of these" option), no candidates -> ``ranked: []`` and band ``ask``.

How p is computed: LambdaRank scores order the candidates; a confidence model (LightGBM binary,
trained on a calibration split the ranker never saw) estimates from the ABSOLUTE fit of the best
candidate (amount gap, day gap, merchant similarity, score margin ...) the probability that it is
the charge, calibrated with isotonic regression on validation. The other candidates keep their
softmax share relative to the best one. Bands use ``src/models/thresholds.yaml`` (key ``m1``),
chosen on validation to keep wrong confirmations under 1%. If training decided the rule is better
(``primary: rule``) or the artifacts are missing, the rule answers and says so in ``model_version``.

Runtime is pure Python (LightGBM text models evaluated by ``models.common.LgbmModel``).
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Mapping

import yaml

from models.common import ARTIFACTS_DIR, Isotonic, LgbmModel, read_thresholds
from models.m1 import features
from models.m1.features import merchant_similarity  # noqa: F401 (part of the ranker API the orchestrator uses)

_lock = threading.Lock()
_state: dict[str, Any] = {}

CONFIRM_THRESHOLD = 0.85  # replaced by thresholds.yaml when present
CHOOSE_THRESHOLD = 0.40


def _load() -> dict[str, Any]:
    if _state:
        return _state
    with _lock:
        if _state:
            return _state
        thr = read_thresholds().get("m1") or {}
        version = thr.get("model_version")
        art = ARTIFACTS_DIR / "m1" / version if version else None
        loaded: dict[str, Any] = {"thr": thr, "model": None, "version": version}
        if art and all((art / f).exists() for f in ("model.txt", "confidence.txt", "calibration.yaml")):
            cal = yaml.safe_load((art / "calibration.yaml").read_text(encoding="utf-8"))
            if list(cal["features"]) != list(features.FEATURES):
                raise RuntimeError("M1 artifact was trained with other features; retrain (make train)")
            loaded["model"] = LgbmModel.load(art / "model.txt")
            loaded["confidence"] = LgbmModel.load(art / "confidence.txt")
            loaded["none_logit"] = float(cal["none_logit"])
            loaded["iso"] = Isotonic.from_dict(cal["isotonic"])
        _state.update(loaded)
        global CONFIRM_THRESHOLD, CHOOSE_THRESHOLD
        CONFIRM_THRESHOLD = float(thr.get("confirm_threshold", CONFIRM_THRESHOLD))
        CHOOSE_THRESHOLD = float(thr.get("choose_threshold", CHOOSE_THRESHOLD))
        return _state


def reload() -> None:
    """Forget the loaded artifacts (tests, after make train)."""
    with _lock:
        _state.clear()


def available() -> bool:
    return _load().get("model") is not None


def primary() -> str:
    """"model" or "rule" (training compares both on the frozen test set and writes the winner)."""
    st = _load()
    return "model" if st.get("model") is not None and st["thr"].get("primary") == "model" else "rule"


def band_for(p_top1: float | None) -> str:
    _load()
    if p_top1 is None:
        return "ask"
    if p_top1 >= CONFIRM_THRESHOLD:
        return "confirm"
    if p_top1 >= CHOOSE_THRESHOLD:
        return "choose"
    return "ask"


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def probabilities(slots: Mapping[str, Any] | None, candidates: list[Any]) -> list[float]:
    """Calibrated P(candidate i is the charge the customer means), one per candidate."""
    st = _load()
    if st.get("model") is None:
        raise RuntimeError("M1 model not trained (make train)")
    rows = features.build(slots, candidates)
    scores = [st["model"].predict(x) for x in rows]
    c = st["none_logit"]
    m = max(scores + [c])
    z = sum(math.exp(v - m) for v in scores) + math.exp(c - m)
    raw = [math.exp(v - m) / z for v in scores]
    top = max(range(len(scores)), key=lambda i: scores[i])
    srt = sorted(scores, reverse=True)
    margin = srt[0] - (srt[1] if len(srt) > 1 else c)
    conf_x = list(rows[top]) + [raw[top], margin, float(len(scores))]
    p_top = st["iso"](_sigmoid(st["confidence"].predict(conf_x)))
    out = [r * (p_top / max(raw[top], 1e-12)) for r in raw]
    total = sum(out)
    if total > 0.999:
        out = [p * 0.999 / total for p in out]
    return out


def rank_candidates(slots: Mapping[str, Any] | None, candidates: list[Any], force: str | None = None) -> dict:
    """``force``: "model" or "rule" (evaluation); default = the primary chosen by training."""
    which = force or primary()
    if which == "rule" or not available():
        from conversation import ranker as rule  # the v1.4 rule (fallback and baseline B1)

        out = rule.rank_candidates(slots, candidates)
        if which == "model":  # asked for the model but there is none
            out["model_version"] = f"{out['model_version']} (m1 unavailable)"
        return out
    if not candidates:
        return {"ranked": [], "band": "ask", "model_version": _load()["version"]}
    ps = probabilities(slots, candidates)
    ranked = sorted(({"txn_id": str(features._get(c, "txn_id")), "p": round(p, 4)} for c, p in zip(candidates, ps)),
                    key=lambda r: (-r["p"], r["txn_id"]))
    return {"ranked": ranked, "band": band_for(ranked[0]["p"]), "model_version": _load()["version"]}


def model_version() -> str:
    return _load()["version"] if primary() == "model" else "m1-rule-0"


def artifact_dir() -> Path | None:
    v = _load().get("version")
    return ARTIFACTS_DIR / "m1" / v if v else None
