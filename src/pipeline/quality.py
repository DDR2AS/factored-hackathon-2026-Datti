"""Quality: validate the silver layer against ``src/pipeline/contracts/silver.yml``.

Output: ``data/quality/dq_results.parquet`` (one row per check) and ``docs/data/dq_report.md``.
Severity ``error`` = contract broken (the run reports FAIL and ``make pipeline`` exits 1);
``warn`` = known defect of the dataset, documented, never blocks.

On the synthetic replica the ``min_rows`` contracts (written for the 150k-customer dataset) are
scaled by customers/150,000 and the report says so.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import yaml

from pipeline import config, warehouse


def _lit(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def checks_for(table: str, spec: dict, lag_days: int, snapshot: date, scale: float):
    """Yields (check, column, severity, SQL counting FAILING rows, description)."""
    t = f"silver.{table} AS t"
    pk = spec.get("primary_key")
    if pk:
        cols = pk if isinstance(pk, list) else [pk]
        cl = ", ".join(cols)
        yield ("primary_key_unica", cl, "error",
               f"SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) n FROM {t} GROUP BY {cl} HAVING COUNT(*) > 1)", "")
        yield ("primary_key_no_nula", cl, "error",
               f"SELECT COUNT(*) FROM {t} WHERE " + " OR ".join(f"{c} IS NULL" for c in cols), "")
    for col in spec.get("unique", []):
        yield ("unico", col, "error",
               f"SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) n FROM {t} WHERE {col} IS NOT NULL GROUP BY {col} "
               f"HAVING COUNT(*) > 1)", "")
    if "min_rows" in spec:
        # On the replica the contract is scaled to its size with a 5% tolerance (it is a random draw).
        need = max(1, int(spec["min_rows"] * scale * (0.95 if scale < 1 else 1)))
        desc = f">= {need:,} filas" + (f" (contrato {spec['min_rows']:,} escalado a la réplica x{scale:.4f}, "
                                       f"tolerancia 5%)" if scale < 1 else "")
        yield ("filas_minimas", "", "error", f"SELECT CASE WHEN COUNT(*) < {need} THEN 1 ELSE 0 END FROM {t}", desc)
    for col in spec.get("not_null", []):
        yield ("no_nulo", col, "error", f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL", "")
    for col, values in (spec.get("accepted_values") or {}).items():
        yield ("valores_permitidos", col, "error",
               f"SELECT COUNT(*) FROM {t} WHERE {col} IS NOT NULL AND CAST({col} AS VARCHAR) NOT IN "
               f"({', '.join(_lit(v) for v in values)})", ", ".join(map(str, values)))
    for col, (lo, hi) in (spec.get("ranges") or {}).items():
        conds = ([f"{col} < {lo}"] if lo is not None else []) + ([f"{col} > {hi}"] if hi is not None else [])
        yield ("rango", col, "error", f"SELECT COUNT(*) FROM {t} WHERE {' OR '.join(conds)}", f"[{lo}, {hi}]")
    for fk in spec.get("foreign_keys", []):
        ref_table, ref_col = fk["ref"].split(".")
        yield ("llave_foranea", fk["column"], fk.get("severity", "error"),
               f"SELECT COUNT(*) FROM {t} WHERE {fk['column']} IS NOT NULL AND {fk['column']} NOT IN "
               f"(SELECT {ref_col} FROM silver.{ref_table})", f"-> {fk['ref']}")
    for rule in spec.get("rules", []):
        yield (rule["name"], "", rule.get("severity", "error"),
               f"SELECT COUNT(*) FROM {t} WHERE {rule['when']}", rule.get("description", ""))
    if spec.get("freshness"):
        col = spec["freshness"]
        yield ("frescura", col, "warn",
               f"SELECT CASE WHEN MAX({col}) < DATE '{snapshot.isoformat()}' - INTERVAL {lag_days} DAY THEN 1 ELSE 0 END FROM {t}",
               f"MAX({col}) >= {snapshot.isoformat()} - {lag_days} día(s)")


def run(con: duckdb.DuckDBPyConnection, snapshot: date, verbose: bool = True, write_report: bool = True) -> pd.DataFrame:
    warehouse.register_layer(con, "silver", config.SILVER_DIR, [s for s, _ in config.SILVER_ORDER])
    contracts = yaml.safe_load(config.CONTRACTS.read_text(encoding="utf-8"))
    lag = contracts.get("freshness_max_lag_days", 1)
    scale = config.replica_scale()
    run_at = datetime.now(timezone.utc)
    rows = []
    for table, spec in contracts["tables"].items():
        if warehouse.layer_glob(config.SILVER_DIR, table) is None:
            continue
        total = con.execute(f"SELECT COUNT(*) FROM silver.{table}").fetchone()[0]
        for check, column, severity, sql, desc in checks_for(table, spec, lag, snapshot, scale):
            failed = int(con.execute(sql).fetchone()[0] or 0)
            status = "PASS" if failed == 0 else ("FAIL" if severity == "error" else "WARN")
            rows.append({"run_at": run_at, "table_name": table, "check_name": check, "column_name": column,
                         "severity": severity, "failed_rows": failed, "total_rows": total,
                         "failed_pct": round(100 * failed / total, 3) if total else 0.0,
                         "status": status, "description": desc})
        if verbose:
            print(f"[quality:{table}] {sum(r['table_name'] == table for r in rows)} checks", flush=True)
    df = pd.DataFrame(rows)
    config.QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(config.QUALITY_DIR / "dq_results.parquet", index=False)
    if write_report:
        _write_report(df, scale)
    return df


def summary(df: pd.DataFrame) -> dict[str, int]:
    return {"checks": int(len(df)), "pass": int((df.status == "PASS").sum()), "warn": int((df.status == "WARN").sum()),
            "fail": int((df.status == "FAIL").sum())}


def _write_report(df: pd.DataFrame, scale: float) -> None:
    s = summary(df)
    source = config.data_source_label()
    lines = ["# Reporte de calidad de datos (capa silver)", "",
             f"Generado por `src/pipeline/quality.py` con `src/pipeline/contracts/silver.yml` "
             f"({df['run_at'].iloc[0]:%Y-%m-%d %H:%M} UTC).", ""]
    if source == "synthetic_replica":
        lines += [f"> **Datos: réplica sintética** (`src/etl/synth_dataset.py`, escala x{scale:.4f} de los clientes). "
                  "Los conteos no son los del dataset de los organizadores; los contratos de filas mínimas se "
                  "escalaron. Con la llave S3 (`make data`) el mismo reporte se genera sobre los datos reales.", ""]
    lines += [f"- Checks: {s['checks']} · PASS: {s['pass']} · WARN (defectos conocidos): {s['warn']} "
              f"· FAIL (rompen contrato): {s['fail']}", "",
              "## Checks con problemas", "",
              "| Tabla | Check | Columna | Severidad | Filas afectadas | % | Estado | Detalle |",
              "|---|---|---|---|---|---|---|---|"]
    bad = df[df.status != "PASS"].sort_values(["status", "table_name", "failed_pct"], ascending=[True, True, False])
    for r in bad.itertuples():
        lines.append(f"| {r.table_name} | {r.check_name} | {r.column_name} | {r.severity} | {r.failed_rows:,} | "
                     f"{r.failed_pct} | {r.status} | {r.description} |")
    lines += ["", "## Checks que pasan (por tabla)", ""]
    for table, g in df[df.status == "PASS"].groupby("table_name"):
        lines.append(f"- **{table}**: " + ", ".join(sorted(set(f"{c}({col})" if col else c
                                                                for c, col in zip(g.check_name, g.column_name)))))
    config.DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DATA_DIR / "dq_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
