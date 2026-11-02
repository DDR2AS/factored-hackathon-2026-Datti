"""Which ranker (M1) and intent classifier (M2) the orchestrator uses (INTERFACES.md #4 and #9).

- ``M1_BACKEND``: ``rule`` (the v1.4 rule ``m1-rule-0``, default when unset) or ``model``
  (``models.m1``: LightGBM LambdaRank, calibrated; it still answers with the rule when training
  decided the rule is better or the artifact is missing, and says so in ``model_version``).
- ``M2_BACKEND``: ``model`` (``models.m2``, default when unset; falls back to the keywords when the
  artifact is missing) or ``keywords`` (``m2-keywords-0``, the M1-build stand-in). The manipulation
  patterns run in both cases, but alone they catch only a minority of new manipulation attempts,
  so the model is the default everywhere, not only in ``local_api.py``, the evaluation and the CDK.

Two things stay rule-based whatever M2 says (docs/propuestas_interfaces.md, "Propuesta a
Cristhian"): the manipulation patterns are a guard OR-ed with the model, so a false negative of the
model never lets an injection through; and ``scores()`` (used to know that "nothing was understood")
keeps the keyword patterns. The models produce evidence only; the lane is decided by
``lane_rules.yaml``.
"""

from __future__ import annotations

from typing import Any, Mapping

from conversation import classifier as keywords
from conversation import ranker as rule_ranker

M1_BACKENDS = ("rule", "model")
M2_BACKENDS = ("keywords", "model")


class RuleRanker:
    """The v1.4 rule behind the ranker interface the orchestrator uses."""

    name = "rule"
    MODEL_VERSION = rule_ranker.MODEL_VERSION
    CONFIRM_THRESHOLD = rule_ranker.CONFIRM_THRESHOLD
    rank_candidates = staticmethod(rule_ranker.rank_candidates)
    band_for = staticmethod(rule_ranker.band_for)
    merchant_similarity = staticmethod(rule_ranker.merchant_similarity)


class ModelRanker:
    """M1 (models.m1) behind the same interface."""

    name = "model"
    merchant_similarity = staticmethod(rule_ranker.merchant_similarity)

    def __init__(self) -> None:
        from models import m1

        self._m1 = m1

    @property
    def MODEL_VERSION(self) -> str:  # noqa: N802 (same attribute name as the module stand-in)
        return self._m1.model_version()

    @property
    def CONFIRM_THRESHOLD(self) -> float:  # noqa: N802
        return self._m1.CONFIRM_THRESHOLD if self._m1.primary() == "model" else rule_ranker.CONFIRM_THRESHOLD

    def rank_candidates(self, slots: Mapping[str, Any] | None, candidates: list[Any]) -> dict:
        return self._m1.rank_candidates(slots, candidates)

    def band_for(self, p_top1: float | None) -> str:
        return self._m1.band_for(p_top1) if self._m1.primary() == "model" else rule_ranker.band_for(p_top1)


class KeywordClassifier:
    name = "keywords"
    MODEL_VERSION = keywords.MODEL_VERSION
    classify = staticmethod(keywords.classify)
    scores = staticmethod(keywords.scores)


class ModelClassifier:
    """M2 with the rule guard: a manipulation the keyword patterns catch always wins."""

    name = "model"
    scores = staticmethod(keywords.scores)

    def __init__(self) -> None:
        from models import m2

        self._m2 = m2

    @property
    def MODEL_VERSION(self) -> str:  # noqa: N802
        v = getattr(self._m2, "MODEL_VERSION", None)
        if callable(v):
            v = v()
        return v or "m2"

    def classify(self, text: str, language: str | None = None) -> dict:
        guard = keywords.classify(text, language)
        if guard["intent_class"] == "manipulation":
            guard = dict(guard)
            guard["model_version"] = f"{guard['model_version']}+guard"
            return guard
        out = dict(self._m2.classify(text, language))
        if out.get("intent_class") not in keywords.CLASSES:
            return guard
        return out


def make_ranker(env: Mapping[str, str]) -> Any:
    name = (env.get("M1_BACKEND") or "rule").strip().lower()
    if name not in M1_BACKENDS:
        raise ValueError(f"M1_BACKEND={name!r} is not valid; use {', '.join(M1_BACKENDS)}")
    if name == "rule":
        return RuleRanker()
    try:
        return ModelRanker()
    except Exception:  # models not installed in this package: the rule answers (and says so)
        return RuleRanker()


def make_classifier(env: Mapping[str, str]) -> Any:
    name = (env.get("M2_BACKEND") or "model").strip().lower()
    if name not in M2_BACKENDS:
        raise ValueError(f"M2_BACKEND={name!r} is not valid; use {', '.join(M2_BACKENDS)}")
    if name == "keywords":
        return KeywordClassifier()
    try:
        clf = ModelClassifier()
        clf.classify("hola", "es")  # loads the artifact now: a missing one fails at start, not mid-turn
        return clf
    except Exception:
        return KeywordClassifier()


def describe(env: Mapping[str, str]) -> str:
    r, c = make_ranker(env), make_classifier(env)
    return f"M1: {r.MODEL_VERSION} · M2: {c.MODEL_VERSION}"
