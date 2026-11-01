"""Silver: bronze (all VARCHAR) -> typed, normalised, de-duplicated tables with dq_ flags.

One SQL file per table in ``src/pipeline/sql/silver/`` (ported unchanged from the v1.4 pipeline).
The SQL reads ``bronze.<table>`` and the silver dimensions it joins (``silver.products`` ...).
Dimension tables are rebuilt whole; daily tables are rebuilt whole (``build``) or one partition at a
time (``silver_partition``: the bronze view is restricted to that ``_process_date``). Deterministic
and idempotent.
"""

from __future__ import annotations

import shutil
from datetime import date

import duckdb

from pipeline import config, warehouse

BRONZE_OF = dict(config.SILVER_ORDER)


def _register_inputs(con: duckdb.DuckDBPyConnection, only_partition: tuple[str, date] | None = None) -> None:
    warehouse.register_layer(con, "bronze", config.BRONZE_DIR, config.RAW_TABLES)
    warehouse.register_layer(con, "silver", config.SILVER_DIR, [s for s, _ in config.SILVER_ORDER])
    if only_partition is not None:
        table, day = only_partition
        glob = warehouse.posix(config.BRONZE_DIR / table / f"_process_date={day.isoformat()}" / "*.parquet")
        con.execute(f"CREATE OR REPLACE VIEW bronze.{table} AS SELECT * FROM read_parquet('{glob}', union_by_name=true)")


def build_table(con: duckdb.DuckDBPyConnection, name: str, snapshot: date) -> int:
    """Rebuild one silver table completely."""
    _register_inputs(con)
    if warehouse.layer_glob(config.BRONZE_DIR, BRONZE_OF[name]) is None:
        return 0
    sql = warehouse.render(warehouse.read_sql("silver", name), snapshot)
    if name in config.SILVER_DAILY:
        n = warehouse.write_partitioned(con, sql, config.SILVER_DIR / name, "process_date")
    else:
        n = warehouse.write_parquet(con, sql, config.SILVER_DIR / name / "data.parquet")
    _register_inputs(con)  # later tables join this one
    return n


def silver_partition(con: duckdb.DuckDBPyConnection, name: str, day: date, snapshot: date) -> int:
    """Rebuild one process_date partition of a daily silver table from its bronze partition."""
    if name not in config.SILVER_DAILY:
        raise ValueError(f"{name} is not a daily table; rebuild it with build_table")
    source = BRONZE_OF[name]
    _register_inputs(con, only_partition=(source, day))
    out_dir = config.SILVER_DIR / name / f"process_date={day.isoformat()}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    if not (config.BRONZE_DIR / source / f"_process_date={day.isoformat()}").exists():
        return 0
    sql = warehouse.render(warehouse.read_sql("silver", name), snapshot)
    # The partition folder must only hold rows of that process_date (a contract rule checks it).
    return warehouse.write_parquet(con, f"SELECT * FROM ({sql}) WHERE process_date = DATE '{day.isoformat()}'",
                                   out_dir / "data_0.parquet")


def run(con: duckdb.DuckDBPyConnection, snapshot: date, tables: tuple[str, ...] | None = None,
        verbose: bool = True) -> dict[str, int]:
    counts = {}
    for name, _ in config.SILVER_ORDER:
        if tables and name not in tables:
            continue
        n = build_table(con, name, snapshot)
        counts[name] = n
        if verbose:
            print(f"[silver:{name}] {n:,} rows", flush=True)
    return counts
