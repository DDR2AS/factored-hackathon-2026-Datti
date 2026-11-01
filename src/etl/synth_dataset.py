"""Synthetic replica of the LATAM Bank dataset (the organizers' 13 tables). SYNTHETIC DATA.

Why it exists
-------------
The real dataset lives in the organizers' S3 bucket and needs the read-only key printed in the
data dictionary PDF (never in git). When that key is not available, ``make data`` builds this
replica so every stage of Expediente Vivo (pipeline, gold tables, models, planted-scenario
evaluation, demo) runs end to end on a laptop. With the key, ``src/etl/ingest_s3_duckdb.py``
downloads the real CSVs to the same place and every later step runs unchanged.

What it reproduces
------------------
Same 13 tables, same column names and CSV layout as the bucket: ``data/raw/<table>.csv`` for
dimension tables and ``data/raw/<table>/year=YYYY/month=MM/day=DD/*.csv`` (one folder per
``process_date``) for daily tables; UTF-8 with BOM, booleans as ``True``/``False``, empty field =
null. The generator follows the rules the v1.4 analysis found in the real data (Arturo's repo
``Arturo-GA/Hackathon-Factored``, ``docs/hallazgos.md`` and ``docs/data_profile.md``):

- ``process_date`` = date(ts - 6 h) for transactions and digital events, date(ts - 8 h) for
  call center and complaints. Three years: 2023-06-17 .. 2026-06-17.
- Each ACTIVE product gets Poisson(13) transactions over the three years (other products 0),
  dates independent of the product's opening date (so ~19% fall before it, a known defect).
- Transaction mix fixed by product type; amounts uniform per transaction type in USD terms;
  currency is an attribute of the product (Mexico 100% USD; CO and AR ~90% local);
  ``amount_usd`` = amount / FIXED rate (COP 4000, ARS 350), null for USD and 5% at random.
- Status and response code independent of everything; ``00`` <=> Approved.
- Fraud is an independent draw. ``fraud_score > 30`` => fraud 100% of the time and covers 55%
  of frauds; the other 45% carry no signal. SCALED: the fraud rate is 0.4% here (0.1% in the
  real data) so that a ~1/40 sample still has a few hundred frauds for M3's experiment.
- 24 merchants, each with one fixed category; geography is noise (lat/lon = country centre
  +-1 deg, Mexico at (0, 0)).
- Call center: weekdays equal, weekends exactly half, daily noise U(0.8, 1.2); first-contact
  resolution depends only on the contact reason; CSAT/CES = 2 + resolved + noise; NPS draws
  2-4 or 5-7; transcripts are templates.
- Complaints: five categories ~20% each; ``affected_product_id`` is a random product of the
  whole bank (another customer's in practice); ``is_repeat_complainer`` is a 15% coin flip;
  resolution 1..30 days (median 16), first response median ~38 h.
- Known quality defects: Mexican customers with +54 phones and "DNI" documents, shared emails,
  broken registration-branch keys, 6% ``last_updated`` in the future, "Mexico" without accent,
  unfilled ``{monto} {moneda}`` in transcripts, "Oferta especial en nan!" subjects.

Scale is ~1/40 of the customers by default (``--customers 4000``). Every number computed on the
replica must be labelled as synthetic; rerun with the real data for real numbers. A marker file
``data/raw/_SYNTHETIC_REPLICA.txt`` tells the pipeline and the reports which data they used.

Usage (repo root)::

    python src/etl/synth_dataset.py                    # data/raw + data/processed/latam_bank.duckdb
    python src/etl/synth_dataset.py --customers 1500   # smaller, faster
    python src/etl/synth_dataset.py --no-load          # only the CSV files
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
MARKER = "_SYNTHETIC_REPLICA.txt"
GENERATOR_VERSION = "replica-2026-10-29.1"

START = date(2023, 6, 17)
END = date(2026, 6, 17)  # inclusive; the dataset cut-off
N_DAYS = (END - START).days + 1
FIXED_RATE = {"USD": 1.0, "COP": 4000.0, "ARS": 350.0, "MXN": 17.0}
FRAUD_RATE = 0.004  # scaled (real: 0.1%), see module docstring

COUNTRIES = ["México", "Colombia", "Argentina"]
COUNTRY_P = [0.50, 0.30, 0.20]
COUNTRY_ISO = {"México": "MX", "Colombia": "CO", "Argentina": "AR"}
ACCENT = {"México": "mexican", "Colombia": "colombian", "Argentina": "argentine"}
LOCAL_CCY = {"México": "USD", "Colombia": "COP", "Argentina": "ARS"}  # Mexico: 100% USD (dataset rule)
INCOME_CCY = {"México": "MXN", "Colombia": "COP", "Argentina": "ARS"}
CENTER = {"México": (0.0, 0.0), "Colombia": (4.6, -74.1), "Argentina": (-34.6, -58.4)}  # Mexico at (0,0): dataset quirk
CITIES = {
    "México": [("Ciudad de México", "Ciudad de México", "01000"), ("Guadalajara", "Jalisco", "44100"),
               ("Monterrey", "Nuevo León", "64000"), ("Puebla", "Puebla", "72000"),
               ("Tijuana", "Baja California", "22000"), ("Querétaro", "Querétaro", "76000")],
    "Colombia": [("Bogotá", "Cundinamarca", "110111"), ("Medellín", "Antioquia", "050001"),
                 ("Cali", "Valle del Cauca", "760001"), ("Barranquilla", "Atlántico", "080001"),
                 ("Cartagena", "Bolívar", "130001")],
    "Argentina": [("Buenos Aires", "Buenos Aires", "C1000"), ("Córdoba", "Córdoba", "X5000"),
                  ("Rosario", "Santa Fe", "S2000"), ("Mendoza", "Mendoza", "M5500"),
                  ("La Plata", "Buenos Aires", "B1900")],
}

FIRST_F = ["María", "Lucía", "Sofía", "Valentina", "Camila", "Daniela", "Gabriela", "Paula", "Carolina",
           "Andrea", "Mariana", "Natalia", "Fernanda", "Isabella", "Julieta", "Martina", "Florencia",
           "Ana", "Laura", "Victoria", "Renata", "Ximena", "Paola", "Lorena"]
FIRST_M = ["Juan", "Carlos", "Luis", "José", "Diego", "Andrés", "Santiago", "Mateo", "Sebastián",
           "Alejandro", "Miguel", "Javier", "Fernando", "Ricardo", "Tomás", "Nicolás", "Martín",
           "Gabriel", "Felipe", "Joaquín", "Emiliano", "Rodrigo", "Pablo", "Hernán"]
LAST = ["García", "Rodríguez", "Martínez", "Hernández", "López", "González", "Pérez", "Sánchez",
        "Ramírez", "Torres", "Flores", "Rivera", "Gómez", "Díaz", "Cruz", "Morales", "Reyes",
        "Gutiérrez", "Ortiz", "Castro", "Vargas", "Romero", "Herrera", "Medina", "Aguilar", "Silva",
        "Rojas", "Moreno", "Jiménez", "Álvarez", "Ruiz", "Mendoza", "Castillo", "Vega", "Ríos"]
EMAIL_DOMAINS = ["gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "correo.com", "mail.com", "live.com"]

PRODUCT_TYPES = ["Cuenta Ahorro", "Tarjeta Crédito", "Cuenta Corriente", "Tarjeta Débito",
                 "Préstamo Personal", "Préstamo Hipotecario", "Inversión", "Seguro"]
PRODUCT_P = [0.3005, 0.2503, 0.2499, 0.0998, 0.0499, 0.0298, 0.0100, 0.0098]
CARDS = {"Tarjeta Crédito", "Tarjeta Débito"}
CREDIT = {"Tarjeta Crédito", "Préstamo Personal", "Préstamo Hipotecario"}

TX_TYPES = ["Purchase", "Withdrawal", "Transfer", "Payment", "Deposit", "Adjustment"]
TX_MIX = {  # product type fixes the mix of transaction types (dataset rule)
    "Tarjeta Crédito": [0.72, 0.05, 0.00, 0.18, 0.00, 0.05],
    "Tarjeta Débito": [0.55, 0.35, 0.07, 0.00, 0.00, 0.03],
    "Cuenta Ahorro": [0.00, 0.35, 0.30, 0.00, 0.30, 0.05],
    "Cuenta Corriente": [0.05, 0.25, 0.35, 0.20, 0.12, 0.03],
    "Préstamo Personal": [0.00, 0.00, 0.00, 0.90, 0.00, 0.10],
    "Préstamo Hipotecario": [0.00, 0.00, 0.00, 0.90, 0.00, 0.10],
    "Inversión": [0.00, 0.30, 0.00, 0.00, 0.60, 0.10],
    "Seguro": [0.00, 0.00, 0.00, 0.90, 0.00, 0.10],
}
TX_USD_RANGE = {"Purchase": (5, 1000), "Withdrawal": (20, 1000), "Transfer": (10, 5000),
                "Payment": (20, 3000), "Deposit": (50, 9999), "Adjustment": (5, 500)}
TX_CHANNEL = {
    "Purchase": (["POS", "Web", "App"], [0.62, 0.19, 0.19]),
    "Withdrawal": (["ATM", "Branch"], [0.92, 0.08]),
    "Transfer": (["App", "Web", "Transfer", "Branch"], [0.40, 0.38, 0.14, 0.08]),
    "Payment": (["App", "Web", "POS", "Branch"], [0.38, 0.34, 0.20, 0.08]),
    "Deposit": (["ATM", "Branch", "Transfer"], [0.45, 0.30, 0.25]),
    "Adjustment": (["App", "Branch"], [0.6, 0.4]),
}
MERCHANTS = [  # 24 merchants, each with ONE category (dataset rule)
    ("Restaurante El Buen Sabor", "Food"), ("Super Ahorro", "Food"), ("Tienda Don José", "Food"),
    ("Mercado Central", "Food"), ("Panadería La Espiga", "Food"), ("Café Aroma", "Food"),
    ("Cable TV", "Services"), ("Empresa Telefónica", "Services"), ("Agua Potable SA", "Services"),
    ("Energía del Valle", "Services"), ("Internet Rápido", "Services"),
    ("Gasolinera El Camino", "Transport"), ("Taxi Seguro", "Transport"), ("Bus Express", "Transport"),
    ("Peaje Autopista", "Transport"),
    ("Cine Estrella", "Entertainment"), ("Streaming Plus", "Entertainment"), ("Teatro Nacional", "Entertainment"),
    ("Parque Diversiones", "Entertainment"),
    ("Farmacia Salud", "Health"), ("Clínica Central", "Health"), ("Óptica Visión", "Health"),
    ("Tienda Online Mega", "Other"), ("Ferretería El Martillo", "Other"),
]
MERCHANT_W = np.array([1.4, 1.4, 1.4, 1.4, 0.4, 0.4, 1.2, 1.2, 0.8, 0.8, 0.8,
                       1.0, 1.0, 0.8, 0.6, 1.0, 1.0, 0.7, 0.7, 1.0, 0.7, 0.6, 1.8, 1.6])
CATEGORIES = ["Food", "Services", "Transport", "Other", "Entertainment", "Health"]
STATUS = ["Approved", "Declined", "Pending", "Reversed"]
STATUS_P = [0.9197, 0.0500, 0.0199, 0.0104]
DECLINE_CODES = ["05", "14", "51", "54"]

REASONS = ["Transaccional", "Producto", "Queja", "Técnico", "Comercial", "Retención"]
REASON_P = [0.3498, 0.2198, 0.1705, 0.1499, 0.0800, 0.0300]
FCR = {"Transaccional": 0.915, "Producto": 0.896, "Queja": 0.436, "Técnico": 0.699,
       "Comercial": 0.652, "Retención": 0.602}
DURATION_MEDIAN = {"Transaccional": 205, "Producto": 263, "Queja": 431, "Técnico": 360,
                   "Comercial": 540, "Retención": 478}
SENTIMENTS = ["Neutral", "Negativo", "Positivo", "Muy Negativo", "Muy Positivo"]
SENTIMENT_SCORE = {"Neutral": (-0.2, 0.2), "Negativo": (-0.6, -0.2), "Positivo": (0.2, 0.6),
                   "Muy Negativo": (-1.0, -0.6), "Muy Positivo": (0.6, 1.0)}

COMPLAINT_CATEGORIES = ["Transactions", "Fees", "Technical", "Branch", "Service"]
COMPLAINT_CAT_P = [0.2024, 0.2020, 0.1998, 0.1991, 0.1967]
SUBCATEGORY = {"Transactions": "Cargo no reconocido", "Fees": "Cobro indebido", "Technical": "Problema con app",
               "Branch": "Atención en sucursal", "Service": "Calidad de servicio"}
RESOLUTIONS = [
    "Se revisó el caso y se dio respuesta al cliente.",
    "Se aplicó el ajuste correspondiente según la política vigente.",
    "Se explicó al cliente el detalle del movimiento reclamado.",
    "No procede: el cargo corresponde a una operación válida.",
    "Caso derivado al área responsable para su seguimiento.",
]
TRANSCRIPT_OPEN = [
    ("Buenos días, quisiera consultar el saldo de mi cuenta.", "Con gusto, ¿me confirma su número de documento?"),
    ("Hola, necesito saber cuánto tengo disponible.", "Claro, permítame verificar sus datos."),
]
TRANSCRIPT_CLOSE = [
    ("Gracias, eso es todo.", "Su saldo disponible es de {monto} {moneda}. ¿Algo más en que pueda ayudarle?"),
    ("Perfecto, muchas gracias.", "Con gusto. Que tenga un buen día."),
    ("Entendido.", "Le confirmo que la consulta quedó registrada."),
    ("Ok, gracias por la ayuda.", "Fue un placer atenderle."),
]


# ---------------------------------------------------------------- helpers

def _choice(rng, values, p=None, size=None):
    p = None if p is None else np.asarray(p, dtype=float) / np.sum(p)
    return rng.choice(np.asarray(values, dtype=object), p=p, size=size)


def _null_out(rng, arr, frac):
    out = np.asarray(arr, dtype=object).copy()
    out[rng.random(len(out)) < frac] = None
    return out


def _bool(arr) -> np.ndarray:
    return np.where(np.asarray(arr, dtype=bool), "True", "False").astype(object)


def _days(rng, n, start=None, end=None) -> np.ndarray:
    start, end = start or START, end or END
    span = (end - start).days + 1
    return np.datetime64(start) + rng.integers(0, span, size=n).astype("timedelta64[D]")


def _ts(rng, days: np.ndarray, hour_lo=0, hour_hi=24) -> np.ndarray:
    secs = rng.integers(hour_lo * 3600, hour_hi * 3600, size=len(days))
    return days.astype("datetime64[s]") + secs.astype("timedelta64[s]")


def _fmt_ts(ts) -> np.ndarray:
    s = pd.Series(ts)
    return s.dt.strftime("%Y-%m-%d %H:%M:%S").where(s.notna(), None).to_numpy(dtype=object)


def _fmt_date(d) -> np.ndarray:
    s = pd.Series(d)
    return s.dt.strftime("%Y-%m-%d").where(s.notna(), None).to_numpy(dtype=object)


def _process_date(ts: np.ndarray, hours: int) -> np.ndarray:
    return (ts - np.timedelta64(hours, "h")).astype("datetime64[D]")


def _money(x: np.ndarray, decimals: int = 2) -> np.ndarray:
    return np.round(np.asarray(x, dtype=float), decimals)


def _digits(rng, n, k) -> np.ndarray:
    return np.array(["".join(map(str, row)) for row in rng.integers(0, 10, size=(n, k))], dtype=object)


# ---------------------------------------------------------------- tables

def gen_exchange_rates(rng) -> pd.DataFrame:
    days = np.arange(np.datetime64(START), np.datetime64(END) + np.timedelta64(1, "D"))
    n = len(days)
    usd_to = {
        "MXN": 17.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n)) - 0.0),
        "COP": 4000.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n))),
        "ARS": 350.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.004, n))),
        "USD": np.ones(n),
    }
    usd_to["COP"] = np.clip(usd_to["COP"], 3600, 4079.94)
    rows = []
    sources = ["Bloomberg", "Reuters", "Internal", "Central Bank"]
    for src in ["USD", "MXN", "COP", "ARS"]:
        for tgt in ["USD", "MXN", "COP", "ARS"]:
            if src == tgt:
                continue
            rate = usd_to[tgt] / usd_to[src]
            spread = rng.uniform(0.003, 0.012, n)
            rows.append(pd.DataFrame({
                "date": _fmt_date(days), "source_currency": src, "target_currency": tgt,
                "exchange_rate": np.round(rate, 6), "buy_rate": np.round(rate * (1 - spread), 6),
                "sell_rate": np.round(rate * (1 + spread), 6), "source": _choice(rng, sources, size=n),
            }))
    return pd.concat(rows, ignore_index=True)


def gen_branches(rng, n=70) -> pd.DataFrame:
    country = np.array(["México"] * (n // 2) + ["Colombia"] * (n * 3 // 10) + ["Argentina"] * (n - n // 2 - n * 3 // 10),
                       dtype=object)
    rows = []
    for i, c in enumerate(country):
        city, state, cp = CITIES[c][rng.integers(len(CITIES[c]))]
        suffix = _choice(rng, ["Centro", "Plaza", "Norte", "Sur", "Express", "Mall"])
        lat0, lon0 = CENTER[c]
        rows.append({
            "branch_id": f"BRN-{1001 + i}", "branch_code": f"SUC{1001 + i:05d}",
            "branch_name": f"Banco LATAM {city} {suffix}",
            "branch_type": _choice(rng, ["Express", "Corporate", "Premium", "Main"], [0.3714, 0.3686, 0.1371, 0.1229]),
            "address": f"Av. {_choice(rng, LAST)} {rng.integers(10, 9999)}", "city": city, "state": state,
            "country": c, "postal_code": cp, "geographic_zone": "Urbana",
            "phone": f"+{rng.integers(10**9, 10**10)}", "email": f"sucursal{1001 + i}@bancolatam.com",
            "opening_time": _choice(rng, ["09:30:00", "09:00:00", "08:00:00", "08:30:00"]),
            "closing_time": _choice(rng, ["17:00:00", "19:00:00", "20:00:00", "18:00:00", "17:30:00"]),
            "has_atms": "True", "atm_count": int(rng.integers(2, 9)), "has_teller_windows": "True",
            "teller_window_count": int(rng.integers(3, 13)),
            "latitude": round(lat0 + rng.uniform(-1, 1), 6), "longitude": round(lon0 + rng.uniform(-1, 1), 6),
            "branch_opening_date": str(date(1990, 1, 3) + timedelta(days=int(rng.integers(0, 12000)))),
            "branch_status": "Active" if rng.random() > 0.04 else "Temporarily Closed",
        })
    return pd.DataFrame(rows)


def gen_agents(rng, branches: pd.DataFrame, n=120) -> pd.DataFrame:
    origin = _choice(rng, ["Mexico", "Colombia", "Argentina"], [0.5, 0.3, 0.2], size=n)
    accent = np.array([{"Mexico": "mexican", "Colombia": "colombian", "Argentina": "argentine"}[o] for o in origin], dtype=object)
    gender = rng.random(n) < 0.5
    first = np.where(gender, _choice(rng, FIRST_F, size=n), _choice(rng, FIRST_M, size=n))
    last = _choice(rng, LAST, size=n)
    branch_ok = rng.random(n) < 0.31
    assigned = np.where(branch_ok, _choice(rng, branches["branch_id"].tolist(), size=n),
                        np.array([f"BR-{x}" for x in rng.integers(100000, 999999, size=n)], dtype=object))
    assigned = _null_out(rng, assigned, 0.3058)
    hire = _days(rng, n, date(2013, 6, 20), date(2026, 3, 17))
    return pd.DataFrame({
        "agent_id": [f"AGT-{20001 + i}" for i in range(n)],
        "employee_code": [f"EMP{int(x):06d}" for x in rng.integers(1000, 999999, size=n)],
        "first_name": first, "last_name": last,
        "email": [f"{f[0].lower()}.{l.lower()}@bancolatam.com" for f, l in zip(first, last)],
        "phone": _null_out(rng, np.array([f"+{x}" for x in rng.integers(10**10, 10**11, size=n)], dtype=object), 0.0575),
        "native_accent": accent, "country_of_origin": origin, "assigned_branch_id": assigned,
        "agent_type": _choice(rng, ["Phone", "Digital", "In-Person", "Hybrid"], [0.49, 0.2092, 0.1917, 0.1092], size=n),
        "experience_level": _choice(rng, ["Specialist", "Senior", "Mid-Senior", "Junior"], [0.6342, 0.2383, 0.1125, 0.015], size=n),
        "languages": _choice(rng, ["español", "español, inglés", "español, portugués", "español, inglés, portugués"],
                             [0.5408, 0.3517, 0.0567, 0.0508], size=n),
        "specialty": _null_out(rng, _choice(rng, ["Fraudes", "Cobranza", "Retención", "Soporte Técnico", "Créditos",
                                                  "Inversiones", "Quejas y Reclamos", "Tarjetas"], size=n), 0.3967),
        "hire_date": _fmt_date(hire),
        "avg_csat": _null_out(rng, np.round(rng.uniform(3.5, 5.0, n), 2), 0.1117),
        "total_monthly_interactions": _null_out(rng, rng.integers(100, 801, n), 0.0925),
        "agent_status": _choice(rng, ["Active", "Vacation", "Leave", "Inactive"], [0.9083, 0.0517, 0.0242, 0.0158], size=n),
        "work_shift": _choice(rng, ["Afternoon", "Morning", "Rotating", "Night"], [0.3483, 0.3317, 0.1692, 0.1508], size=n),
    })


def gen_customers(rng, n: int) -> pd.DataFrame:
    country = _choice(rng, COUNTRIES, COUNTRY_P, size=n)
    city_rows = [CITIES[c][rng.integers(len(CITIES[c]))] for c in country]
    gender = _choice(rng, ["F", "O", "M"], size=n)
    first = np.where(gender == "M", _choice(rng, FIRST_M, size=n),
                     np.where(gender == "F", _choice(rng, FIRST_F, size=n),
                              np.where(rng.random(n) < 0.5, _choice(rng, FIRST_F, size=n), _choice(rng, FIRST_M, size=n))))
    last = _choice(rng, LAST, size=n)
    # Shared e-mails (dataset defect): first name or initial + surname + optional number, 7 domains.
    num = np.where(rng.random(n) < 0.35, rng.integers(1, 99, n).astype(str), "")
    local = [(f[0] if rng.random() < 0.4 else f).lower() + "." + l.lower() + s for f, l, s in zip(first, last, num)]
    email = _null_out(rng, np.array([f"{x}@{d}" for x, d in zip(local, _choice(rng, EMAIL_DOMAINS, size=n))], dtype=object), 0.0199)
    # +54 phones for every Mexican customer (dataset defect).
    prefix = np.array(["+54" if c in ("México", "Argentina") else "+57" for c in country], dtype=object)
    mobile = _null_out(rng, np.array([f"{p} 9 {x}" for p, x in zip(prefix, rng.integers(10**9, 10**10, size=n))], dtype=object), 0.0314)
    landline = _null_out(rng, np.array([f"{p} {x}" for p, x in zip(prefix, rng.integers(10**7, 10**8, size=n))], dtype=object), 0.5004)
    doc_type = _choice(rng, ["DNI", "CE", "Pasaporte", "CC"], [0.6983, 0.101, 0.1004, 0.1003], size=n)
    doc_num = np.array([f"{x}" for x in rng.permutation(np.arange(10_000_000, 10_000_000 + n * 7))[:n]], dtype=object)
    reg = _days(rng, n, date(2018, 6, 18), END)
    updated = reg.astype("datetime64[s]") + rng.integers(0, 3 * 365 * 86400, size=n).astype("timedelta64[s]")
    updated = np.minimum(updated, np.datetime64(END).astype("datetime64[s]") + np.int64(86399))
    future = rng.random(n) < 0.06
    updated[future] = (np.datetime64(END).astype("datetime64[s]")
                       + rng.integers(86400, 363 * 86400, size=int(future.sum())).astype("timedelta64[s]"))
    income_usd = np.exp(rng.normal(np.log(2300), 0.8, n))
    income_local = income_usd * np.array([FIXED_RATE[INCOME_CCY[c]] for c in country])
    score = np.clip(rng.normal(647, 90, n), 422, 850).round().astype(int)
    seg = np.where((income_usd > 6000) & (score > 650), "Premium",
                   np.where((income_usd > 2600) | (score > 720), "Plus", "Basic"))
    seg = np.where(rng.random(n) < 0.05, "Student", seg)
    return pd.DataFrame({
        "customer_id": [f"CUS-{10000001 + i}" for i in range(n)],
        "document_number": doc_num, "document_type": doc_type, "first_name": first, "last_name": last,
        "date_of_birth": _fmt_date(_days(rng, n, date(1942, 7, 7), date(2005, 6, 21))),
        "gender": gender, "email": email, "mobile_phone": mobile, "landline_phone": landline,
        "address": _null_out(rng, np.array([f"Calle {_choice(rng, LAST)} {x}" for x in rng.integers(1, 9999, size=n)], dtype=object), 0.0491),
        "city": [r[0] for r in city_rows], "state": [r[1] for r in city_rows], "country": country,
        "postal_code": _null_out(rng, np.array([r[2] for r in city_rows], dtype=object), 0.1003),
        "detected_accent": _null_out(rng, np.array([ACCENT[c] for c in country], dtype=object), 0.2988),
        "segment": seg,
        "credit_score": _null_out(rng, score, 0.1499),
        "estimated_monthly_income": _null_out(rng, np.round(income_local, 2), 0.2002),
        "occupation": _null_out(rng, _choice(rng, ["Manager", "Accountant", "Salesperson", "Homemaker", "Entrepreneur",
                                                   "Doctor", "Engineer", "Teacher", "Student", "Lawyer", "Nurse", "Driver",
                                                   "Retired", "Artist", "Chef", "Technician", "Analyst", "Consultant",
                                                   "Mechanic", "Architect"], size=n), 0.1003),
        "marital_status": _null_out(rng, _choice(rng, ["Married", "Divorced", "Single", "Widowed"], size=n), 0.0797),
        "education_level": _null_out(rng, _choice(rng, ["College Prep", "High School", "University", "Graduate", "Elementary"],
                                                  [0.2998, 0.2508, 0.2489, 0.1005, 0.1], size=n), 0.1197),
        "registration_date": _fmt_ts(_ts(rng, reg, 8, 19)),
        "registration_branch_id": [f"BR-{x}" for x in rng.integers(100000, 999999, size=n)],  # broken FK (defect)
        "customer_status": _choice(rng, ["Active", "Inactive", "Suspended", "Closed"], [0.8513, 0.0994, 0.0294, 0.0199], size=n),
        "last_updated": _fmt_ts(updated), "accepts_marketing": _bool(rng.random(n) < 0.5),
    })


def gen_products(rng, customers: pd.DataFrame, branches: pd.DataFrame) -> pd.DataFrame:
    has = rng.random(len(customers)) < 0.93
    owners = customers.loc[has, ["customer_id", "country"]]
    counts = 1 + rng.poisson(1.87, size=len(owners))
    cust = np.repeat(owners["customer_id"].to_numpy(), counts)
    country = np.repeat(owners["country"].to_numpy(), counts)
    n = len(cust)
    ptype = _choice(rng, PRODUCT_TYPES, PRODUCT_P, size=n)
    local = np.array([LOCAL_CCY[c] for c in country], dtype=object)
    usd_draw = rng.random(n) < 0.10
    currency = np.where((local != "USD") & usd_draw, "USD", local)
    number = np.empty(n, dtype=object)
    for t in PRODUCT_TYPES:
        m = ptype == t
        k = int(m.sum())
        if k == 0:
            continue
        if t in CARDS:
            number[m] = ["4" + x for x in _digits(rng, k, 15)]
        elif t.startswith("Préstamo"):
            number[m] = ["LOAN-" + x for x in _digits(rng, k, 8)]
        elif t == "Inversión":
            number[m] = ["INV-" + x for x in _digits(rng, k, 7)]
        elif t == "Seguro":
            number[m] = ["POL-" + x for x in _digits(rng, k, 7)]
        else:
            number[m] = list(_digits(rng, k, 10))
    opening = _days(rng, n, date(2018, 6, 18), END)
    is_card = np.isin(ptype, list(CARDS))
    expiry = opening + rng.integers(3 * 365, 5 * 365 + 1, size=n).astype("timedelta64[D]")
    expiry_s = np.where(is_card, _fmt_date(expiry), None)
    is_credit = np.isin(ptype, list(CREDIT))
    rate = np.array([FIXED_RATE[c] for c in currency])
    limit = np.where(is_credit, np.round(rng.uniform(1000, 60000, n) * rate, 2), np.nan)
    balance = np.round(np.abs(rng.normal(0, 1, n)) * rng.uniform(500, 20000, n) * rate, 2)
    dpd = np.where(is_credit, _choice(rng, [0, 15, 30, 60, 90, 180], [0.8503, 0.0248, 0.0249, 0.0248, 0.0258, 0.0494], size=n), None)
    last_tx = _null_out(rng, _fmt_ts(_ts(rng, _days(rng, n, date(2018, 6, 21), END))), 0.2357)
    updated = opening.astype("datetime64[s]") + rng.integers(0, 3 * 365 * 86400, size=n).astype("timedelta64[s]")
    updated = np.minimum(updated, np.datetime64(END).astype("datetime64[s]"))
    return pd.DataFrame({
        "product_id": [f"PRD-{20000001 + i}" for i in range(n)], "customer_id": cust, "product_type": ptype,
        "product_number": number, "currency": currency, "current_balance": balance,
        "credit_limit": np.where(np.isnan(limit), None, limit),
        "interest_rate": _null_out(rng, np.where(is_credit, np.round(rng.uniform(8, 45, n), 2), np.round(rng.uniform(0, 6, n), 2)), 0.1002),
        "opening_date": _fmt_date(opening), "expiration_date": expiry_s,
        "opening_branch_id": _choice(rng, branches["branch_id"].tolist(), size=n),
        "product_status": _choice(rng, ["Active", "Closed", "Blocked", "Suspended"], [0.8499, 0.0801, 0.0498, 0.0202], size=n),
        "opening_channel": _choice(rng, ["Branch", "Web", "App", "Call Center"], [0.4996, 0.2507, 0.1993, 0.0504], size=n),
        "has_linked_app": _bool(rng.random(n) < 0.4996),
        "days_past_due": dpd, "last_transaction_date": last_tx, "last_updated": _fmt_ts(updated),
    })


def gen_transactions(rng, products: pd.DataFrame, customers: pd.DataFrame, branches: pd.DataFrame) -> pd.DataFrame:
    active = products[products["product_status"] == "Active"]
    counts = rng.poisson(13, size=len(active))
    idx = np.repeat(np.arange(len(active)), counts)
    p = active.iloc[idx].reset_index(drop=True)
    n = len(p)
    cust_country = customers.set_index("customer_id").loc[p["customer_id"], ["country", "city"]].reset_index(drop=True)
    ttype = np.empty(n, dtype=object)
    for t, mix in TX_MIX.items():
        m = (p["product_type"] == t).to_numpy()
        if m.any():
            ttype[m] = _choice(rng, TX_TYPES, mix, size=int(m.sum()))
    days = _days(rng, n)
    ts = _ts(rng, days, 0, 24)
    # purchases mostly in the day: re-draw their hour 7..23
    pur = ttype == "Purchase"
    ts[pur] = _ts(rng, days[pur], 7, 23)
    pdate = _process_date(ts, 6)
    usd = np.empty(n)
    for t, (lo, hi) in TX_USD_RANGE.items():
        m = ttype == t
        usd[m] = rng.uniform(lo, hi, int(m.sum()))
    ccy = p["currency"].to_numpy()
    rate = np.array([FIXED_RATE[c] for c in ccy])
    amount = _money(usd * rate)
    amount_usd = np.where(ccy == "USD", None, np.round(amount / rate, 2)).astype(object)
    amount_usd = _null_out(rng, amount_usd, 0.05)
    # merchants: purchases + a third of payments (services)
    has_m = pur | ((ttype == "Payment") & (rng.random(n) < 0.33))
    m_idx = rng.choice(len(MERCHANTS), size=n, p=MERCHANT_W / MERCHANT_W.sum())
    merchant = np.where(has_m, np.array([MERCHANTS[i][0] for i in m_idx], dtype=object), None)
    mcat = np.where(has_m, np.array([MERCHANTS[i][1] for i in m_idx], dtype=object), None)
    mcat_out = mcat.copy()
    mcat_out[(rng.random(n) < 0.02) & has_m] = None  # imputable from the merchant (dq flag in silver)
    tcat = np.where(has_m, mcat, np.where((ttype == "Payment") & (rng.random(n) < 0.5), "Services",
                                          np.where(rng.random(n) < 0.18, _choice(rng, CATEGORIES, size=n), None)))
    channel = np.empty(n, dtype=object)
    for t, (vals, pr) in TX_CHANNEL.items():
        m = ttype == t
        channel[m] = _choice(rng, vals, pr, size=int(m.sum()))
    country = cust_country["country"].to_numpy()
    foreign = rng.random(n)
    tx_country = np.where(foreign < 0.0093, "Brazil", np.where(foreign < 0.0186, "USA", country)).astype(object)
    tx_country[(tx_country == "México") & (rng.random(n) < 0.019)] = "Mexico"  # unaccented duplicate (defect)
    city = np.where(np.isin(tx_country, ["Brazil"]), "São Paulo",
                    np.where(tx_country == "USA", "Miami", cust_country["city"].to_numpy())).astype(object)
    city = _null_out(rng, city, 0.1003)
    br_by_country = {c: branches.loc[branches["country"] == c, "branch_id"].tolist() for c in COUNTRIES}
    branch = np.array([rng.choice(br_by_country[c]) if ch in ("ATM", "Branch") or rng.random() < 0.05 else None
                       for c, ch in zip(country, channel)], dtype=object)
    status = _choice(rng, STATUS, STATUS_P, size=n)
    code = np.where(status == "Approved", "00", _choice(rng, DECLINE_CODES, size=n)).astype(object)
    code = _null_out(rng, code, 0.0498)
    is_fraud = rng.random(n) < FRAUD_RATE
    score = rng.uniform(0, 30, n)
    hi = is_fraud & (rng.random(n) < 0.55)
    score[hi] = rng.uniform(30.01, 99.99, int(hi.sum()))
    score = np.round(score, 2).astype(object)
    score[(rng.random(n) < 0.1996) & ~hi] = None
    has_geo = rng.random(n) < 0.1935
    lat0 = np.array([CENTER.get(c, (0, 0))[0] for c in country])
    lon0 = np.array([CENTER.get(c, (0, 0))[1] for c in country])
    lat = np.where(has_geo, np.round(lat0 + rng.uniform(-1, 1, n), 6), None)
    lon = np.where(has_geo, np.round(lon0 + rng.uniform(-1, 1, n), 6), None)
    order = np.argsort(ts, kind="stable")
    df = pd.DataFrame({
        "transaction_id": None, "transaction_date": _fmt_ts(ts), "process_date": _fmt_date(pdate),
        "product_id": p["product_id"].to_numpy(), "customer_id": p["customer_id"].to_numpy(),
        "transaction_type": ttype, "transaction_category": tcat, "amount": amount, "currency": ccy,
        "amount_usd": amount_usd, "channel": channel, "branch_id": branch, "merchant_name": merchant,
        "merchant_category": mcat_out, "transaction_country": tx_country, "transaction_city": city,
        "transaction_status": status, "response_code": code, "is_fraud": _bool(is_fraud),
        "fraud_score": score, "latitude": lat, "longitude": lon,
    }).iloc[order].reset_index(drop=True)
    df["transaction_id"] = [f"TXN-{100000001 + i}" for i in range(len(df))]
    return df


def _daily_counts(rng, total: int, weekend_half: bool = True) -> np.ndarray:
    days = np.arange(np.datetime64(START), np.datetime64(END) + np.timedelta64(1, "D"))
    weekday = (days.astype("datetime64[D]").view("int64") - 4) % 7  # 0 = Monday
    w = np.where(weekday >= 5, 0.5, 1.0) if weekend_half else np.ones(len(days))
    w = w * rng.uniform(0.8, 1.2, len(days))
    lam = w / w.sum() * total
    return rng.poisson(lam)


def gen_call_center(rng, customers: pd.DataFrame, agents: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    total = int(686_296 * len(customers) / 150_000)
    per_day = _daily_counts(rng, total)
    days = np.repeat(np.arange(np.datetime64(START), np.datetime64(END) + np.timedelta64(1, "D")), per_day)
    n = len(days)
    ts = _ts(rng, days, 0, 24)
    cust = _choice(rng, customers["customer_id"].tolist(), size=n)
    country = customers.set_index("customer_id").loc[cust, "country"].to_numpy()
    ag = agents.sample(n=n, replace=True, random_state=int(rng.integers(1 << 31)))
    reason = _choice(rng, REASONS, REASON_P, size=n)
    itype = _choice(rng, ["Inbound Call", "Outbound Call", "Chat", "Email", "Video"], [0.7004, 0.1495, 0.1001, 0.0401, 0.0099], size=n)
    channel = np.where(np.isin(itype, ["Inbound Call", "Outbound Call"]), "Phone",
                       np.where(itype == "Email", "Email", np.where(itype == "Video", "Web",
                                _choice(rng, ["App", "WhatsApp", "Web Chat"], [0.384, 0.333, 0.283], size=n)))).astype(object)
    med = np.array([DURATION_MEDIAN[r] for r in reason])
    duration = np.clip(np.round(med * np.exp(rng.normal(0, 0.45, n))), 30, 1204).astype(int)
    resolved = rng.random(n) < np.array([FCR[r] for r in reason])
    followup = ~resolved | (rng.random(n) < 0.15)
    sentiment = np.where(reason == "Transaccional", "Neutral",
                         _choice(rng, SENTIMENTS, [0.49, 0.21, 0.17, 0.085, 0.045], size=n)).astype(object)
    s_lo = np.array([SENTIMENT_SCORE[s][0] for s in sentiment])
    s_hi = np.array([SENTIMENT_SCORE[s][1] for s in sentiment])
    accent_null = rng.random(n) < 0.2983
    cust_accent = np.where(accent_null, None, np.array([ACCENT[c] for c in country], dtype=object))
    agent_accent = np.where(accent_null, None, ag["native_accent"].to_numpy())
    has_tr = rng.random(n) < 0.2496
    mentioned = np.where(rng.random(n) < 0.3997, np.array([str(x) for x in _digits(rng, n, 10)], dtype=object), None)
    df = pd.DataFrame({
        "interaction_id": [f"INT-{300000001 + i}" for i in range(n)],
        "interaction_date": _fmt_ts(ts), "process_date": _fmt_date(_process_date(ts, 8)),
        "customer_id": cust, "agent_id": ag["agent_id"].to_numpy(), "interaction_type": itype,
        "channel": channel, "contact_reason": reason, "reason_category": reason,
        "duration_seconds": _null_out(rng, duration, 0.1402),
        "wait_time_seconds": _null_out(rng, np.clip(np.round(rng.gamma(4, 30, n)), 0, 424).astype(int), 0.2996),
        "was_resolved": _bool(resolved), "requires_followup": _bool(followup),
        "detected_sentiment": sentiment, "sentiment_score": np.round(rng.uniform(s_lo, s_hi), 2),
        "customer_detected_accent": cust_accent, "agent_used_accent": agent_accent,
        "was_escalated": _bool(rng.random(n) < 0.0996), "mentioned_products": mentioned,
        "has_transcript": _bool(has_tr), "has_recording": _bool(np.isin(itype, ["Inbound Call", "Outbound Call", "Video"]) & (rng.random(n) < 0.99)),
    })
    df["_resolved"] = resolved
    df["_ts"] = ts
    return df


def gen_transcripts(rng, cc: pd.DataFrame) -> pd.DataFrame:
    rows = cc[cc["has_transcript"] == "True"]
    n = len(rows)
    o = rng.integers(0, len(TRANSCRIPT_OPEN), n)
    c = rng.integers(0, len(TRANSCRIPT_CLOSE), n)
    cust_text = [TRANSCRIPT_OPEN[a][0] + " " + TRANSCRIPT_CLOSE[b][0] for a, b in zip(o, c)]
    agent_text = [TRANSCRIPT_OPEN[a][1] + " " + TRANSCRIPT_CLOSE[b][1] for a, b in zip(o, c)]
    full = [f"Cliente: {TRANSCRIPT_OPEN[a][0]} Agente: {TRANSCRIPT_OPEN[a][1]} Cliente: {TRANSCRIPT_CLOSE[b][0]} "
            f"Agente: {TRANSCRIPT_CLOSE[b][1]}" for a, b in zip(o, c)]
    return pd.DataFrame({
        "transcript_id": [f"TRS-{400000001 + i}" for i in range(n)],
        "interaction_id": rows["interaction_id"].to_numpy(), "process_date": rows["process_date"].to_numpy(),
        "customer_id": rows["customer_id"].to_numpy(), "agent_id": rows["agent_id"].to_numpy(),
        "full_text": full, "customer_text": cust_text, "agent_text": agent_text, "detected_language": "es",
        "detected_accent": _null_out(rng, rows["customer_detected_accent"].to_numpy(), 0.1),
        "accent_confidence": _null_out(rng, np.round(rng.uniform(0.75, 0.99, n), 2), 0.1001),
        "detected_keywords": _null_out(rng, _choice(rng, ["banco, servicio, cuenta", "cuenta, servicio, banco", "cuenta, banco, servicio",
                                                          "servicio, cuenta, banco", "servicio, banco, cuenta", "banco, cuenta, servicio"], size=n), 0.0513),
        "mentioned_entities": _null_out(rng, np.array(["cuenta de ahorros, saldo disponible"] * n, dtype=object), 0.1002),
        "detected_intents": _null_out(rng, np.array(["consulta_general"] * n, dtype=object), 0.0494),
        "main_topics": rows["contact_reason"].to_numpy(),
        "transcription_model": _choice(rng, ["AWS Transcribe", "Whisper v3", "Google STT", "Azure Speech"], size=n),
        "audio_quality": _null_out(rng, _choice(rng, ["High", "Medium", "Low"], [0.703, 0.248, 0.049], size=n), 0.0504),
        "duration_seconds": rows["duration_seconds"].to_numpy(),
    })


def gen_surveys(rng, cc: pd.DataFrame) -> pd.DataFrame:
    rows = cc[rng.random(len(cc)) < 0.31]
    n = len(rows)
    resolved = rows["_resolved"].to_numpy()
    stype = _choice(rng, ["CSAT", "NPS", "CES"], [0.6009, 0.2992, 0.0999], size=n)
    noise = _choice(rng, [-1, 0, 1], [0.15, 0.70, 0.15], size=n).astype(int)
    csat = np.clip(2 + resolved.astype(int) + noise, 1, 5)
    nps = np.where(resolved, rng.integers(5, 8, n), rng.integers(2, 5, n))
    score = np.where(stype == "NPS", nps, csat)
    hours = np.round(rng.uniform(1.01, 35.97, n), 2)
    sdate = rows["_ts"].to_numpy() + (hours * 3600).astype("int64").astype("timedelta64[s]")
    q1 = _null_out(rng, _choice(rng, ["¿Cómo calificaría la atención brindada?", "¿El agente resolvió su consulta satisfactoriamente?",
                                      "¿Qué tan satisfecho está con el servicio recibido?"], size=n), 0.4299)
    comments = _null_out(rng, _choice(rng, ["Tardaron mucho en atenderme.", "No resolvieron mi problema completamente.",
                                            "Tuve que esperar demasiado tiempo.", "No estoy satisfecho con la solución.",
                                            "El agente no fue muy claro en sus explicaciones.", "Normal, sin problemas mayores.",
                                            "Buena atención.", "Todo bien."], size=n), 0.5244)
    return pd.DataFrame({
        "survey_id": [f"SRV-{500000001 + i}" for i in range(n)], "survey_date": _fmt_ts(sdate),
        "process_date": rows["process_date"].to_numpy(), "interaction_id": rows["interaction_id"].to_numpy(),
        "customer_id": rows["customer_id"].to_numpy(), "agent_id": rows["agent_id"].to_numpy(),
        "survey_type": stype, "send_channel": _choice(rng, ["Email", "SMS", "App", "IVR", "Web"], [0.3989, 0.2999, 0.2002, 0.0509, 0.0501], size=n),
        "main_score": score, "nps_category": np.where(stype == "NPS", np.where(score <= 6, "Detractor", "Passive"), None),
        "question_1_text": q1, "question_1_response": np.where(pd.isna(q1), None, rng.integers(1, 6, n)),
        "question_2_text": _null_out(rng, np.array(["¿El tiempo de espera fue aceptable?"] * n, dtype=object), 0.6175),
        "question_2_response": _null_out(rng, rng.integers(1, 6, n), 0.617),
        "question_3_text": _null_out(rng, np.array(["¿Volvería a contactarnos por este canal?"] * n, dtype=object), 0.8113),
        "question_3_response": _null_out(rng, rng.integers(1, 6, n), 0.8115),
        "open_comments": comments,
        "comment_sentiment": np.where(pd.isna(comments), None, _choice(rng, ["Negative", "Neutral", "Positive"], [0.6669, 0.2644, 0.0687], size=n)),
        "response_time_hours": hours,
        "campaign_response_rate": _null_out(rng, np.round(rng.uniform(15, 45, n), 2), 0.1506),
    })


def gen_complaints(rng, customers: pd.DataFrame, products: pd.DataFrame, agents: pd.DataFrame,
                   branches: pd.DataFrame) -> pd.DataFrame:
    total = int(67_095 * len(customers) / 150_000)
    per_day = _daily_counts(rng, total, weekend_half=False)
    days = np.repeat(np.arange(np.datetime64(START), np.datetime64(END) + np.timedelta64(1, "D")), per_day)
    n = len(days)
    ts = _ts(rng, days, 0, 24)
    cat = _choice(rng, COMPLAINT_CATEGORIES, COMPLAINT_CAT_P, size=n)
    sub = np.array([SUBCATEGORY[c] for c in cat], dtype=object)
    swap = rng.random(n) < 0.04
    sub[swap] = _choice(rng, list(SUBCATEGORY.values()), size=int(swap.sum()))
    sub = _null_out(rng, sub, 0.0998)
    status = _choice(rng, ["In Process", "Open", "Resolved", "Escalated", "Closed", "Rejected"],
                     [0.3998, 0.2999, 0.2014, 0.0495, 0.0389, 0.0105], size=n)
    assigned = (status != "Open") | (rng.random(n) < 0.15)
    assigned &= rng.random(n) < 0.83
    assign_ts = ts + rng.integers(600, 48 * 3600, n).astype("timedelta64[s]")
    first_resp = (status != "Open") & (rng.random(n) < 0.86)
    fr_ts = ts + np.round(np.exp(rng.normal(np.log(38), 0.6, n)) * 3600).astype("int64").astype("timedelta64[s]")
    done = np.isin(status, ["Resolved", "Closed", "Rejected"])
    res_days = rng.integers(1, 31, n)
    res_ts = ts + (res_days * 86400).astype("timedelta64[s]")
    closed = status == "Closed"
    close_ts = res_ts + rng.integers(3600, 5 * 86400, n).astype("timedelta64[s]")
    has_amount = rng.random(n) < 0.3242
    comp = done & (rng.random(n) < 0.30)
    return pd.DataFrame({
        "complaint_id": [f"CMP-{60000001 + i}" for i in range(n)],
        "creation_date": _fmt_ts(ts), "process_date": _fmt_date(_process_date(ts, 8)),
        "customer_id": _choice(rng, customers["customer_id"].tolist(), size=n),
        "case_type": _choice(rng, ["Complaint", "Claim", "Request", "Suggestion"], [0.6029, 0.2474, 0.1008, 0.0489], size=n),
        "category": cat, "subcategory": sub,
        "reception_channel": _choice(rng, ["Call Center", "Email", "Web", "App", "Branch", "Regulator"],
                                     [0.5032, 0.1986, 0.1473, 0.1003, 0.04, 0.0107], size=n),
        # a random product of the whole bank: another customer's in practice (v1.4 finding)
        "affected_product_id": _null_out(rng, _choice(rng, products["product_id"].tolist(), size=n), 0.3357),
        "related_branch_id": _null_out(rng, _choice(rng, branches["branch_id"].tolist(), size=n), 0.7142),
        "origin_interaction_id": None,
        "description": [f"Queja relacionada con {c.lower()}" for c in cat],
        "claimed_amount": np.where(has_amount, np.round(rng.uniform(50.27, 4999.93, n), 2), None),
        "currency": np.where(has_amount, _choice(rng, ["MXN", "COP", "USD", "ARS"], size=n), None),
        "priority": _choice(rng, ["Medium", "Low", "High", "Critical"], [0.4984, 0.3042, 0.1474, 0.05], size=n),
        "status": status,
        "assigned_agent_id": np.where(assigned, _choice(rng, agents["agent_id"].tolist(), size=n), None),
        "assignment_date": np.where(assigned, _fmt_ts(assign_ts), None),
        "first_response_date": np.where(first_resp, _fmt_ts(fr_ts), None),
        "resolution_date": np.where(done, _fmt_ts(res_ts), None),
        "closing_date": np.where(closed, _fmt_ts(close_ts), None),
        "sla_breached": _bool(rng.random(n) < 0.2011),
        "resolution_days": np.where(done, res_days, None),
        "resolution": np.where(done, _choice(rng, RESOLUTIONS, size=n), None),
        "compensation_granted": np.where(comp, np.round(rng.uniform(10.15, 499.98, n), 2), None),
        "resolution_satisfaction": np.where(closed, rng.integers(1, 6, n), None),
        "is_repeat_complainer": _bool(rng.random(n) < 0.1503),  # a coin flip, not real history (v1.4 finding)
    })


def gen_digital_events(rng, customers: pd.DataFrame, products: pd.DataFrame, per_customer: float = 30.0) -> pd.DataFrame:
    n_sessions = int(len(customers) * per_customer / 6)
    days = _days(rng, n_sessions)
    start = _ts(rng, days, 6, 24)
    anon = rng.random(n_sessions) < 0.20
    cust = np.where(anon, None, _choice(rng, customers["customer_id"].tolist(), size=n_sessions))
    country_by_cust = customers.set_index("customer_id")["country"]
    ch = _choice(rng, ["Android App", "iOS App", "Desktop Web", "Mobile Web"], [0.351, 0.2493, 0.201, 0.1987], size=n_sessions)
    platform = np.where(ch == "Android App", "Android", np.where(ch == "iOS App", "iOS",
                        np.where(ch == "Mobile Web", _choice(rng, ["Android", "iOS"], size=n_sessions),
                                 _choice(rng, ["Linux", "MacOS", "Windows"], size=n_sessions)))).astype(object)
    browser = np.where(np.isin(ch, ["Desktop Web", "Mobile Web"]),
                       _choice(rng, ["Safari", "Chrome", "Samsung Internet", "Firefox", "Edge"], [0.29, 0.29, 0.17, 0.13, 0.12], size=n_sessions), None)
    app_ver = np.where(np.isin(ch, ["Android App", "iOS App"]),
                       np.array([f"{a}.{b}.{c}" for a, b, c in rng.integers(1, 10, size=(n_sessions, 3))], dtype=object), None)
    ip = np.array([f"{a}.{b}.{c}.{d}" for a, b, c, d in rng.integers(1, 255, size=(n_sessions, 4))], dtype=object)
    sess_country = np.array([country_by_cust.get(c, None) if c is not None else None for c in cust], dtype=object)
    sess_country = np.where(pd.isna(sess_country), _choice(rng, COUNTRIES, COUNTRY_P, size=n_sessions), sess_country).astype(object)
    ip_country = sess_country.copy()
    ip_country[(ip_country == "México") & (rng.random(n_sessions) < 0.145)] = "Mexico"
    pages = [("/login", "Iniciar Sesión", "login", "Authentication"), ("/products/loans", "Préstamos", "view_product", "Product"),
             ("/products/credit-card", "Tarjeta de Crédito", "view_product", "Product"),
             ("/products/savings", "Cuenta de Ahorro", "view_product", "Product"),
             ("/payments", "Pagar Servicios", "initiate_payment", "Transaction"),
             ("/transactions", "Movimientos", "view_transactions", "Transaction"),
             ("/transfers", "Transferencias", "initiate_transfer", "Transaction"),
             ("/home", "Inicio", "view_home", "Navigation"), ("/profile", "Mi Perfil", "view_profile", "Navigation"),
             ("/help", "Ayuda", "view_help", "Navigation"), ("/settings", "Configuración", "update_settings", "Navigation"),
             ("/logout", "Cerrar Sesión", "logout", "Authentication")]
    rows = []
    k = 0
    prod_ids = products["product_id"].tolist()
    for s in range(n_sessions):
        n_mid = int(rng.integers(2, 7))
        seq = [("Login", 0)] + [(str(_choice(rng, ["PageView", "Click", "FormSubmit", "Error", "Search"],
                                                  [0.58, 0.33, 0.055, 0.035, 0.0]) or "PageView"), int(rng.integers(1, 11))) for _ in range(n_mid)] + [("Logout", 11)]
        t = start[s]
        for etype, page_i in seq:
            t = t + np.timedelta64(int(rng.integers(5, 240)), "s")
            url, title, action, ecat = pages[page_i]
            rows.append((f"EVT-{700000001 + k}", t, cust[s], f"SES-{800000001 + s}", etype, ecat, ch[s], platform[s],
                         browser[s], app_ver[s], url, title, action,
                         f"btn-{action}" if etype in ("Click", "FormSubmit") else None,
                         rng.choice(prod_ids) if ecat == "Product" and rng.random() < 0.4 else None,  # never the customer's (v1.4)
                         round(float(rng.uniform(10.2, 4999.52)), 2) if etype == "FormSubmit" and rng.random() < 0.6 else None,
                         int(rng.integers(5, 301)) if rng.random() < 0.363 else None,
                         ip[s], ip_country[s], ch[s] in ("Android App", "iOS App", "Mobile Web")))
            k += 1
    ev = pd.DataFrame(rows, columns=["event_id", "_t", "customer_id", "session_id", "event_type", "event_category", "channel",
                                     "platform", "browser", "app_version", "page_url", "page_title", "action", "element_id",
                                     "product_id", "event_value", "duration_seconds", "ip_address", "ip_country", "is_mobile"])
    n = len(ev)
    t = ev["_t"].to_numpy().astype("datetime64[s]")
    city_lookup = {c: [x[0] for x in CITIES[c]] for c in COUNTRIES}
    ev.insert(1, "event_date", _fmt_ts(t))
    ev.insert(2, "process_date", _fmt_date(_process_date(t, 6)))
    ev = ev.drop(columns=["_t"])
    ev["platform"] = _null_out(rng, ev["platform"].to_numpy(), 0.0502)
    ev["page_url"] = _null_out(rng, ev["page_url"].to_numpy(), 0.0495)
    ev["ip_address"] = _null_out(rng, ev["ip_address"].to_numpy(), 0.0503)
    ev["ip_city"] = _null_out(rng, np.array([rng.choice(city_lookup["México" if c == "Mexico" else c]) for c in ev["ip_country"]], dtype=object), 0.2828)
    ev["is_mobile"] = _bool(ev["is_mobile"].to_numpy())
    utm = rng.random(n) < 0.0536
    ev["referrer"] = np.where(rng.random(n) < 0.0672, _choice(rng, ["https://www.instagram.com", "https://email.marketing.com",
                                                                     "https://www.facebook.com", "https://www.google.com"], size=n), None)
    ev["utm_source"] = np.where(utm, _choice(rng, ["direct", "google", "email", "facebook"], size=n), None)
    ev["utm_medium"] = np.where(utm, _choice(rng, ["organic", "cpc", "social", "email"], size=n), None)
    ev["utm_campaign"] = np.where(utm, _choice(rng, ["spring_promo", "new_users", "retention"], size=n), None)
    return ev[["event_id", "event_date", "process_date", "customer_id", "session_id", "event_type", "event_category",
               "channel", "platform", "browser", "app_version", "page_url", "page_title", "action", "element_id",
               "product_id", "event_value", "duration_seconds", "ip_address", "ip_country", "ip_city", "is_mobile",
               "referrer", "utm_source", "utm_medium", "utm_campaign"]]


def gen_campaigns(rng, n=60) -> pd.DataFrame:
    ctype = _choice(rng, ["Email", "SMS", "WhatsApp", "Push", "Mix", "Voice"], [0.335, 0.21, 0.185, 0.12, 0.105, 0.045], size=n)
    obj = _choice(rng, ["Retention", "Cross-sell", "Acquisition", "Reactivation", "Up-sell"], [0.31, 0.255, 0.19, 0.125, 0.12], size=n)
    prod = _null_out(rng, _choice(rng, ["Tarjeta Crédito", "Cuenta Ahorro", "Préstamo Personal", "Inversión", "Cuenta Corriente",
                                        "Préstamo Hipotecario", "Seguro"], [0.29, 0.22, 0.16, 0.11, 0.1, 0.06, 0.06], size=n), 0.11)
    start = _days(rng, n, date(2023, 7, 1), date(2026, 6, 14))
    end = start + rng.integers(30, 70, n).astype("timedelta64[D]")
    return pd.DataFrame({
        "campaign_id": [f"CMP-{3001 + i}" for i in range(n)],  # same prefix as complaints (ambiguous, as in the data)
        "campaign_name": [f"Campaña {o.lower()} {i + 1}" for i, o in enumerate(obj)],
        "description": _null_out(rng, np.array([f"Campaña de {o.lower()} para {p}" for o, p in zip(obj, prod)], dtype=object), 0.195),
        "campaign_type": ctype, "campaign_objective": obj, "promoted_product": prod,
        "target_segment": _null_out(rng, _choice(rng, ["Plus", "Premium", "Basic", "Student"], size=n), 0.395),
        "target_country": _null_out(rng, _choice(rng, ["Colombia", "Mexico", "Argentina"], size=n), 0.555),
        "start_date": _fmt_date(start), "end_date": _fmt_date(end),
        "budget": _null_out(rng, np.round(rng.uniform(6355.31, 499954, n), 2), 0.155),
        "campaign_status": _choice(rng, ["Completed", "Paused", "Active"], [0.86, 0.125, 0.015], size=n),
        "expected_conversion_rate": _null_out(rng, np.round(rng.uniform(0.55, 14.69, n), 2), 0.07),
    })


def gen_campaign_sends(rng, customers: pd.DataFrame, campaigns: pd.DataFrame, scale: float = 0.5) -> pd.DataFrame:
    total = int(1_746_801 * len(customers) / 150_000 * scale)
    starts = np.array(campaigns["start_date"].tolist(), dtype="datetime64[D]")
    ends = np.array(campaigns["end_date"].tolist(), dtype="datetime64[D]")
    camp_idx = rng.integers(0, len(campaigns), total)
    span = (ends[camp_idx] - starts[camp_idx]).astype(int) + 1
    day = starts[camp_idx] + (rng.random(total) * span).astype(int).astype("timedelta64[D]")
    keep = day <= np.datetime64(END) - np.timedelta64(2, "D")
    camp_idx, day = camp_idx[keep], day[keep]
    n = len(day)
    ts = _ts(rng, day, 8, 21)
    ctype = campaigns["campaign_type"].to_numpy()[camp_idx]
    channel = np.where(ctype == "Mix", _choice(rng, ["Email", "SMS", "WhatsApp", "Push", "Voice"], size=n), ctype).astype(object)
    status = _choice(rng, ["Sent", "Failed", "Bounced", "Blocked"], [0.9404, 0.0296, 0.0199, 0.0102], size=n)
    delivered = status == "Sent"
    open_p = np.select([channel == "SMS", channel == "Push", channel == "Email"], [0.5, 0.4, 0.3], 0.0)
    can_open = np.isin(channel, ["SMS", "Push", "Email"])
    opened = delivered & can_open & (rng.random(n) < open_p)
    clicked = opened & (rng.random(n) < 0.2)
    conv = clicked & (rng.random(n) < 0.1)
    open_ts = ts + rng.integers(60, 5 * 86400, n).astype("timedelta64[s]")
    click_ts = open_ts + rng.integers(5, 3600, n).astype("timedelta64[s]")
    conv_ts = click_ts + rng.integers(60, 2 * 86400, n).astype("timedelta64[s]")
    prod = campaigns["promoted_product"].to_numpy()[camp_idx]
    subject = np.where(channel == "Email", [f"¡Oferta especial en {p if p else 'nan'}!" for p in prod], None)  # "nan" defect
    return pd.DataFrame({
        "send_id": [f"SND-{900000001 + i}" for i in range(n)], "send_date": _fmt_ts(ts),
        "process_date": _fmt_date(ts.astype("datetime64[D]")),
        "campaign_id": campaigns["campaign_id"].to_numpy()[camp_idx],
        "customer_id": _choice(rng, customers["customer_id"].tolist(), size=n),  # consent not checked (defect)
        "send_channel": channel,
        "template_used": _null_out(rng, np.array([f"TPL-{x}" for x in rng.integers(100, 975, n)], dtype=object), 0.1005),
        "subject": subject, "send_status": status, "was_delivered": _bool(delivered),
        "was_opened": np.where(can_open & delivered, _bool(opened), None),
        "open_date": np.where(opened, _fmt_ts(open_ts), None), "was_clicked": _bool(clicked),
        "click_date": np.where(clicked, _fmt_ts(click_ts), None),
        "click_count": np.where(clicked, rng.integers(1, 6, n), None), "had_conversion": _bool(conv),
        "conversion_date": np.where(conv, _fmt_ts(conv_ts), None),
        "conversion_value": np.where(conv, np.round(rng.uniform(100.88, 4999.16, n), 2), None),
        "open_device": np.where(opened, _choice(rng, ["Tablet", "Desktop", "Mobile"], size=n), None),
        "open_country": np.where(opened, _choice(rng, ["México", "Colombia", "Argentina"], [0.5, 0.3, 0.2], size=n), None),
        "failure_reason": np.where(np.isin(status, ["Failed", "Bounced"]),
                                   _choice(rng, ["SMTP error", "Invalid email address", "User blocked sender"], [0.5, 0.33, 0.17], size=n), None),
        "send_cost": _null_out(rng, np.round(rng.uniform(0.0001, 0.3, n) ** 1.8, 4), 0.1503),
    })


# ---------------------------------------------------------------- writing

DAILY = {"transactions", "call_center_interactions", "call_transcripts", "complaints", "satisfaction_surveys",
         "digital_events", "campaign_sends"}


def _write_flat(df: pd.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return len(df)


def _write_daily(df: pd.DataFrame, table: str, raw_dir: Path) -> int:
    base = raw_dir / table
    for pdate, part in df.groupby("process_date", sort=True):
        y, m, d = pdate.split("-")
        folder = base / f"year={y}" / f"month={m}" / f"day={d}"
        folder.mkdir(parents=True, exist_ok=True)
        part.to_csv(folder / f"{table}_{y}{m}{d}.csv", index=False, encoding="utf-8-sig")
    return len(df)


def generate(customers: int = 4000, seed: int = 42, raw_dir: Path = RAW_DIR, verbose: bool = True,
             start: date | None = None) -> dict[str, int]:
    """Write the 13 tables under ``raw_dir``; returns rows per table. ``start`` shortens the period
    (tests use a few weeks); the cut-off stays 2026-06-17."""
    global START, N_DAYS
    START = start or date(2023, 6, 17)
    N_DAYS = (END - START).days + 1
    t0 = time.time()
    rng = np.random.default_rng(seed)
    if raw_dir.exists():
        marker = raw_dir / MARKER
        real_files = any(p for p in raw_dir.iterdir() if p.name != MARKER)
        if real_files and not marker.exists():
            raise SystemExit(f"{raw_dir} has files that are not a synthetic replica (no {MARKER}). "
                             "Refusing to overwrite what looks like the real dataset; move it first.")
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)
    # Marker first: a run that crashes halfway still leaves a folder recognised as a replica.
    (raw_dir / MARKER).write_text("SYNTHETIC REPLICA (generation in progress or interrupted)\n", encoding="utf-8")

    def log(msg):
        if verbose:
            print(f"[synth {time.time() - t0:5.1f}s] {msg}", flush=True)

    out: dict[str, int] = {}
    fx = gen_exchange_rates(rng)
    out["daily_exchange_rates"] = _write_flat(fx, raw_dir / "daily_exchange_rates.csv")
    br = gen_branches(rng)
    out["branches"] = _write_flat(br, raw_dir / "branches.csv")
    ag = gen_agents(rng, br)
    out["service_agents"] = _write_flat(ag, raw_dir / "service_agents.csv")
    cu = gen_customers(rng, customers)
    out["customers"] = _write_flat(cu, raw_dir / "customers.csv")
    pr = gen_products(rng, cu, br)
    out["products"] = _write_flat(pr, raw_dir / "products.csv")
    mc = gen_campaigns(rng)
    out["marketing_campaigns"] = _write_flat(mc, raw_dir / "marketing_campaigns.csv")
    log(f"dimensions: {out}")
    tx = gen_transactions(rng, pr, cu, br)
    out["transactions"] = _write_daily(tx, "transactions", raw_dir)
    log(f"transactions: {len(tx):,} (frauds {int((tx['is_fraud'] == 'True').sum())})")
    cc = gen_call_center(rng, cu, ag, pr)
    tr = gen_transcripts(rng, cc)
    sv = gen_surveys(rng, cc)
    out["call_center_interactions"] = _write_daily(cc.drop(columns=["_resolved", "_ts"]), "call_center_interactions", raw_dir)
    out["call_transcripts"] = _write_daily(tr, "call_transcripts", raw_dir)
    out["satisfaction_surveys"] = _write_daily(sv, "satisfaction_surveys", raw_dir)
    log(f"call center {len(cc):,}, transcripts {len(tr):,}, surveys {len(sv):,}")
    cp = gen_complaints(rng, cu, pr, ag, br)
    out["complaints"] = _write_daily(cp, "complaints", raw_dir)
    de = gen_digital_events(rng, cu, pr)
    out["digital_events"] = _write_daily(de, "digital_events", raw_dir)
    cs = gen_campaign_sends(rng, cu, mc)
    out["campaign_sends"] = _write_daily(cs, "campaign_sends", raw_dir)
    log(f"complaints {len(cp):,}, digital events {len(de):,}, campaign sends {len(cs):,}")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "SYNTHETIC REPLICA of the LATAM Bank dataset (not the organizers' data).",
        f"generator: src/etl/synth_dataset.py {GENERATOR_VERSION}",
        f"generated_at: {stamp}",
        f"seed: {seed}",
        f"customers: {customers}",
        f"period: {START} .. {END}",
        f"days: {N_DAYS}",
        f"fraud_rate: {FRAUD_RATE} (real data: 0.001)",
    ] + [f"rows.{k}: {v}" for k, v in sorted(out.items())]
    (raw_dir / MARKER).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("done")
    return out


def is_replica(raw_dir: Path = RAW_DIR) -> bool:
    return (raw_dir / MARKER).exists()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--customers", type=int, default=int(os.environ.get("SYNTH_CUSTOMERS", "4000")))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    ap.add_argument("--start", type=date.fromisoformat, default=None, help="first day (default 2023-06-17)")
    ap.add_argument("--no-load", action="store_true", help="only write the CSV files (skip the DuckDB load)")
    args = ap.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    generate(customers=args.customers, seed=args.seed, raw_dir=args.raw_dir, start=args.start)
    if not args.no_load:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from load_duckdb import load_raw_to_duckdb  # noqa: E402

        load_raw_to_duckdb(args.raw_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
