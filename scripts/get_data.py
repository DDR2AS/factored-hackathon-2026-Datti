"""``make data``: the organizers' dataset when the S3 key is configured, else the synthetic replica.

- Real data: ``.env`` has ``AWS_BUCKET_NAME``, ``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY`` (the
  read-only key from the data dictionary PDF; never in git). Runs Diego's
  ``src/etl/ingest_s3_duckdb.py`` (S3 -> data/raw -> data/processed/latam_bank.duckdb).
- Otherwise: ``src/etl/synth_dataset.py`` writes a SYNTHETIC replica with the same tables, columns
  and layout (data/raw/_SYNTHETIC_REPLICA.txt marks it) and loads the same DuckDB file.

    python scripts/get_data.py               # automatic
    python scripts/get_data.py --synthetic   # force the replica (e.g. --customers 1500 for a quick run)
    python scripts/get_data.py --real        # fail if the S3 key is missing
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src" / "etl"))


def has_s3_key() -> bool:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    return all(os.environ.get(k) for k in ("AWS_BUCKET_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--synthetic", action="store_true")
    g.add_argument("--real", action="store_true")
    ap.add_argument("--customers", type=int, default=int(os.environ.get("SYNTH_CUSTOMERS", "4000")))
    args = ap.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    real = args.real or (not args.synthetic and has_s3_key())
    if args.real and not has_s3_key():
        print("Falta la llave S3 en .env (AWS_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY).", file=sys.stderr)
        return 2
    if real:
        raw = REPO / "data" / "raw"
        marker = raw / "_SYNTHETIC_REPLICA.txt"
        if marker.exists():
            import shutil

            print("Hay una réplica sintética en data/raw: se borra antes de descargar los datos reales.")
            shutil.rmtree(raw)
        from src.etl.ingest_s3_duckdb import run_ingestion  # noqa: E402

        run_ingestion()
        return 0
    print("Sin llave S3 en .env: se genera la RÉPLICA SINTÉTICA del dataset (src/etl/synth_dataset.py).")
    import synth_dataset  # noqa: E402

    return synth_dataset.main(["--customers", str(args.customers)])


if __name__ == "__main__":
    raise SystemExit(main())
