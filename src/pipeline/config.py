"""Paths, parameters and documented assumptions of the pipeline.

Everything here comes from the v1.4 analysis of the real data (docs in Arturo-GA/Hackathon-Factored:
docs/hallazgos.md, docs/tratamiento_datos.md). ``DATA_DIR`` can be moved with the environment
variable of the same name (a Lambda uses /tmp/data).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR") or (REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"
QUALITY_DIR = DATA_DIR / "quality"
SERVING_DIR = DATA_DIR / "serving"
LABELS_DIR = DATA_DIR / "labels"
WAREHOUSE = DATA_DIR / "processed" / "warehouse.duckdb"
SQL_DIR = PACKAGE_DIR / "sql"
CONTRACTS = PACKAGE_DIR / "contracts" / "silver.yml"
REPLICA_MARKER = "_SYNTHETIC_REPLICA.txt"


def _docs_dir(data_dir: Path) -> Path:
    """Generated reports (dq_report.md, gold_tables.md) go to docs/data/ only for the repo's own data
    folder; any other folder (tests, a Lambda's /tmp) keeps them under <data_dir>/docs."""
    try:
        own = Path(data_dir).resolve() == (REPO_ROOT / "data").resolve()
    except OSError:
        own = False
    return REPO_ROOT / "docs" / "data" if own else Path(data_dir) / "docs"


DOCS_DATA_DIR = _docs_dir(DATA_DIR)

# Dimension tables come as one CSV; daily tables as year=/month=/day= folders (process_date).
DIM_TABLES = ("daily_exchange_rates", "branches", "service_agents", "customers", "products", "marketing_campaigns")
DAILY_TABLES = ("transactions", "call_center_interactions", "call_transcripts", "satisfaction_surveys", "complaints",
                "campaign_sends", "digital_events")
RAW_TABLES = DIM_TABLES + DAILY_TABLES

# Silver build order: dimensions before the facts that join them. (silver name, raw/bronze name)
SILVER_ORDER = (
    ("exchange_rates", "daily_exchange_rates"),
    ("branches", "branches"),
    ("service_agents", "service_agents"),
    ("customers", "customers"),
    ("products", "products"),
    ("marketing_campaigns", "marketing_campaigns"),
    ("transactions", "transactions"),
    ("call_center_interactions", "call_center_interactions"),
    ("call_transcripts", "call_transcripts"),
    ("satisfaction_surveys", "satisfaction_surveys"),
    ("complaints", "complaints"),
    ("campaign_sends", "campaign_sends"),
    ("digital_events", "digital_events"),
)
SILVER_DAILY = {s for s, b in SILVER_ORDER if b in DAILY_TABLES}

# Gold build order (INTERFACES.md #5 + plan v2 section 8; docs/data/gold_tables.md).
GOLD_ORDER = (
    "customer_products", "customer_profile", "transactions_enriched", "fee_schedule", "fee_charges",
    "customer_baseline", "customer_category_baseline", "merchant_stats", "complaints_baseline",
    "contact_history", "digital_sessions", "kpi_baseline", "cc_category_baseline", "labels",
)

# Domain parameters (evidence in docs/hallazgos.md of the v1.4 repo).
DEFAULT_SNAPSHOT = date(2026, 6, 17)  # last process_date of the dataset: "today" for windows
# amount_usd of the dataset = amount / FIXED rate (not the daily fx table). MXN never appears in
# transactions; its rate (~17) is only used for incomes and complaint amounts.
USD_RATES = {"USD": 1.0, "COP": 4000.0, "ARS": 350.0, "MXN": 17.0}
FRAUD_SCORE_HIGH = 30  # fraud_score > 30 => fraud in 100% of cases; <= 30 carries no information
AGENT_COST_PER_MINUTE_USD = float(os.environ.get("AGENT_COST_PER_MINUTE_USD", "0.15"))  # business ASSUMPTION
BASELINE_GAP_DAYS = 14   # customer_baseline ignores the last 14 days (the disputed charges must not define "normal")
BASELINE_WINDOW_DAYS = 365
FEE_MONTHS = 6           # synthetic monthly fee charges kept per product
CONTACT_WINDOW_DAYS = 365  # contact_history window used by the gateway for repeat complaints

DUCKDB_MEMORY = os.environ.get("DUCKDB_MEMORY", "2GB")
DUCKDB_THREADS = int(os.environ.get("DUCKDB_THREADS", "4"))


def set_data_dir(path: Path | str) -> None:
    """Point every layer at another data folder (tests, a Lambda's /tmp)."""
    global DATA_DIR, RAW_DIR, BRONZE_DIR, SILVER_DIR, GOLD_DIR, QUALITY_DIR, SERVING_DIR, LABELS_DIR, WAREHOUSE
    global DOCS_DATA_DIR
    DATA_DIR = Path(path)
    DOCS_DATA_DIR = _docs_dir(DATA_DIR)
    RAW_DIR = DATA_DIR / "raw"
    BRONZE_DIR = DATA_DIR / "bronze"
    SILVER_DIR = DATA_DIR / "silver"
    GOLD_DIR = DATA_DIR / "gold"
    QUALITY_DIR = DATA_DIR / "quality"
    SERVING_DIR = DATA_DIR / "serving"
    LABELS_DIR = DATA_DIR / "labels"
    WAREHOUSE = DATA_DIR / "processed" / "warehouse.duckdb"


def sql_params(snapshot: date) -> dict[str, object]:
    return {
        "snapshot_date": snapshot.isoformat(),
        "usd_rate_COP": USD_RATES["COP"],
        "usd_rate_ARS": USD_RATES["ARS"],
        "usd_rate_MXN": USD_RATES["MXN"],
        "fraud_score_high": FRAUD_SCORE_HIGH,
        "agent_cost_per_minute_usd": AGENT_COST_PER_MINUTE_USD,
        "baseline_gap_days": BASELINE_GAP_DAYS,
        "baseline_window_days": BASELINE_WINDOW_DAYS,
        "fee_months": FEE_MONTHS,
        "contact_window_days": CONTACT_WINDOW_DAYS,
    }


def is_replica(raw_dir: Path | None = None) -> bool:
    """True when data/raw holds the synthetic replica (src/etl/synth_dataset.py), not the organizers' data."""
    return ((raw_dir or RAW_DIR) / REPLICA_MARKER).exists()


def replica_scale(raw_dir: Path | None = None) -> float:
    """customers in the replica / 150,000 (the real dataset); 1.0 for the real data."""
    marker = (raw_dir or RAW_DIR) / REPLICA_MARKER
    if not marker.exists():
        return 1.0
    for line in marker.read_text(encoding="utf-8").splitlines():
        if line.startswith("customers:"):
            try:
                return max(1e-4, int(line.split(":", 1)[1]) / 150_000)
            except ValueError:
                break
    return 1.0


def data_source_label(raw_dir: Path | None = None) -> str:
    return "synthetic_replica" if is_replica(raw_dir) else "organizers_dataset"
