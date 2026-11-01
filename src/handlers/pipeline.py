"""Pipeline entry point (PipelineStack): one partition per invocation, fanned out by Step Functions.

Events (the pipeline state machine runs them in this order):
- ``{"step": "dimensions"}``: the six dimension tables (one CSV each) to bronze and silver in the lake;
  the daily partitions join them (``silver.products`` ...).
- ``{"table": "transactions", "date": "2026-06-17", "refresh_gold": false}``: ``pipeline.run.run_partition``
  for that daily partition. ``refresh_gold`` defaults to true (``run_partition``'s default, for a
  single late partition); the backfill map sends false and refreshes gold once at the end.
- ``{"step": "gold", "serving": true}``: rebuild gold from the whole silver layer of the lake and
  write the DynamoDB import file (``pipeline/serving.py``) under ``serving/<version>/``.

The same code runs on a laptop with DuckDB: here it works in ``DATA_DIR`` (``/tmp/data``, emptied on
every invocation because Lambda reuses containers) and ``cloud/lake_sync.py`` copies only what the
step needs:

- in: the partition's raw CSVs from ``BUCKET_RAW`` (under ``RAW_PREFIX``, ``data/``) plus the table's
  first file (schema contract), and from ``BUCKET_LAKE`` the silver tables the partition's SQL
  joins (``silver.products`` ...), or the whole silver layer when gold is rebuilt;
- out: ``bronze/<table>/_process_date=<day>/`` and ``silver/<table>/process_date=<day>/`` replaced in
  the lake (removed if the partition disappeared from raw), and ``gold/`` when it was rebuilt.

The quality contracts (``make pipeline``) are not run here: their report needs pyarrow, which does
not fit next to DuckDB and pandas in a Lambda package (``cloud/requirements-pipeline.txt``).
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date
from pathlib import Path

DEFAULT_DATA_DIR = "/tmp/data"
DEFAULT_RAW_PREFIX = "data/"


def _workspace(env) -> Path:
    data_dir = Path(env.get("DATA_DIR") or DEFAULT_DATA_DIR)
    shutil.rmtree(data_dir, ignore_errors=True)  # a reused container keeps /tmp
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir.parent)  # DuckDB spills to a relative .tmp; /var/task is read only
    from pipeline import config

    config.set_data_dir(data_dir)
    return data_dir


def _s3():
    from cloud import aws

    return aws.client("s3", read_timeout=60.0)


def silver_inputs(name: str) -> list[str]:
    """The other silver tables a silver table's SQL reads (``silver.<table>``)."""
    from pipeline import warehouse

    sql = warehouse.read_sql("silver", name)
    return sorted({t for t in re.findall(r"\bsilver\.(\w+)", sql) if t != name})


def handler(event, context):
    step = (event or {}).get("step")
    if step == "dimensions":
        return _dimensions(os.environ)
    if step == "gold":
        return _gold(event, os.environ)
    if step is not None:
        raise ValueError(f"unknown pipeline step {step!r}")
    table = (event or {}).get("table")
    day_raw = (event or {}).get("date")
    if not table or not day_raw:
        raise ValueError("Each partition needs 'table' and 'date'.")
    day = date.fromisoformat(str(day_raw))
    refresh_gold = bool(event.get("refresh_gold", True))
    return run_partition(table, day, refresh_gold, os.environ)


def run_partition(table: str, day: date, refresh_gold: bool, env) -> dict:
    from cloud import lake_sync
    from pipeline import config, run

    if table not in config.DAILY_TABLES:
        raise ValueError(f"{table} is not a daily table (daily: {', '.join(config.DAILY_TABLES)})")
    raw_bucket, lake = env["BUCKET_RAW"], env["BUCKET_LAKE"]
    raw_prefix = env.get("RAW_PREFIX", DEFAULT_RAW_PREFIX)
    _workspace(env)
    s3 = _s3()
    silver_name = run.SILVER_OF[table]
    raw_files = lake_sync.download_raw_partition(s3, raw_bucket, raw_prefix, table, day, config.RAW_DIR)
    lake_sync.download_replica_marker(s3, raw_bucket, raw_prefix, config.RAW_DIR)
    if refresh_gold:
        lake_sync.download_prefix(s3, lake, "silver/", config.SILVER_DIR)
        lake_sync.download_prefix(s3, lake, "labels/", config.LABELS_DIR)
    else:
        for dep in silver_inputs(silver_name):
            lake_sync.download_prefix(s3, lake, f"silver/{dep}/", config.SILVER_DIR / dep)
    result = run.run_partition(table, day, refresh_gold=refresh_gold, verbose=False)
    out = {
        "bronze": lake_sync.replace_prefix(s3, lake, config.BRONZE_DIR / table / f"_process_date={day.isoformat()}",
                                           f"bronze/{table}/_process_date={day.isoformat()}/"),
        "silver": lake_sync.replace_prefix(s3, lake, config.SILVER_DIR / silver_name / f"process_date={day.isoformat()}",
                                           f"silver/{silver_name}/process_date={day.isoformat()}/"),
    }
    if refresh_gold:
        out["gold"] = lake_sync.replace_prefix(s3, lake, config.GOLD_DIR, "gold/")
    return {**result, "raw_files": raw_files, "gold_refreshed": refresh_gold, "lake": out, "status": "ok"}


