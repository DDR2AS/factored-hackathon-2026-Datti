"""Bronze: a faithful copy of the raw CSV files (every column VARCHAR) plus lineage columns.

- Schema contract: every file of a table must have the same header as the table's first file;
  a different header stops the run (schema evolution is detected, never silently merged).
- Lineage: ``_source_file`` (path relative to data/raw), ``_process_date`` (from the
  year=/month=/day= folder) and ``_ingested_at``.
- ``bronze_partition(table, day)`` processes one daily partition (the Lambda unit);
  ``bronze_backfill(table)`` processes every partition of a table in one statement and writes the
  same layout. Re-running either is idempotent.
"""

from __future__ import annotations

import csv
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from pipeline import config, warehouse

PARTITION_RE = re.compile(r"year=(\d{4})[\\/]month=(\d{2})[\\/]day=(\d{2})")


class SchemaContractError(RuntimeError):
    """A raw file's header differs from the table's contract (first file)."""


def read_header(path: Path) -> list[str]:
    with open(path, encoding="utf-8-sig", newline="") as fh:  # the bucket's CSVs carry a BOM
        return next(csv.reader(fh))


def raw_files(table: str, day: date | None = None) -> list[Path]:
    flat = config.RAW_DIR / f"{table}.csv"
    if flat.exists():
        return [flat]
    base = config.RAW_DIR / table
    if day is not None:
        folder = base / f"year={day:%Y}" / f"month={day:%m}" / f"day={day:%d}"
        return sorted(folder.glob("*.csv"))
    return sorted(base.glob("year=*/month=*/day=*/*.csv"))


def raw_partitions(table: str) -> list[date]:
    base = config.RAW_DIR / table
    out = set()
    for folder in base.glob("year=*/month=*/day=*"):
        m = PARTITION_RE.search(str(folder))
        if m:
            out.add(date(int(m[1]), int(m[2]), int(m[3])))
    return sorted(out)


def expected_header(table: str) -> list[str]:
    files = raw_files(table)
    if not files:
        raise FileNotFoundError(f"no raw files for {table} in {config.RAW_DIR}")
    return read_header(files[0])


def check_headers(table: str, files: list[Path], header: list[str] | None = None) -> list[str]:
    header = header or expected_header(table)
    for f in files:
        got = read_header(f)
        if got != header:
            raise SchemaContractError(f"schema contract broken in {f}: header differs from the table's first file\n"
                                      f"  expected: {header}\n  received: {got}")
    return header


PDATE_FROM_PATH = ("make_date(CAST(regexp_extract(filename, 'year=(\\d{4})', 1) AS INTEGER), "
                   "CAST(regexp_extract(filename, 'month=(\\d{2})', 1) AS INTEGER), "
                   "CAST(regexp_extract(filename, 'day=(\\d{2})', 1) AS INTEGER))")


def _select(files: list[Path], header: list[str], ingested_at: datetime, pdate_sql: str) -> str:
    """SELECT over the raw files with every column as VARCHAR (empty field = NULL) + lineage."""
    file_list = ", ".join(f"'{warehouse.posix(f)}'" for f in files)
    cols = ", ".join(f"'{c}': 'VARCHAR'" for c in header)
    raw_root = warehouse.posix(config.RAW_DIR) + "/"
    return (f"SELECT * EXCLUDE (filename), replace(replace(filename, '\\', '/'), '{raw_root}', '') AS _source_file, "
            f"{pdate_sql} AS _process_date, TIMESTAMPTZ '{ingested_at.isoformat()}' AS _ingested_at "
            f"FROM read_csv([{file_list}], header=true, columns={{{cols}}}, filename=true, "
            f"delim=',', quote='\"', escape='\"')")


def bronze_dim(con: duckdb.DuckDBPyConnection, table: str, ingested_at: datetime | None = None) -> int:
    files = raw_files(table)
    header = check_headers(table, files)
    ingested_at = ingested_at or datetime.now(timezone.utc)
    sql = _select(files, header, ingested_at, "CAST(NULL AS DATE)")
    return warehouse.write_parquet(con, sql, config.BRONZE_DIR / table / "data.parquet")


def bronze_partition(con: duckdb.DuckDBPyConnection, table: str, day: date,
                     ingested_at: datetime | None = None) -> int:
    """One daily partition of one table -> data/bronze/<table>/_process_date=<day>/data_0.parquet."""
    files = raw_files(table, day)
    out_dir = config.BRONZE_DIR / table / f"_process_date={day.isoformat()}"
    if not files:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        return 0
    header = check_headers(table, files)
    ingested_at = ingested_at or datetime.now(timezone.utc)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    return warehouse.write_parquet(con, _select(files, header, ingested_at, f"DATE '{day.isoformat()}'"),
                                   out_dir / "data_0.parquet")


def bronze_backfill(con: duckdb.DuckDBPyConnection, table: str, ingested_at: datetime | None = None) -> int:
    """Every partition of a daily table in one statement (same layout as bronze_partition)."""
    files = raw_files(table)
    header = check_headers(table, files)
    ingested_at = ingested_at or datetime.now(timezone.utc)
    out_dir = config.BRONZE_DIR / table
    return warehouse.write_partitioned(con, _select(files, header, ingested_at, PDATE_FROM_PATH), out_dir, "_process_date")


def run(con: duckdb.DuckDBPyConnection, tables: tuple[str, ...] | None = None, verbose: bool = True) -> dict[str, int]:
    counts = {}
    ingested_at = datetime.now(timezone.utc)
    for table in tables or config.RAW_TABLES:
        if not raw_files(table):
            if verbose:
                print(f"[bronze:{table}] no raw files, skipped")
            continue
        n = bronze_dim(con, table, ingested_at) if table in config.DIM_TABLES else bronze_backfill(con, table, ingested_at)
        counts[table] = n
        if verbose:
            print(f"[bronze:{table}] {n:,} rows", flush=True)
    return counts
