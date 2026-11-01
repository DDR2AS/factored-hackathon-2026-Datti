"""Gold: serving and analytics tables (INTERFACES.md #5; schemas in docs/data/gold_tables.md).

Each table is one SQL file in ``src/pipeline/sql/gold/`` over the silver views and the gold tables
built before it (``config.GOLD_ORDER``). Output: ``data/gold/<table>/data.parquet``.
``labels`` is the learning-loop table: the union of ``data/labels/*.parquet`` written by the
analyst console export or the simulated learning cycle (empty schema when there is none).
A small ``data/gold/_meta.yaml`` records the data source (organizers' dataset or synthetic
replica), the snapshot date and the row counts, so every consumer can label its numbers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import duckdb
import yaml

from pipeline import config, warehouse

LABELS_SCHEMA = ("label_id VARCHAR, case_id VARCHAR, source VARCHAR, label_type VARCHAR, value VARCHAR, "
                 "detail VARCHAR, model_version VARCHAR, created_at TIMESTAMP, synthetic BOOLEAN")


def _labels_sql() -> str:
    files = sorted(config.LABELS_DIR.glob("*.parquet")) if config.LABELS_DIR.exists() else []
    if not files:
        return f"SELECT * FROM (SELECT CAST(NULL AS VARCHAR) AS label_id) WHERE FALSE"
    file_list = ", ".join(f"'{warehouse.posix(f)}'" for f in files)
    return f"SELECT * FROM read_parquet([{file_list}], union_by_name=true)"


def build_table(con: duckdb.DuckDBPyConnection, name: str, snapshot: date) -> int:
    if name == "labels":
        sql = _labels_sql()
        if "WHERE FALSE" in sql:
            cols = ", ".join(f"CAST(NULL AS {c.split()[1]}) AS {c.split()[0]}" for c in LABELS_SCHEMA.split(", "))
            sql = f"SELECT {cols} WHERE FALSE"
    else:
        sql = warehouse.render(warehouse.read_sql("gold", name), snapshot)
    n = warehouse.write_parquet(con, sql, config.GOLD_DIR / name / "data.parquet")
    glob = warehouse.posix(config.GOLD_DIR / name / "data.parquet")
    con.execute(f"CREATE OR REPLACE VIEW gold.{name} AS SELECT * FROM read_parquet('{glob}')")
    return n


def run(con: duckdb.DuckDBPyConnection, snapshot: date, tables: tuple[str, ...] | None = None,
        verbose: bool = True) -> dict[str, int]:
    warehouse.register_layer(con, "silver", config.SILVER_DIR, [s for s, _ in config.SILVER_ORDER])
    warehouse.register_layer(con, "gold", config.GOLD_DIR, list(config.GOLD_ORDER))
    counts = {}
    for name in config.GOLD_ORDER:
        if tables and name not in tables:
            continue
        counts[name] = build_table(con, name, snapshot)
        if verbose:
            print(f"[gold:{name}] {counts[name]:,} rows", flush=True)
    write_meta(snapshot, counts)
    return counts


def write_meta(snapshot: date, counts: dict[str, int]) -> dict:
    meta_path = config.GOLD_DIR / "_meta.yaml"
    old = {}
    if meta_path.exists():
        old = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    rows = {**(old.get("rows") or {}), **counts}
    meta = {
        "data_source": config.data_source_label(),
        "synthetic": config.is_replica(),
        "replica_scale": round(config.replica_scale(), 6),
        "snapshot_date": snapshot.isoformat(),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": rows,
    }
    config.GOLD_DIR.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return meta


def read_meta() -> dict:
    meta_path = config.GOLD_DIR / "_meta.yaml"
    if not meta_path.exists():
        return {}
    return yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
