"""The S3 copies the pipeline Lambda makes around ``pipeline.run`` (nothing else lives here).

Layout in the lake bucket (``BUCKET_LAKE``) = the local ``data/`` layout of ``pipeline/warehouse.py``:
``bronze/<table>/_process_date=<day>/``, ``silver/<table>/process_date=<day>/`` or
``silver/<table>/data.parquet``, ``gold/<table>/data.parquet``, ``serving/<version>/*.jsonl.gz``.
The raw bucket (``BUCKET_RAW``) keeps the organizers' layout under ``RAW_PREFIX`` (``data/``):
``<table>.csv`` for dimensions, ``<table>/year=YYYY/month=MM/day=DD/*.csv`` for daily tables.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

DELETE_BATCH = 1000


def keys(s3: Any, bucket: str, prefix: str) -> list[str]:
    out: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out += [o["Key"] for o in page.get("Contents", []) if not o["Key"].endswith("/")]
    return sorted(out)


def download(s3: Any, bucket: str, key: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(dest))


def download_prefix(s3: Any, bucket: str, prefix: str, dest_dir: Path) -> int:
    """Every object under ``prefix`` into ``dest_dir`` (relative paths kept). Returns how many."""
    found = keys(s3, bucket, prefix)
    for key in found:
        download(s3, bucket, key, Path(dest_dir) / key[len(prefix):])
    return len(found)


def raw_partition_prefix(raw_prefix: str, table: str, day: date) -> str:
    return f"{raw_prefix}{table}/year={day:%Y}/month={day:%m}/day={day:%d}/"


def download_raw_partition(s3: Any, bucket: str, raw_prefix: str, table: str, day: date, raw_dir: Path) -> int:
    """The CSV files of one daily partition, plus the table's first file (``bronze.expected_header``
    compares every partition with it: the schema contract). Returns the partition's file count."""
    n = download_prefix(s3, bucket, raw_partition_prefix(raw_prefix, table, day), Path(raw_dir) / table
                        / f"year={day:%Y}" / f"month={day:%m}" / f"day={day:%d}")
    first = next(iter(keys(s3, bucket, f"{raw_prefix}{table}/")), None)
    if first is not None:
        local = Path(raw_dir) / first[len(raw_prefix):]
        if not local.exists():
            download(s3, bucket, first, local)
    return n


def download_replica_marker(s3: Any, bucket: str, raw_prefix: str, raw_dir: Path) -> bool:
    """``_SYNTHETIC_REPLICA.txt`` (D15) when the raw bucket holds the synthetic replica, so gold's
    ``_meta.yaml`` keeps saying where its data came from. False when it is the organizers' data."""
    from pipeline import config

    key = f"{raw_prefix}{config.REPLICA_MARKER}"
    if key not in keys(s3, bucket, key):
        return False
    download(s3, bucket, key, Path(raw_dir) / config.REPLICA_MARKER)
    return True


def delete_prefix(s3: Any, bucket: str, prefix: str) -> int:
    found = keys(s3, bucket, prefix)
    for start in range(0, len(found), DELETE_BATCH):
        batch = [{"Key": k} for k in found[start:start + DELETE_BATCH]]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
    return len(found)


def upload_dir(s3: Any, bucket: str, src_dir: Path, prefix: str) -> int:
    src_dir = Path(src_dir)
    if not src_dir.exists():
        return 0
    n = 0
    for path in sorted(p for p in src_dir.rglob("*") if p.is_file() and not p.name.endswith(".tmp.parquet")):
        s3.upload_file(str(path), bucket, prefix + path.relative_to(src_dir).as_posix())
        n += 1
    return n


def replace_prefix(s3: Any, bucket: str, src_dir: Path, prefix: str) -> dict[str, int]:
    """Make ``prefix`` hold exactly ``src_dir`` (a partition that disappeared locally is removed)."""
    deleted = delete_prefix(s3, bucket, prefix)
    return {"deleted": deleted, "uploaded": upload_dir(s3, bucket, src_dir, prefix)}
