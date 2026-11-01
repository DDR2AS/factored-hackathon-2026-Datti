"""Trace queries (INTERFACES.md #7) over the local JSONL traces; the same SQL runs in Athena over the
S3 traces in the cloud (plan v2 section 13, Thursday). Traces carry ids, names, latencies, tokens,
costs and error codes, never free text.

    python scripts/trace_report.py                  # data/traces/*.jsonl -> stdout summary
    python scripts/trace_report.py --since 2026-10-29
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO_ROOT / "data" / "traces"

QUERIES = {
    # one row per chat turn: total latency and cost (sum of its steps)
    "turns": """
        SELECT trace_id, any_value(session_id) AS session_id, max(turn) AS turn, sum(latency_ms) AS latency_ms,
               sum(COALESCE(cost_usd, 0)) AS cost_usd, bool_or(error_code IS NOT NULL) AS had_error
        FROM traces WHERE trace_id LIKE 'tr-%' GROUP BY trace_id""",
    "latency": """
        SELECT count(*) AS turns, quantile_cont(latency_ms, 0.5) AS p50_ms, quantile_cont(latency_ms, 0.95) AS p95_ms,
               max(latency_ms) AS max_ms FROM turns""",
    "steps": """
        SELECT actor, name, count(*) AS n, round(avg(latency_ms), 1) AS avg_ms,
               count(*) FILTER (WHERE error_code IS NOT NULL) AS errors
        FROM traces GROUP BY 1, 2 ORDER BY n DESC""",
    "errors": """
        SELECT error_code, actor, name, count(*) AS n FROM traces WHERE error_code IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY n DESC""",
    "models": """
        SELECT name, version, count(*) AS calls, sum(COALESCE(tokens_in, 0)) AS tokens_in,
               sum(COALESCE(tokens_out, 0)) AS tokens_out, round(sum(COALESCE(cost_usd, 0)), 6) AS cost_usd
        FROM traces WHERE actor = 'model' GROUP BY 1, 2 ORDER BY calls DESC""",
    "sessions": """
        SELECT count(DISTINCT session_id) AS sessions, round(avg(cost), 6) AS cost_per_session_usd
        FROM (SELECT session_id, sum(COALESCE(cost_usd, 0)) AS cost FROM traces GROUP BY 1)""",
    "lifecycle": """
        SELECT name, output_ref, count(*) AS n FROM traces
        WHERE trace_id LIKE 'lc-%' AND name IN ('lifecycle.transition', 'investigator', 'investigator_stub', 'analyst_decision')
        GROUP BY 1, 2 ORDER BY n DESC LIMIT 20""",
}


def connect(trace_dir: Path = TRACE_DIR, since: str | None = None) -> duckdb.DuckDBPyConnection | None:
    files = sorted(trace_dir.glob("*.jsonl"))
    if since:
        files = [f for f in files if f.stem >= since]
    if not files:
        return None
    con = duckdb.connect()
    lst = ", ".join(f"'{f.as_posix()}'" for f in files)
    con.execute(f"CREATE VIEW traces AS SELECT * FROM read_json_auto([{lst}], format='newline_delimited', "
                f"union_by_name=true, ignore_errors=true)")
    con.execute(f"CREATE VIEW turns AS {QUERIES['turns']}")
    return con


def summary(trace_dir: Path = TRACE_DIR, since: str | None = None) -> dict:
    con = connect(trace_dir, since)
    if con is None:
        return {"available": False}
    out = {"available": True}
    for name in ("latency", "steps", "errors", "models", "sessions", "lifecycle"):
        cur = con.execute(QUERIES[name])
        cols = [d[0] for d in cur.description]
        out[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Trace queries over data/traces/*.jsonl")
    ap.add_argument("--since", default=None, help="first file date (YYYY-MM-DD)")
    args = ap.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    s = summary(since=args.since)
    if not s["available"]:
        print("No hay trazas en data/traces/ (se escriben al usar la API local).")
        return 0
    for k, rows in s.items():
        if k == "available":
            continue
        print(f"\n== {k}")
        for r in rows[:15]:
            print("  ", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
