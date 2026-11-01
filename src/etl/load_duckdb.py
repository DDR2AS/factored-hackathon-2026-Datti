"""Load data/raw (real download or synthetic replica) into data/processed/latam_bank.duckdb.

Same tables and the same ``read_csv_auto`` reading as the load step of
``src/etl/ingest_s3_duckdb.py`` (Diego's script), factored out so the synthetic replica
(``src/etl/synth_dataset.py``) builds an identical exploration database without touching S3.
The pipeline (``src/pipeline``) reads the CSV files directly; this database is for exploration
(DBeaver, notebooks) and for ``src/etl/validate_data.py``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
DB_PATH = REPO_ROOT / "data" / "processed" / "latam_bank.duckdb"

FLAT_TABLES = ("branches", "customers", "daily_exchange_rates", "marketing_campaigns", "products", "service_agents")
PARTITIONED_TABLES = ("call_center_interactions", "call_transcripts", "campaign_sends", "complaints",
                      "satisfaction_surveys", "transactions", "digital_events")


def load_raw_to_duckdb(raw_dir: Path = RAW_DIR, db_path: Path = DB_PATH, verbose: bool = True) -> dict[str, int]:
    raw_dir, db_path = Path(raw_dir), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    counts: dict[str, int] = {}
    try:
        for table in FLAT_TABLES:
            path = raw_dir / f"{table}.csv"
            if not path.exists():
                print(f"[WARN] no se encontró {path}", file=sys.stderr)
                continue
            t0 = time.time()
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto(?)", [path.as_posix()])
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if verbose:
                print(f"[DUCKDB] {table}: {counts[table]:,} filas ({time.time() - t0:.1f}s)")
        for table in PARTITIONED_TABLES:
            folder = raw_dir / table
            if not folder.exists():
                print(f"[WARN] no se encontró la carpeta {folder}", file=sys.stderr)
                continue
            t0 = time.time()
            glob = (folder / "**" / "*.csv").as_posix()
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto(?, union_by_name=True)", [glob])
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if verbose:
                print(f"[DUCKDB] {table}: {counts[table]:,} filas ({time.time() - t0:.1f}s)")
    finally:
        con.close()
    return counts


if __name__ == "__main__":
    load_raw_to_duckdb()