def _dimensions(env) -> dict:
    """Raw dimension CSVs -> bronze -> silver (the same functions as ``make pipeline``)."""
    from cloud import lake_sync
    from pipeline import bronze, config, silver, warehouse

    raw_bucket, lake = env["BUCKET_RAW"], env["BUCKET_LAKE"]
    raw_prefix = env.get("RAW_PREFIX", DEFAULT_RAW_PREFIX)
    _workspace(env)
    s3 = _s3()
    found = []
    for table in config.DIM_TABLES:
        key = f"{raw_prefix}{table}.csv"
        if key in lake_sync.keys(s3, raw_bucket, key):
            lake_sync.download(s3, raw_bucket, key, config.RAW_DIR / f"{table}.csv")
            found.append(table)
    missing = sorted(set(config.DIM_TABLES) - set(found))
    if missing:
        raise RuntimeError(f"raw dimension files missing in s3://{raw_bucket}/{raw_prefix}: {', '.join(missing)}")
    lake_sync.download_replica_marker(s3, raw_bucket, raw_prefix, config.RAW_DIR)
    names = [s for s, b in config.SILVER_ORDER if b in config.DIM_TABLES]
    con = warehouse.connect()
    try:
        n_bronze = bronze.run(con, tables=config.DIM_TABLES, verbose=False)
        snap = warehouse.snapshot_date(con)  # no silver transactions here: the dataset cut-off
        n_silver = silver.run(con, snap, tables=tuple(names), verbose=False)
    finally:
        con.close()
    published = {
        "bronze": {t: lake_sync.replace_prefix(s3, lake, config.BRONZE_DIR / t, f"bronze/{t}/")
                   for t in config.DIM_TABLES},
        "silver": {s: lake_sync.replace_prefix(s3, lake, config.SILVER_DIR / s, f"silver/{s}/") for s in names},
    }
    return {"step": "dimensions", "bronze_rows": n_bronze, "silver_rows": n_silver, "snapshot_date": snap.isoformat(),
            "lake": published, "status": "ok"}


def _gold(event, env) -> dict:
    from cloud import lake_sync
    from pipeline import config, run

    lake = env["BUCKET_LAKE"]
    _workspace(env)
    s3 = _s3()
    if env.get("BUCKET_RAW"):
        lake_sync.download_replica_marker(s3, env["BUCKET_RAW"], env.get("RAW_PREFIX", DEFAULT_RAW_PREFIX), config.RAW_DIR)
    n_silver = lake_sync.download_prefix(s3, lake, "silver/", config.SILVER_DIR)
    if n_silver == 0:
        raise RuntimeError("the lake has no silver layer yet: run the partitions first")
    lake_sync.download_prefix(s3, lake, "labels/", config.LABELS_DIR)
    serving = bool(event.get("serving", True))
    result = run.build_all(steps=("gold",), serving=serving, verbose=False)
    out = {"step": "gold", "snapshot_date": result.get("snapshot_date"), "seconds": result.get("seconds"),
           "gold": lake_sync.replace_prefix(s3, lake, config.GOLD_DIR, "gold/"), "status": "ok"}
    if serving and result.get("serving"):
        path = Path(result["serving"]["path"])
        version = path.name.removeprefix("serving-").split(".")[0]
        key = f"serving/{version}/{path.name}"
        s3.upload_file(str(path), lake, key)
        out["serving"] = {"import_prefix": f"serving/{version}/", "key": key, "items": result["serving"]["items"]}
    return out
