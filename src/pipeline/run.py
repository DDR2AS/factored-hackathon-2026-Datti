"""Pipeline entry points.

``run_partition(table, day)`` processes ONE daily partition (bronze + silver of that day) and then
refreshes gold: the unit a Lambda runs when a new partition lands (plan v2 section 8, incremental
loading and late arrivals). ``build_all`` is the full local run: bronze backfill -> silver ->
quality -> gold -> (serving export).

CLI (repo root; scripts/run_pipeline.py adds src/ to the path)::

    python scripts/run_pipeline.py                                   # everything
    python scripts/run_pipeline.py --steps silver quality gold       # reprocess without re-reading CSVs
    python scripts/run_pipeline.py --partition transactions 2026-06-17   # one partition + gold
    python scripts/run_pipeline.py --serving                         # also write the DynamoDB import file
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from pipeline import bronze, config, gold, quality, silver, warehouse

STEPS = ("bronze", "silver", "quality", "gold")
SILVER_OF = {b: s for s, b in config.SILVER_ORDER}


def run_partition(table: str, day: date | str, refresh_gold: bool = True, verbose: bool = True) -> dict:
    """One daily partition of one raw table: bronze -> silver (that day) -> gold refresh.
    Idempotent: running it twice gives the same files. A partition that disappeared from raw is
    removed from bronze and silver (late corrections)."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    if table not in config.DAILY_TABLES:
        raise ValueError(f"{table} is not a daily table (daily: {', '.join(config.DAILY_TABLES)})")
    t0 = time.time()
    con = warehouse.connect()
    try:
        snap = warehouse.snapshot_date(con)
        n_bronze = bronze.bronze_partition(con, table, day)
        n_silver = silver.silver_partition(con, SILVER_OF[table], day, snap)
        result = {"table": table, "date": day.isoformat(), "bronze_rows": n_bronze, "silver_rows": n_silver}
        if refresh_gold:
            snap = warehouse.snapshot_date(con)
            result["gold"] = gold.run(con, snap, verbose=False)
    finally:
        con.close()
    result["seconds"] = round(time.time() - t0, 2)
    if verbose:
        print(f"[partition] {table} {day}: bronze {result['bronze_rows']}, silver {result['silver_rows']} "
              f"({result['seconds']} s)")
    return result


def build_all(steps: tuple[str, ...] = STEPS, serving: bool = False, verbose: bool = True) -> dict:
    t0 = time.time()
    out: dict = {"data_source": config.data_source_label()}
    con = warehouse.connect()
    try:
        if "bronze" in steps:
            out["bronze"] = bronze.run(con, verbose=verbose)
        snap = warehouse.snapshot_date(con)
        if "silver" in steps:
            out["silver"] = silver.run(con, snap, verbose=verbose)
            snap = warehouse.snapshot_date(con)
        out["snapshot_date"] = snap.isoformat()
        if "quality" in steps:
            df = quality.run(con, snap, verbose=verbose)
            out["quality"] = quality.summary(df)
        if "gold" in steps:
            out["gold"] = gold.run(con, snap, verbose=verbose)
            if config.DATA_DIR.resolve() == (config.REPO_ROOT / "data").resolve():  # not for temp test folders
                from pipeline import docs

                docs.write()
        if serving:
            from pipeline import serving as serving_mod

            path, n = serving_mod.export(con)
            out["serving"] = {"path": str(path), "items": n}
    finally:
        con.close()
    warehouse.build_warehouse_views()
    out["seconds"] = round(time.time() - t0, 1)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Expediente Vivo data pipeline (bronze -> silver -> quality -> gold)")
    ap.add_argument("--steps", nargs="*", choices=STEPS, default=list(STEPS))
    ap.add_argument("--partition", nargs=2, metavar=("TABLE", "DATE"), help="process one daily partition, then gold")
    ap.add_argument("--serving", action="store_true", help="also write the DynamoDB import file (data/serving)")
    args = ap.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if not config.RAW_DIR.exists():
        print(f"No hay datos en {config.RAW_DIR}: corre primero `make data` (S3 o réplica sintética).", file=sys.stderr)
        return 2
    if args.partition:
        run_partition(args.partition[0], args.partition[1])
        return 0
    out = build_all(tuple(args.steps), serving=args.serving)
    print(f"\nFuente de datos: {out['data_source']} · snapshot {out.get('snapshot_date')} · {out['seconds']} s")
    q = out.get("quality")
    if q:
        print(f"Calidad: {q['checks']} checks · {q['pass']} PASS · {q['warn']} WARN · {q['fail']} FAIL "
              f"(reporte en docs/data/dq_report.md)")
        if q["fail"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
