"""Learning loop, one demonstration cycle (plan v2 section 5). SIMULATED analyst decisions.

Every analyst action writes labels (which charge was right, whether the lane was right, whether the
recommendation was accepted or edited). This script runs one cycle end to end:

1. A fresh batch of planted scenarios (another seed than ``make eval``) goes through the app (v2).
2. A simulated analyst decides each lane B case with the ground truth: approve when the investigator's
   recommendation matches it, edit otherwise. Labels use the console convention (#10
   ``labels_emitted``) and go to ``data/labels/labels_<stamp>.parquet`` (``synthetic: true``,
   ``source: simulated_analyst``); ``make pipeline`` publishes them in ``gold.labels``.
3. Each confirmed charge becomes a labelled M1 query (what the customer said, the candidates of
   that day, the charge they confirmed). A candidate M1 is trained with them added to the training
   split and compared with the current M1 on the SAME frozen test queries.
4. The candidate is promoted only if it is at least as safe (wrong confirmations) and as accurate
   (top-1). Either way the decision and the numbers are written to ``data/eval/learning_loop.yaml``
   and ``docs/modelos/ciclo_aprendizaje.md``.

    python scripts/learning_loop.py
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS_DIR = REPO_ROOT / "data" / "labels"
EVAL_DIR = REPO_ROOT / "data" / "eval"
DOC = REPO_ROOT / "docs" / "modelos" / "ciclo_aprendizaje.md"
SEED = 777


def simulate_labels(rows: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []

    def add(case_id, label_type, value, detail=None, model_version=None):
        out.append({"label_id": "LB-" + uuid.uuid4().hex[:12], "case_id": case_id, "source": "simulated_analyst",
                    "label_type": label_type, "value": value, "detail": detail, "model_version": model_version,
                    "created_at": now, "synthetic": True})

    for r in rows:
        case_id = r.get("case_id")
        if not case_id:
            continue
        lane = r.get("lane")
        add(case_id, "lane", f"{lane}:{'confirmed' if r['scores'].get('lane_correct') else 'escalated'}")
        ev = r.get("match_event") or {}
        if ev.get("confirmed") and ev.get("chosen"):
            add(case_id, "match", f"{ev['chosen']}:{'confirmed' if ev['chosen'] == ev.get('target') else 'corrected'}",
                model_version="m1")
        rep = r.get("report")
        truth = (r.get("expected") or {}).get("recommendation")
        if rep and truth:
            ok = rep["recommendation"] in truth
            add(case_id, "decision", "approve" if ok else "edit")
            add(case_id, "recommendation", f"{rep['recommendation']}:{'accepted' if ok else 'edited'}",
                detail=None if ok else truth[0], model_version=rep.get("model_id"))
    return out


def write_labels(labels: list[dict]) -> Path | None:
    if not labels:
        return None
    import pandas as pd

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    path = LABELS_DIR / f"labels_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.parquet"
    pd.DataFrame(labels).to_parquet(path, index=False)
    return path


def queries_from_events(rows: list[dict]) -> list[dict]:
    """Confirmed charges -> labelled M1 queries (same window as the orchestrator, as of that day)."""
    from models.m1 import train as m1train

    by_customer, country = m1train.load_charges()
    out = []
    for r in rows:
        ev = r.get("match_event") or {}
        chosen, target = ev.get("chosen"), ev.get("target")
        if not (ev.get("confirmed") and chosen and chosen == target) or str(chosen).startswith("PL-"):
            continue
        cid = ev["customer_id"]
        ref = date.fromisoformat(ev["reference_date"]) if ev.get("reference_date") else None
        claimed = {k: (ev.get("claimed") or {}).get(k) for k in ("amount", "currency", "date", "merchant_text", "card_last4")}
        if ref is None or not any(claimed.get(k) for k in ("amount", "date", "merchant_text")):
            continue
        if claimed.get("date"):
            said = date.fromisoformat(claimed["date"])
            lo, hi = said - timedelta(days=7), min(said + timedelta(days=7), ref)
        else:
            lo, hi = ref - timedelta(days=30), ref
        cands = [dict(t) for t in by_customer.get(cid, []) if lo <= t["local_date"] <= hi]
        if not any(c["txn_id"] == chosen for c in cands):
            continue
        claimed["date_is_relative"] = None
        out.append({"customer_id": cid, "country": country.get(cid, "?"), "slots": claimed, "target": chosen,
                    "no_match": False, "candidates": cands, "n_slots": sum(1 for v in claimed.values() if v),
                    "source": "learning_loop"})
    return out


def run(n_scenarios: int = 240, verbose: bool = True) -> dict:
    from eval import runner
    from eval.scenarios import generate
    from models.m1 import train as m1train

    t0 = time.time()
    scs = generate(n=n_scenarios, seed=SEED)
    res = runner.run_config("v2", scs, verbose=verbose)
    labels = simulate_labels(res["rows"])
    path = write_labels(labels)
    queries = queries_from_events(res["rows"])
    current = yaml.safe_load((EVAL_DIR / "m1_report.yaml").read_text(encoding="utf-8"))
    cur = current["test"]["m1"]
    cand = m1train.train(write=False, verbose=verbose, extra_train=queries)
    new = cand["test"]["m1"]
    promote = (new["wrong_confirmation_rate"] <= cur["wrong_confirmation_rate"] and new["top1"] >= cur["top1"]
               and len(queries) > 0)
    version = None
    if promote:
        version = current["model_version"].split("+")[0] + f"+loop{datetime.now(timezone.utc):%Y%m%d}"
        m1train.train(write=True, verbose=verbose, extra_train=queries, version=version)
    rep = {
        "synthetic": True, "source": "simulated_analyst", "seed": SEED, "seconds": round(time.time() - t0, 1),
        "scenarios": len(scs), "labels": len(labels), "labels_file": str(path) if path else None,
        "labels_by_type": {t: sum(1 for x in labels if x["label_type"] == t) for t in sorted({x["label_type"] for x in labels})},
        "m1_labelled_queries": len(queries),
        "current": {"model_version": current["model_version"], **{k: cur[k] for k in ("top1", "wrong_confirmation_rate", "confirm_rate", "no_match_detection")}},
        "candidate": {k: new[k] for k in ("top1", "wrong_confirmation_rate", "confirm_rate", "no_match_detection")},
        "promoted": bool(promote), "promoted_version": version,
        "rule": "promote only if wrong confirmations <= current AND top-1 >= current, on the same frozen test queries",
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "learning_loop.yaml").write_text(yaml.safe_dump(rep, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _doc(rep)
    return rep


def _doc(r: dict) -> None:
    pct = lambda v: f"{100 * v:.2f}%"  # noqa: E731
    lines = [
        "# Ciclo de aprendizaje (una vuelta de demostración)", "",
        "Generado por `python scripts/learning_loop.py` (`src/eval/learning_loop.py`). **Decisiones de analista "
        "SIMULADAS** con la verdad sembrada de los escenarios; etiquetas marcadas `synthetic: true`.", "",
        f"- Escenarios nuevos (semilla {r['seed']}, distinta de `make eval`): {r['scenarios']}.",
        f"- Etiquetas escritas: {r['labels']} ({r['labels_by_type']}) en `data/labels/` → `gold.labels` con `make pipeline`.",
        f"- Consultas etiquetadas para M1 (cargos confirmados por el cliente): {r['m1_labelled_queries']}.", "",
        "| | M1 actual | Candidato con etiquetas |", "|---|---|---|",
        f"| Versión | `{r['current']['model_version']}` | {'`' + r['promoted_version'] + '`' if r['promoted'] else '(no promovido)'} |",
        f"| Top-1 (prueba congelada) | {pct(r['current']['top1'])} | {pct(r['candidate']['top1'])} |",
        f"| Confirmaciones equivocadas | {pct(r['current']['wrong_confirmation_rate'])} | {pct(r['candidate']['wrong_confirmation_rate'])} |",
        f"| Confirmación directa | {pct(r['current']['confirm_rate'])} | {pct(r['candidate']['confirm_rate'])} |",
        f"| Detección de cargo inexistente | {pct(r['current']['no_match_detection'])} | {pct(r['candidate']['no_match_detection'])} |",
        "", f"**Decisión:** {'se promovió el candidato' if r['promoted'] else 'se mantiene el modelo actual'}. Regla: {r['rule']}.", "",
        "En producción el mismo paso corre como trabajo de entrenamiento (SageMaker, pago por segundo) con las "
        "decisiones reales de la consola (`labels_emitted`), y M2 se reentrena igual con las correcciones de intención.",
    ]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    r = run()
    print(f"\nCiclo de aprendizaje: {r['labels']} etiquetas, {r['m1_labelled_queries']} consultas para M1, "
          f"{'PROMOVIDO ' + r['promoted_version'] if r['promoted'] else 'no promovido'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
