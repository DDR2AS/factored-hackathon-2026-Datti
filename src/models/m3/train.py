"""M3 lift experiment (plan v2 section 5, ``make train``): does behaviour add anything to fraud_score?

Population: transactions where the rule does NOT fire (fraud_score <= 30 or no score): the rule
already catches the rest with 100% precision. Point-in-time features from the customer's own
history (only earlier transactions and sessions), time split (train < 2025-10-01 <= test).
LightGBM on is_fraud vs fraud_score alone, compared on PR-AUC and on precision at a fixed alert
budget (0.5% of the transactions), with bootstrap 95% intervals.

Decision rule (written to thresholds.yaml, key m3): lift is significant only if the lower bound of
the PR-AUC difference is above 0 AND precision at the budget is at least twice the base rate. If not
(expected from the v1.4 analysis: 44 variables recovered nothing), M3 stays evidence only and the
negative result is reported.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import yaml

from models.common import write_threshold_section

VERSION = "m3-evidence-1"
REPO_ROOT = Path(__file__).resolve().parents[3]
GOLD_DIR = REPO_ROOT / "data" / "gold"
REPORT_DIR = REPO_ROOT / "data" / "eval"
DOC = REPO_ROOT / "docs" / "modelos" / "m3.md"
SPLIT = date(2025, 10, 1)
ALERT_BUDGET = 0.005
FEATURES = ("amount_usd", "amount_vs_median", "amount_vs_cat_median", "first_time_merchant", "hour",
            "hour_gap", "velocity_24h", "amount_24h_usd", "foreign", "login_before_30m", "new_device_before",
            "channel_pos", "channel_web", "channel_app", "channel_atm", "is_purchase", "days_since_prev")


def build_frame(gold_dir: Path = GOLD_DIR):
    g = lambda t: (gold_dir / t / "data.parquet").as_posix()  # noqa: E731
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{g('transactions_enriched')}')")
    sess = gold_dir / "digital_sessions" / "data.parquet"
    if sess.exists():
        con.execute(f"CREATE TABLE s AS SELECT customer_id, started_at, has_login, first_seen_device FROM read_parquet('{sess.as_posix()}')")
    else:
        con.execute("CREATE TABLE s (customer_id VARCHAR, started_at TIMESTAMP, has_login BOOLEAN, first_seen_device BOOLEAN)")
    df = con.execute("""
        WITH x AS (
            SELECT t.*, CAST(COALESCE(amount_usd, CASE WHEN currency = 'USD' THEN amount END) AS DOUBLE) AS a_usd,
                   hour(ts) AS hh
            FROM t WHERE txn_type IN ('purchase', 'payment', 'withdrawal', 'transfer')
        ),
        w AS (
            SELECT x.*,
                median(a_usd) OVER (PARTITION BY customer_id ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_median,
                median(a_usd) OVER (PARTITION BY customer_id, merchant_category ORDER BY ts
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_cat_median,
                median(hh) OVER (PARTITION BY customer_id ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_hour,
                count(*) OVER (PARTITION BY customer_id, merchant_id ORDER BY ts
                               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_same_merchant,
                count(*) OVER (PARTITION BY customer_id ORDER BY ts RANGE BETWEEN INTERVAL 24 HOUR PRECEDING AND INTERVAL 1 SECOND PRECEDING) AS velocity_24h,
                sum(a_usd) OVER (PARTITION BY customer_id ORDER BY ts RANGE BETWEEN INTERVAL 24 HOUR PRECEDING AND INTERVAL 1 SECOND PRECEDING) AS amount_24h_usd,
                lag(ts) OVER (PARTITION BY customer_id ORDER BY ts) AS prev_ts
            FROM x
        ),
        ss AS (
            SELECT w.txn_id,
                   bool_or(s.has_login) AS login_before_30m,
                   bool_or(s.first_seen_device) AS new_device_before
            FROM w JOIN s ON s.customer_id = w.customer_id
                         AND s.started_at BETWEEN w.ts - INTERVAL 30 MINUTE AND w.ts
            GROUP BY 1
        )
        SELECT w.txn_id, w.local_date, w.is_fraud, w.fraud_score,
               w.a_usd AS amount_usd,
               w.a_usd / NULLIF(w.prev_median, 0) AS amount_vs_median,
               w.a_usd / NULLIF(w.prev_cat_median, 0) AS amount_vs_cat_median,
               CASE WHEN w.merchant_id LIKE 'M-%' THEN (w.prev_same_merchant = 0)::INT END AS first_time_merchant,
               w.hh AS hour, abs(w.hh - w.prev_hour) AS hour_gap,
               COALESCE(w.velocity_24h, 0) AS velocity_24h, COALESCE(w.amount_24h_usd, 0) AS amount_24h_usd,
               w.is_foreign::INT AS foreign,
               COALESCE(ss.login_before_30m, FALSE)::INT AS login_before_30m,
               COALESCE(ss.new_device_before, FALSE)::INT AS new_device_before,
               (w.channel = 'POS')::INT AS channel_pos, (w.channel = 'Web')::INT AS channel_web,
               (w.channel = 'App')::INT AS channel_app, (w.channel = 'ATM')::INT AS channel_atm,
               (w.txn_type = 'purchase')::INT AS is_purchase,
               date_diff('hour', w.prev_ts, w.ts) / 24.0 AS days_since_prev
        FROM w LEFT JOIN ss USING (txn_id)
        WHERE w.fraud_score IS NULL OR w.fraud_score <= 30
    """).df()
    rule_stats = con.execute("""
        SELECT count(*) FILTER (WHERE is_fraud) AS frauds,
               count(*) FILTER (WHERE is_fraud AND fraud_score > 30) AS caught,
               count(*) FILTER (WHERE fraud_score > 30) AS flagged,
               count(*) FILTER (WHERE fraud_score > 30 AND is_fraud) AS flagged_fraud
        FROM t
    """).fetchone()
    con.close()
    return df, dict(zip(("frauds", "caught", "flagged", "flagged_fraud"), rule_stats))


def pr_auc(y: np.ndarray, s: np.ndarray) -> float:
    from sklearn.metrics import average_precision_score

    return float(average_precision_score(y, s)) if y.sum() else float("nan")


def precision_at(y: np.ndarray, s: np.ndarray, budget: float) -> float:
    k = max(1, int(round(budget * len(y))))
    idx = np.argsort(-s, kind="stable")[:k]
    return float(y[idx].mean())


def train(gold_dir: Path = GOLD_DIR, write: bool = True, verbose: bool = True, n_boot: int = 500) -> dict:
    import lightgbm as lgb

    t0 = time.time()
    log = (lambda m: print(f"[m3 {time.time() - t0:5.1f}s] {m}", flush=True)) if verbose else (lambda m: None)
    df, rule = build_frame(gold_dir)
    meta_file = gold_dir / "_meta.yaml"
    data_meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    df["is_fraud"] = df["is_fraud"].astype(int)
    df["local_date"] = df["local_date"].astype("datetime64[us]")
    split_ts = np.datetime64(SPLIT.isoformat())
    train_df = df[df["local_date"] < split_ts]
    test_df = df[df["local_date"] >= split_ts]
    X_tr, y_tr = train_df[list(FEATURES)].astype(float).to_numpy(), train_df["is_fraud"].to_numpy()
    X_te, y_te = test_df[list(FEATURES)].astype(float).to_numpy(), test_df["is_fraud"].to_numpy()
    log(f"population (rule does not fire): {len(df):,} txns, {int(df.is_fraud.sum())} frauds; "
        f"train {len(y_tr):,} ({int(y_tr.sum())}), test {len(y_te):,} ({int(y_te.sum())})")
    clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=40,
                             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=11, verbose=-1,
                             scale_pos_weight=max(1.0, (len(y_tr) - y_tr.sum()) / max(1, y_tr.sum()) / 10))
    clf.fit(X_tr, y_tr)
    s_model = clf.predict_proba(X_te)[:, 1]
    s_score = test_df["fraud_score"].fillna(-1).to_numpy(dtype=float)
    base_rate = float(y_te.mean()) if len(y_te) else float("nan")
    res = {"pr_auc_model": pr_auc(y_te, s_model), "pr_auc_fraud_score": pr_auc(y_te, s_score),
           "precision_at_budget_model": precision_at(y_te, s_model, ALERT_BUDGET),
           "precision_at_budget_fraud_score": precision_at(y_te, s_score, ALERT_BUDGET), "base_rate": base_rate}
    rng = np.random.default_rng(5)
    diffs, p_model = [], []
    for _ in range(n_boot):
        idx = rng.integers(0, len(y_te), len(y_te))
        yy = y_te[idx]
        if yy.sum() == 0:
            continue
        diffs.append(pr_auc(yy, s_model[idx]) - pr_auc(yy, s_score[idx]))
        p_model.append(precision_at(yy, s_model[idx], ALERT_BUDGET))
    ci = [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))] if diffs else [None, None]
    ci_p = [float(np.percentile(p_model, 2.5)), float(np.percentile(p_model, 97.5))] if p_model else [None, None]
    significant = bool(ci[0] is not None and ci[0] > 0 and res["precision_at_budget_model"] >= 2 * base_rate)
    importance = sorted(zip(FEATURES, clf.booster_.feature_importance("gain")), key=lambda kv: -kv[1])
    report = {
        "model_version": VERSION, "data_source": data_meta.get("data_source"), "seconds": round(time.time() - t0, 1),
        "population": "transactions where fraud_score <= 30 or null (the rule does not fire)",
        "split": {"train_before": SPLIT.isoformat(), "test_from": SPLIT.isoformat()},
        "sizes": {"train": int(len(y_tr)), "train_frauds": int(y_tr.sum()), "test": int(len(y_te)), "test_frauds": int(y_te.sum())},
        "rule_fraud_score_gt_30": {"frauds_total": int(rule["frauds"]), "caught": int(rule["caught"]),
                                   "recall": round(rule["caught"] / max(1, rule["frauds"]), 4),
                                   "precision": round(rule["flagged_fraud"] / max(1, rule["flagged"]), 4)},
        "test": {k: (round(v, 5) if isinstance(v, float) else v) for k, v in res.items()},
        "pr_auc_diff_ci95": [round(v, 5) if v is not None else None for v in ci],
        "precision_at_budget_model_ci95": [round(v, 5) if v is not None else None for v in ci_p],
        "alert_budget": ALERT_BUDGET, "lift_significant": significant,
        "decision": "lane_c_trigger_candidate" if significant else "evidence_only",
        "feature_importance_gain": {k: round(float(v), 1) for k, v in importance},
    }
    log(f"PR-AUC model {res['pr_auc_model']:.4f} vs fraud_score {res['pr_auc_fraud_score']:.4f} "
        f"(base rate {base_rate:.4f}); diff CI95 {report['pr_auc_diff_ci95']} -> "
        f"{'LIFT' if significant else 'no lift: evidence only'}")
    if write:
        write_threshold_section("m3", {
            "model_version": VERSION, "lift_significant": significant, "decision": report["decision"],
            "alert_budget": ALERT_BUDGET, "data_source": data_meta.get("data_source"),
            "note": "evidence only unless lift_significant; the lane rules keep fraud_score > 30",
        })
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "m3_report.yaml").write_text(yaml.safe_dump(report, sort_keys=False, allow_unicode=True), encoding="utf-8")
        write_doc(report)
    return report


def write_doc(r: dict) -> None:
    t = r["test"]
    rr = r["rule_fraud_score_gt_30"]
    lines = [
        "# M3 · Evidencia de riesgo y experimento de lift", "",
        f"Generado por `make train` (`src/models/m3/train.py`), versión `{r['model_version']}`. Fuente de datos: "
        f"**{r['data_source']}**" + (" (réplica sintética: los números no son del dataset real)" if r["data_source"] == "synthetic_replica" else "") + ".", "",
        "## La regla que ya existe", "",
        f"`fraud_score > 30`: precisión {100 * rr['precision']:.1f}%, cubre {100 * rr['recall']:.1f}% de los "
        f"{rr['frauds_total']} fraudes. Se mantiene como regla de ruta C (`high_fraud_score`).", "",
        "## Experimento (un día, plan v2 §5)", "",
        f"- Población: {r['population']}.",
        f"- Partición temporal: entrenamiento antes de {r['split']['train_before']}, prueba desde {r['split']['test_from']}.",
        f"- Tamaños: entrenamiento {r['sizes']['train']:,} transacciones ({r['sizes']['train_frauds']} fraudes), "
        f"prueba {r['sizes']['test']:,} ({r['sizes']['test_frauds']} fraudes).",
        "- Variables (siempre con información anterior a la transacción): monto en USD, monto frente a la mediana del "
        "cliente y de la categoría, primera vez en el comercio, hora y distancia a su hora habitual, velocidad en 24 h, "
        "país distinto, inicio de sesión o dispositivo nuevo en los 30 min previos, canal y tipo.", "",
        "| Métrica (prueba) | LightGBM con comportamiento | fraud_score solo |", "|---|---|---|",
        f"| PR-AUC | {t['pr_auc_model']:.4f} | {t['pr_auc_fraud_score']:.4f} |",
        f"| Precisión con presupuesto de alertas de {100 * r['alert_budget']:.1f}% | {t['precision_at_budget_model']:.4f} "
        f"{r['precision_at_budget_model_ci95']} | {t['precision_at_budget_fraud_score']:.4f} |",
        f"| Tasa base de fraude | {t['base_rate']:.4f} | |", "",
        f"Diferencia de PR-AUC, IC 95% bootstrap: {r['pr_auc_diff_ci95']}.", "",
        f"**Resultado: {'hay lift significativo' if r['lift_significant'] else 'no hay lift significativo'}.** "
        + ("M3 podría agregarse como disparador de ruta C con ese presupuesto de alertas (requiere una decisión del equipo)."
           if r["lift_significant"] else
           "M3 queda como evidencia para el investigador y el analista (\"8 veces tu monto habitual, primera vez en "
           "este comercio\"), no como disparador. Es el resultado esperado por el análisis v1.4: el fraude fuera de la "
           "regla es un sorteo independiente de todo lo observable."), "",
        "Limitaciones: en la réplica sintética la tasa de fraude está escalada (0,4% frente a 0,1% real) para tener "
        "casos suficientes; repetir con los datos reales antes de citar los números.",
    ]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    train()
