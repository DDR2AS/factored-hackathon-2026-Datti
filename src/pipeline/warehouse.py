"""DuckDB helpers shared by every step: connection, views over the Parquet layers, SQL rendering.

Layout on disk (all git-ignored under data/):
  data/bronze/<table>/_process_date=YYYY-MM-DD/data_0.parquet   daily tables (all columns VARCHAR + lineage)
  data/bronze/<table>/data.parquet                               dimension tables
  data/silver/<table>/process_date=YYYY-MM-DD/data_0.parquet     daily tables (typed, dq_ flags)
  data/silver/<table>/data.parquet                               dimension tables
  data/gold/<table>/data.parquet                                 serving and analytics tables
Partition columns are also written inside the files, so readers never need hive partitioning.
"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

import duckdb

from pipeline import config


def connect(path: Path | str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """In-memory by default (every step re-creates its views); a file path for the warehouse."""
    con = duckdb.connect(str(path) if path else ":memory:", read_only=read_only)
    con.execute(f"SET memory_limit='{config.DUCKDB_MEMORY}'")
    con.execute(f"SET threads={config.DUCKDB_THREADS}")
    con.execute("SET preserve_insertion_order=false")
    for schema in ("bronze", "silver", "gold", "quality"):
        if not read_only:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    return con


def posix(path: Path) -> str:
    return Path(path).as_posix().replace("'", "''")


def layer_glob(layer_dir: Path, table: str) -> str | None:
    """Glob of the Parquet files of a table in a layer, or None if there are none."""
    base = layer_dir / table
    if (base / "data.parquet").exists():
        return posix(base / "data.parquet")
    if base.exists() and any(base.glob("*/*.parquet")):
        return posix(base) + "/*/*.parquet"
    return None


def register_layer(con: duckdb.DuckDBPyConnection, schema: str, layer_dir: Path,
                   tables: tuple[str, ...] | list[str] | None = None) -> list[str]:
    """CREATE OR REPLACE VIEW <schema>.<table> over every table found in a layer."""
    names = tables if tables is not None else sorted(p.name for p in layer_dir.glob("*") if p.is_dir())
    found = []
    for t in names:
        glob = layer_glob(layer_dir, t)
        if glob is None:
            continue
        con.execute(f"CREATE OR REPLACE VIEW {schema}.{t} AS SELECT * FROM read_parquet('{glob}', union_by_name=true)")
        found.append(t)
    return found


def render(sql: str, snapshot: date, extra: dict | None = None) -> str:
    """Replace {{name}} with the pipeline parameters (config.sql_params)."""
    params = {**config.sql_params(snapshot), **(extra or {})}

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in params:
            raise KeyError(f"unknown SQL parameter: {key}")
        return str(params[key])

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, sql)


def read_sql(kind: str, name: str) -> str:
    return (config.SQL_DIR / kind / f"{name}.sql").read_text(encoding="utf-8")


def write_parquet(con: duckdb.DuckDBPyConnection, select_sql: str, out_file: Path) -> int:
    """Write the result of a SELECT to one Parquet file (atomic replace); returns the row count."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(".tmp.parquet")
    con.execute(f"COPY ({select_sql}) TO '{posix(tmp)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    tmp.replace(out_file)
    return con.execute(f"SELECT COUNT(*) FROM read_parquet('{posix(out_file)}')").fetchone()[0]


def write_partitioned(con: duckdb.DuckDBPyConnection, select_sql: str, out_dir: Path, column: str) -> int:
    """Rewrite a whole partitioned table (one folder per value of ``column``)."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    con.execute(f"COPY ({select_sql}) TO '{posix(out_dir)}' (FORMAT PARQUET, COMPRESSION ZSTD, "
                f"PARTITION_BY ({column}), WRITE_PARTITION_COLUMNS true, FILENAME_PATTERN 'data_{{i}}')")
    return con.execute(f"SELECT COUNT(*) FROM read_parquet('{posix(out_dir)}/*/*.parquet')").fetchone()[0]


def snapshot_date(con: duckdb.DuckDBPyConnection | None = None) -> date:
    """Last process_date of silver transactions (the dataset cut-off), else the default."""
    glob = layer_glob(config.SILVER_DIR, "transactions")
    if glob is None:
        return config.DEFAULT_SNAPSHOT
    own = con is None
    con = con or connect()
    try:
        d = con.execute(f"SELECT max(process_date) FROM read_parquet('{glob}')").fetchone()[0]
    finally:
        if own:
            con.close()
    return d or config.DEFAULT_SNAPSHOT


def build_warehouse_views(path: Path | None = None) -> None:
    """data/processed/warehouse.duckdb with views bronze/silver/gold over the Parquet files
    (for DBeaver, notebooks and ad-hoc SQL; nothing reads it in the app)."""
    path = Path(path or config.WAREHOUSE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = connect(path)
    try:
        register_layer(con, "bronze", config.BRONZE_DIR)
        register_layer(con, "silver", config.SILVER_DIR)
        register_layer(con, "gold", config.GOLD_DIR)
        q = config.QUALITY_DIR / "dq_results.parquet"
        if q.exists():
            con.execute(f"CREATE OR REPLACE VIEW quality.dq_results AS SELECT * FROM read_parquet('{posix(q)}')")
    finally:
        con.close()
