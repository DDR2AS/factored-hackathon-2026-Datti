import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
import duckdb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from config.app import Config

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def download_single_file(s3_client, bucket, key, local_file_path):
    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
    if not os.path.exists(local_file_path):
        s3_client.download_file(bucket, key, local_file_path)
    return key

def download_s3_prefix(s3_client, bucket, s3_prefix, local_dir, max_workers=20):
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket, Prefix=s3_prefix)
    
    tasks = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for page in pages:
            if 'Contents' not in page:
                continue
            for item in page['Contents']:
                key = item['Key']
                if key.endswith('/'):
                    continue
                # Mapear ruta S3 a ruta local relativa
                rel_path = os.path.relpath(key, 'data')
                local_path = os.path.join(local_dir, rel_path)
                tasks.append(executor.submit(download_single_file, s3_client, bucket, key, local_path))
                
        completed = 0
        total = len(tasks)
        for _ in as_completed(tasks):
            completed += 1
            if completed % 200 == 0 or completed == total:
                print(f"  [DESCARGA S3] Progreso: {completed}/{total} archivos descritos...")

def run_ingestion():
    print("=" * 60)
    print("[ETL] INICIANDO: Descarga Multithreaded S3 -> Local Raw -> DuckDB")
    print("=" * 60)
    
    config = Config()
    bucket = config.awss3.bucket
    region = config.awss3.region
    access_key = config.awss3.access_key_id
    secret_key = config.awss3.secret_access_key

    if not bucket or not access_key or not secret_key:
        raise ValueError("[ERROR] Faltan credenciales AWS S3 en el archivo .env")

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    raw_dir = os.path.join(base_dir, "data", "raw")
    processed_dir = os.path.join(base_dir, "data", "processed")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)
    
    db_path = os.path.join(processed_dir, "latam_bank.duckdb")
    print(f"[INFO] Carpeta local raw: {raw_dir}")
    print(f"[INFO] Base de datos DuckDB: {db_path}")

    # 1. Crear cliente S3 con boto3
    s3_client = boto3.client(
        's3',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key
    )

    # 2. Descargar archivos desde S3
    print("\n--- PASO 1: Descargando archivos CSV desde S3 (Multi-threaded) ---")
    start_dl = time.time()
    download_s3_prefix(s3_client, bucket, "data/", raw_dir, max_workers=25)
    dl_elapsed = round(time.time() - start_dl, 2)
    print(f"[OK] Descarga completada en {dl_elapsed}s")

    # 3. Conectar a DuckDB y construir las tablas
    print("\n--- PASO 2: Construyendo tablas en DuckDB Local ---")
    con = duckdb.connect(db_path)

    # Tablas simples (CSV en raíz de data/raw)
    flat_tables = {
        "branches": os.path.join(raw_dir, "branches.csv"),
        "customers": os.path.join(raw_dir, "customers.csv"),
        "daily_exchange_rates": os.path.join(raw_dir, "daily_exchange_rates.csv"),
        "marketing_campaigns": os.path.join(raw_dir, "marketing_campaigns.csv"),
        "products": os.path.join(raw_dir, "products.csv"),
        "service_agents": os.path.join(raw_dir, "service_agents.csv")
    }

    for table_name, csv_path in flat_tables.items():
        if os.path.exists(csv_path):
            start = time.time()
            print(f"[DUCKDB] Procesando {table_name}...")
            con.execute(f"DROP TABLE IF EXISTS {table_name};")
            con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_csv_auto('{csv_path.replace(os.sep, '/')}');")
            cnt = con.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()[0]
            print(f"[OK] {table_name}: {cnt:,} filas ({round(time.time() - start, 2)}s)")
        else:
            print(f"[WARN] No se encontro {csv_path}")

    # Tablas particionadas
    partitioned_tables = [
        "call_center_interactions",
        "call_transcripts",
        "campaign_sends",
        "complaints",
        "satisfaction_surveys",
        "transactions",
        "digital_events"
    ]

    for table_name in partitioned_tables:
        table_dir = os.path.join(raw_dir, table_name)
        if os.path.exists(table_dir):
            start = time.time()
            print(f"[DUCKDB] Procesando carpeta {table_name}...")
            glob_path = os.path.join(table_dir, "**", "*.csv").replace(os.sep, '/')
            con.execute(f"DROP TABLE IF EXISTS {table_name};")
            con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_csv_auto('{glob_path}', union_by_name=True);")
            cnt = con.execute(f"SELECT COUNT(*) FROM {table_name};").fetchone()[0]
            print(f"[OK] {table_name}: {cnt:,} filas ({round(time.time() - start, 2)}s)")
        else:
            print(f"[WARN] No se encontro la carpeta {table_dir}")

    # 4. Resumen
    print("\n" + "=" * 60)
    print("[ETL] PROCESO COMPLETADO CON EXITO")
    print("=" * 60)
    tables = con.execute("SHOW TABLES;").fetchall()
    print("Tablas en DuckDB local:")
    for t in tables:
        t_name = t[0]
        cnt = con.execute(f"SELECT COUNT(*) FROM {t_name};").fetchone()[0]
        print(f"  * {t_name:<30} | {cnt:>12,} filas")

    con.close()
    db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
    print(f"\n[INFO] Tamano final BD local DuckDB: {db_size_mb} MB")

if __name__ == "__main__":
    run_ingestion()
