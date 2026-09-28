# factored-hackathon-2026-Datti

Repo para el proyecto **Factored AI & Data Hackathon 2026**.

## 🛠️ Librerías e Importaciones del Proyecto (Dependencies)

Las principales librerías utilizadas en este proyecto y sus propósitos:

| Librería | Versión | Propósito / Uso |
| :--- | :--- | :--- |
| **`python-dotenv`** | `1.0.1` | Carga de variables de entorno desde archivos `.env` (credenciales AWS S3, llaves de API, etc.). |
| **`boto3`** | Latest | SDK oficial de AWS para Python. Permite la exploración e interacción directa con el bucket de S3 (`factored-datathon-2026-s3-157725502942-us-east-2-an`). |
| **`duckdb`** | Latest | Motor SQL OLAP analítico in-memory/on-disk de ultra-alta velocidad. Utilizado para procesar los ~19M de registros del dataset en local/Parquet sin latencia. |
| **`pandas`** | Latest | Manipulación y estructuración de dataframes en Python para análisis de datos y preparación de baselines. |
| **`pyarrow`** | Latest | Soporte para el formato de almacenamiento en columnas Parquet y conversión eficiente entre Arrow, Pandas y DuckDB. |

---

##  Instalación de Dependencias

Para instalar todas las librerías necesarias en tu entorno virtual (`.venv`):

```bash
pip install -r requirements.txt
```

---

##  Extracción de Data Raw (S3 → Local → DuckDB)
El script [`src/etl/ingest_s3_duckdb.py`](src/etl/ingest_s3_duckdb.py) descarga todos los CSV crudos del bucket S3 (prefijo `data/`) a `data/raw/` y los carga como tablas en la base local `data/processed/latam_bank.duckdb`.

Requiere las credenciales AWS S3 en el archivo `.env`. Desde la raíz del repo:

```bash
python src/etl/ingest_s3_duckdb.py
```

> Los archivos que ya existen en `data/raw/` no se vuelven a descargar.

---

## Cómo trabajamos (leer primero)

| Archivo | Para qué |
| :--- | :--- |
| [`RUNBOOK.md`](RUNBOOK.md) | Contexto operativo: fechas, reglas, desarrollo local, modelo de coordinación. **Empieza aquí.** |
| [`docs/plan/expediente-vivo-v2.html`](docs/plan/expediente-vivo-v2.html) | Plan completo v2 con diagramas, decisiones, costos y cronograma (abrir en el navegador). |
| [`DECISIONS.md`](DECISIONS.md) | Decisiones D1–D11, append-only. |
| [`INTERFACES.md`](INTERFACES.md) | Contratos entre dueños (API, gateway, caso, modelos, tablas gold, reporte del investigador, trazas, cliente LLM). |
| [`STATUS.md`](STATUS.md) | Reclamar trabajo antes de construirlo. |
| [`docs/architecture.md`](docs/architecture.md) | Arquitectura en Mermaid (flujo, local, nube). |
| [`AGENTS.md`](AGENTS.md) / `.agents/skills/` | Instrucciones para agentes de código (Claude Code, Codex, Cursor, etc.). |

Andrés administra la cuenta de AWS. Por ahora el resto del equipo no tiene acceso: desarrollamos y probamos en local (DuckDB + proveedor LLM `mock`) contra `INTERFACES.md`. Cuando haya que desplegar, se pide un usuario IAM a Andrés y todo cambio en AWS se hace con CDK, nunca a mano en la consola.

## Componentes preexistentes

Los archivos de coordinación (`RUNBOOK.md`, `STATUS.md`, `DECISIONS.md`, `INTERFACES.md`, `AGENTS.md`, `CLAUDE.md`), las skills de agentes en `.agents/skills/`, el contrato del `Makefile`, el workflow de CI, `.gitattributes`, `scripts/licences.py` y las plantillas en `templates/` se adaptaron del repositorio de preparación previo del equipo, `nextwave-kit`. Se copiaron archivos; no se comparte historial de git.
