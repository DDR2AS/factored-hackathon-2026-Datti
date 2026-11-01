"""``make train``: M1 (transaction ranker), M2 (intent classifier) and M3 (risk evidence + lift
experiment). Needs the gold tables (make data && make pipeline) for M1 and M3; M2 trains on generated
text only. Each model writes its artifacts under src/models/artifacts/, its key in
src/models/thresholds.yaml, a report in data/eval/ and a page in docs/modelos/.

    python scripts/train.py            # all
    python scripts/train.py m1 m3      # some
"""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

MODELS = ("m1", "m2", "m3")


def main(argv: list[str] | None = None) -> int:
    which = [m for m in (argv if argv is not None else sys.argv[1:]) if m in MODELS] or list(MODELS)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    gold = REPO / "data" / "gold" / "transactions_enriched" / "data.parquet"
    if any(m in ("m1", "m3") for m in which) and not gold.exists():
        print("No hay tablas gold: corre `make data` y `make pipeline` antes de entrenar M1 y M3.", file=sys.stderr)
        return 2
    failed = []
    for m in which:
        t0 = time.time()
        print(f"\n=== {m.upper()} ===", flush=True)
        try:
            mod = importlib.import_module(f"models.{m}.train")
            if hasattr(mod, "train"):
                mod.train()
            else:
                mod.main([])
        except SystemExit as e:  # a training script that ends with sys.exit(0)
            if e.code not in (0, None):
                failed.append(m)
        except Exception as e:  # report and go on with the other models
            print(f"[{m}] falló: {type(e).__name__}: {e}", file=sys.stderr)
            failed.append(m)
        print(f"[{m}] {time.time() - t0:.0f} s", flush=True)
    if failed:
        print(f"\nModelos con error: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nListo: artefactos en src/models/artifacts/, umbrales en src/models/thresholds.yaml, "
          "reportes en data/eval/*_report.yaml y docs/modelos/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
