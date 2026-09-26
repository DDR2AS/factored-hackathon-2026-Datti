import os
import sys
import duckdb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def run_validation():
    print("=" * 60)
    print("[DATA QUALITY] Ejecutando validacion de esquemas y contratos")
    print("=" * 60)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    db_path = os.path.join(base_dir, "data", "processed", "latam_bank.duckdb")

    if not os.path.exists(db_path):
        print(f"[ERROR] No existe la base de datos en {db_path}")
        return

    con = duckdb.connect(db_path, read_only=True)

    expected_tables = [
        "customers", "products", "branches", "service_agents", "marketing_campaigns",
        "transactions", "call_center_interactions", "call_transcripts",
        "satisfaction_surveys", "digital_events", "complaints", "campaign_sends",
        "daily_exchange_rates"
    ]

    print("\n1. Verificacion de existencia de tablas:")
    existing_tables = [t[0] for t in con.execute("SHOW TABLES;").fetchall()]
    for tbl in expected_tables:
        status = "[OK]" if tbl in existing_tables else "[MISSING]"
        print(f"  {status} {tbl}")

    print("\n2. Conteo de filas y metricas de calidad basica:")
    for tbl in existing_tables:
        cnt = con.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]
        print(f"  • Tabla {tbl:<30} : {cnt:>12,} filas")

    print("\n3. Verificacion de integridad de llaves primarias (customers, products, branches):")
    for tbl, pk in [("customers", "customer_id"), ("products", "product_id"), ("branches", "branch_id")]:
        if tbl in existing_tables:
            total = con.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]
            distinct = con.execute(f"SELECT COUNT(DISTINCT {pk}) FROM {tbl};").fetchone()[0]
            dups = total - distinct
            dup_pct = round((dups / total) * 100, 2) if total > 0 else 0
            print(f"  • {tbl}.{pk} -> Duplicados: {dups:,} ({dup_pct}%)")

    con.close()
    print("\n" + "=" * 60)
    print("[DATA QUALITY] Validacion preliminar finalizada.")
    print("=" * 60)

if __name__ == "__main__":
    run_validation()
