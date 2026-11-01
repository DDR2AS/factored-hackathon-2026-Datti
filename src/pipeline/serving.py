"""Serving export: gold -> DynamoDB items (plan v2 section 8, "serving-table export").

DynamoDB "import from S3" always creates a NEW table, so each publish builds the next serving
table and the app switches to it with one config change (``TABLE_SERVING``). This module writes
the import file locally: ``data/serving/serving-<version>.jsonl.gz`` in DynamoDB JSON
(``{"Item": {"pk": {"S": ...}, ...}}``), one item per record, partition key = the customer:

    pk = CUST#<customer_id>
    sk = PROFILE | PRODUCT#<product_id> | TXN#<local_date>#<txn_id> | BASELINE
         | CATBASE#<category> | FEE#<product_id>#<fee_code> | CONTACT#<date>#<contact_id>
    merchant aggregates: pk = MERCHANT#<merchant_id>, sk = STATS

Everything the gateway may read for one customer sits under that customer's pk, so an IAM
condition on ``dynamodb:LeadingKeys`` can enforce the scope in production. ``is_fraud`` (a label)
is never exported. The upload to S3 and the ImportTable call belong to the cloud side (CDK).
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from pipeline import config, warehouse

TXN_FIELDS = ("txn_id", "product_id", "card_last4", "local_date", "local_time", "amount", "currency", "amount_usd",
              "merchant_id", "merchant_name", "merchant_category", "status", "response_code", "fraud_score",
              "txn_type", "reversal_of", "channel", "txn_country", "is_foreign", "origin")


def _av(v):
    """Python value -> DynamoDB attribute value (None -> NULL)."""
    if v is None:
        return {"NULL": True}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, (int, float, Decimal)):
        return {"N": str(v)}
    if isinstance(v, (date, datetime)):
        return {"S": v.isoformat()}
    if isinstance(v, (list, tuple)):
        return {"L": [_av(x) for x in v]}
    return {"S": str(v)}


def _item(pk: str, sk: str, record: dict) -> str:
    item = {"pk": {"S": pk}, "sk": {"S": sk}}
    for k, v in record.items():
        if k in ("pk", "sk"):
            continue
        item[k] = _av(v)
    return json.dumps({"Item": item}, ensure_ascii=False)


def _rows(con: duckdb.DuckDBPyConnection, sql: str):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        for r in batch:
            yield dict(zip(cols, r))


def export(con: duckdb.DuckDBPyConnection | None = None, version: str | None = None,
           out_dir: Path | None = None) -> tuple[Path, int]:
    own = con is None
    con = con or warehouse.connect()
    out_dir = out_dir or config.SERVING_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    version = version or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    path = out_dir / f"serving-{version}.jsonl.gz"
    g = lambda t: warehouse.posix(config.GOLD_DIR / t / "data.parquet")  # noqa: E731
    n = 0
    try:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for r in _rows(con, f"SELECT * EXCLUDE (age, registration_date) FROM read_parquet('{g('customer_profile')}')"):
                fh.write(_item(f"CUST#{r['customer_id']}", "PROFILE", r) + "\n"); n += 1
            for r in _rows(con, f"SELECT product_id, customer_id, kind, name, currency, card_last4, card_id, status, is_card "
                                f"FROM read_parquet('{g('customer_products')}')"):
                fh.write(_item(f"CUST#{r['customer_id']}", f"PRODUCT#{r['product_id']}", r) + "\n"); n += 1
            cols = ", ".join(TXN_FIELDS)
            for t in ("transactions_enriched", "fee_charges"):
                for r in _rows(con, f"SELECT customer_id, {cols} FROM read_parquet('{g(t)}')"):
                    fh.write(_item(f"CUST#{r['customer_id']}", f"TXN#{r['local_date']}#{r['txn_id']}", r) + "\n"); n += 1
            for r in _rows(con, f"SELECT * FROM read_parquet('{g('customer_baseline')}')"):
                fh.write(_item(f"CUST#{r['customer_id']}", "BASELINE", r) + "\n"); n += 1
            for r in _rows(con, f"SELECT * FROM read_parquet('{g('customer_category_baseline')}')"):
                fh.write(_item(f"CUST#{r['customer_id']}", f"CATBASE#{r['merchant_category']}", r) + "\n"); n += 1
            for r in _rows(con, f"SELECT * FROM read_parquet('{g('fee_schedule')}')"):
                fh.write(_item(f"CUST#{r['customer_id']}", f"FEE#{r['product_id']}#{r['fee_code']}", r) + "\n"); n += 1
            for r in _rows(con, f"SELECT * EXCLUDE (ts) FROM read_parquet('{g('contact_history')}')"):
                fh.write(_item(f"CUST#{r['customer_id']}", f"CONTACT#{r['date']}#{r['contact_id']}", r) + "\n"); n += 1
            for r in _rows(con, f"SELECT * FROM read_parquet('{g('merchant_stats')}')"):
                fh.write(_item(f"MERCHANT#{r['merchant_id']}", "STATS", r) + "\n"); n += 1
    finally:
        if own:
            con.close()
    return path, n
