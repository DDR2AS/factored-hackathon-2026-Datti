"""G3 · offline judge (plan v2 section 6): scores reply CLARITY and TONE with a rubric.

- Runs through the #8 client with ``model_role="judge"`` (Opus 5 per D5; batch where possible).
  Correctness is never the judge's job: it is checked in code against the planted ground truth.
- Before its scores are used, the judge is validated against replies scored by hand by two people
  (``eval/judge/hand_scored.yaml``): agreement (exact and within one point, Spearman) is reported.
- Without a model (``LLM_PROVIDER`` mock or none) nothing is scored and the report says "not run":
  the judge never falls back to a heuristic pretending to be a judge.

    python scripts/judge.py --prepare      # write the hand-scoring sheet from the latest eval replies
    python scripts/judge.py --run          # needs LLM_PROVIDER=anthropic|bedrock and credentials
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "data" / "eval"
SHEET = REPO_ROOT / "eval" / "judge" / "hand_scored.yaml"
PROMPT_ID = "g3_judge"


class JudgeScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clarity: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    register_ok: bool
    promises: bool
    rationale: str = Field(max_length=400)


def load_replies() -> list[dict]:
    f = EVAL_DIR / "replies_sample.yaml"
    if not f.exists():
        return []
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("replies", [])


def prepare_sheet(n: int = 50, seed: int = 4) -> Path:
    """The hand-scoring sheet: 50 replies (synthetic) with empty scores for two raters."""
    replies = load_replies()
    if not replies:
        raise SystemExit("No hay respuestas de muestra: corre `make eval` primero.")
    rng = random.Random(seed)
    sample = rng.sample(replies, min(n, len(replies)))
    if SHEET.exists():
        old = yaml.safe_load(SHEET.read_text(encoding="utf-8")) or {}
        if any(i.get("rater_a") or i.get("rater_b") for i in old.get("items", [])):
            raise SystemExit(f"{SHEET} ya tiene puntajes: no se sobrescribe.")
    doc = {
        "synthetic": True,
        "instructions": ("Dos personas del equipo puntúan cada respuesta por separado, sin ver la del otro ni la del "
                         "juez: clarity y tone de 1 a 5 (rúbrica en src/prompts/g3_judge/v1.md). Dejar null lo que "
                         "no se puntuó. Estas respuestas son sintéticas (generadas por la app en la evaluación)."),
        "items": [{"id": r["id"], "language": r["language"], "country": r["country"], "situation": r["situation"],
                   "text": r["text"], "rater_a": {"clarity": None, "tone": None},
                   "rater_b": {"clarity": None, "tone": None}} for r in sample],
    }
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    SHEET.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return SHEET


def score(client: Any, item: dict) -> dict:
    out = client.complete(PROMPT_ID, {"language": item["language"], "country": item["country"],
                                      "situation": item["situation"], "text": item["text"]},
                          output_schema=JudgeScore.model_json_schema(), model_role="judge")
    return {**JudgeScore.model_validate(out["output"]).model_dump(), "model_id": out.get("model_id"),
            "cost_usd": (out.get("usage") or {}).get("cost_usd")}


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    from scipy.stats import spearmanr

    return float(spearmanr(a, b).correlation)


def validate(judge_scores: dict[str, dict]) -> dict:
    """Agreement of the judge with the human mean, per dimension."""
    if not SHEET.exists():
        return {"status": "no_sheet"}
    items = (yaml.safe_load(SHEET.read_text(encoding="utf-8")) or {}).get("items", [])
    out = {}
    for dim in ("clarity", "tone"):
        pairs = []
        for i in items:
            hs = [i.get(r, {}).get(dim) for r in ("rater_a", "rater_b")]
            hs = [h for h in hs if h is not None]
            if hs and i["id"] in judge_scores:
                pairs.append((sum(hs) / len(hs), judge_scores[i["id"]][dim]))
        if not pairs:
            out[dim] = {"n": 0, "status": "pending human scores"}
            continue
        exact = sum(1 for h, j in pairs if round(h) == j) / len(pairs)
        within1 = sum(1 for h, j in pairs if abs(h - j) <= 1) / len(pairs)
        out[dim] = {"n": len(pairs), "exact": round(exact, 3), "within_one": round(within1, 3),
                    "spearman": _spearman([p[0] for p in pairs], [p[1] for p in pairs])}
    return out


def run(provider: str | None = None, limit: int = 60) -> dict:
    provider = (provider or os.environ.get("LLM_PROVIDER") or "none").lower()
    if provider in ("mock", "none"):
        rep = {"status": "not_run", "reason": f"LLM_PROVIDER={provider}: the judge needs a real model"}
    else:
        os.environ["LLM_PROVIDER"] = provider
        import llm

        replies = load_replies()[:limit]
        scores, errors = {}, 0
        for r in replies:
            try:
                scores[r["id"]] = score(llm, r)
            except Exception:  # one failed call is counted, never fatal
                errors += 1
        vals = list(scores.values())
        rep = {"status": "ok", "n": len(vals), "errors": errors,
               "clarity_mean": round(sum(v["clarity"] for v in vals) / len(vals), 2) if vals else None,
               "tone_mean": round(sum(v["tone"] for v in vals) / len(vals), 2) if vals else None,
               "promises_flagged": sum(1 for v in vals if v["promises"]),
               "cost_usd": round(sum(float(v.get("cost_usd") or 0) for v in vals), 4),
               "validation": validate(scores), "scores": scores}
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "judge_report.yaml").write_text(yaml.safe_dump(rep, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G3 offline judge (clarity and tone)")
    ap.add_argument("--prepare", action="store_true", help="write the hand-scoring sheet (eval/judge/hand_scored.yaml)")
    ap.add_argument("--run", action="store_true", help="score the sample replies with the judge model")
    ap.add_argument("--provider", default=None)
    args = ap.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if args.prepare:
        print(f"Planilla: {prepare_sheet()}")
    if args.run or not args.prepare:
        rep = run(args.provider)
        print(f"Juez G3: {rep['status']}" + (f" ({rep.get('reason')})" if rep.get("reason") else
                                              f" · n={rep['n']} · claridad {rep['clarity_mean']} · tono {rep['tone_mean']}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
