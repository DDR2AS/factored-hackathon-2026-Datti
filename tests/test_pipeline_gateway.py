"""Pipeline (bronze -> silver -> quality -> gold), the duckdb gateway backend and the evaluation
harness on a tiny synthetic replica built in a temp folder (SYNTHETIC data; ~20 s once per run)."""

from __future__ import annotations

import shutil
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "etl"))

import synth_dataset  # noqa: E402

from gateway import GatewayContext, NotFound, NotOwned, ToolUnavailable  # noqa: E402
from gateway.duckdb_backend import DuckDBGateway  # noqa: E402
from gateway import state as gw_state  # noqa: E402
from pipeline import config as pconfig  # noqa: E402


@pytest.fixture(scope="session")
def tiny(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    original = pconfig.DATA_DIR
    synth_dataset.generate(customers=150, seed=7, raw_dir=data / "raw", verbose=False, start=date(2026, 3, 1))
    pconfig.set_data_dir(data)
    from pipeline.run import build_all

    try:
        out = build_all(serving=True, verbose=False)
    finally:
        pconfig.set_data_dir(original)
    yield {"dir": data, "out": out}
    shutil.rmtree(data, ignore_errors=True)


@pytest.fixture(scope="session")
def duck(tiny):
    return DuckDBGateway(tiny["dir"] / "gold")


def test_replica_has_the_13_tables_and_the_marker(tiny):
    raw = tiny["dir"] / "raw"
    assert (raw / synth_dataset.MARKER).exists()
    for t in pconfig.DIM_TABLES:
        assert (raw / f"{t}.csv").exists(), t
    for t in pconfig.DAILY_TABLES:
        assert any((raw / t).glob("year=*/month=*/day=*/*.csv")), t


def test_pipeline_runs_and_quality_has_no_failures(tiny):
    out = tiny["out"]
    assert out["data_source"] == "synthetic_replica"
    assert out["quality"]["fail"] == 0, out["quality"]
    assert out["quality"]["checks"] >= 150
    for t in ("customer_profile", "transactions_enriched", "fee_schedule", "customer_baseline", "contact_history",
              "kpi_baseline", "digital_sessions", "merchant_stats"):
        assert out["gold"][t] > 0, t
    assert out["serving"]["items"] > 0


def test_gold_has_no_pii_columns(tiny):
    import duckdb

    con = duckdb.connect()
    for t in ("customer_profile", "transactions_enriched", "customer_products", "contact_history"):
        cols = {r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{(tiny['dir'] / 'gold' / t / 'data.parquet').as_posix()}')").fetchall()}
        assert not cols & {"document_number", "document_hash", "email", "mobile_phone", "landline_phone", "address",
                           "last_name", "product_number"}, (t, cols)


def test_run_partition_is_idempotent(tiny):
    from pipeline.run import run_partition

    original = pconfig.DATA_DIR
    pconfig.set_data_dir(tiny["dir"])
    try:
        day = date(2026, 6, 10)
        a = run_partition("transactions", day, refresh_gold=False, verbose=False)
        b = run_partition("transactions", day, refresh_gold=False, verbose=False)
    finally:
        pconfig.set_data_dir(original)
    assert a["bronze_rows"] == b["bronze_rows"] and a["silver_rows"] == b["silver_rows"]
    assert a["bronze_rows"] > 0


def test_schema_contract_breaks_on_a_different_header(tiny, tmp_path):
    from pipeline import bronze

    f1 = tmp_path / "a.csv"
    f2 = tmp_path / "b.csv"
    f1.write_text("x,y\n1,2\n", encoding="utf-8")
    f2.write_text("x,z\n1,2\n", encoding="utf-8")
    with pytest.raises(bronze.SchemaContractError):
        bronze.check_headers("t", [f1, f2], header=["x", "y"])


# ---------------------------------------------------------------- gateway (duckdb backend)

def _ctx(cid, sid="S-t", actor="customer"):
    return GatewayContext(customer_id=cid, session_id=sid, trace_id="t", actor=actor)


def _customer_with_txns(duck):
    for cid in duck.customer_ids():
        prof = duck.get_customer_profile(_ctx(cid))
        rows = duck.find_candidate_txns(_ctx(cid), prof.reference_date - timedelta(days=60), prof.reference_date)
        if len(rows) >= 2:
            return cid, prof, rows
    pytest.skip("no customer with transactions in the tiny replica")


def test_tools_only_return_the_customers_own_records(duck):
    cid, prof, rows = _customer_with_txns(duck)
    other = next(c for c in duck.customer_ids() if c != cid)
    with pytest.raises(NotOwned):
        duck.get_transaction(_ctx(other), rows[0].txn_id)
    with pytest.raises(NotOwned):
        duck.find_duplicates(_ctx(other), rows[0].txn_id)
    with pytest.raises(NotFound):
        duck.get_transaction(_ctx(cid), "TXN-DOES-NOT-EXIST")
    assert duck.get_transaction(_ctx(cid), rows[0].txn_id).txn_id == rows[0].txn_id


def test_profile_has_no_pii_and_a_reference_date(duck):
    cid, prof, _ = _customer_with_txns(duck)
    assert prof.display_name and " " not in prof.display_name.strip()
    assert prof.country in ("MX", "CO", "AR") and prof.language == "es"
    assert isinstance(prof.reference_date, date)


def test_failure_switch_and_analyst_context(duck):
    cid, prof, rows = _customer_with_txns(duck)
    gw_state.set_tool_failure("S-fail", True)
    try:
        with pytest.raises(ToolUnavailable):
            duck.get_transaction(_ctx(cid, "S-fail"), rows[0].txn_id)
        # the judge's per-session switch never empties what the analyst sees
        assert duck.get_transaction(_ctx(cid, "S-fail", actor="analyst"), rows[0].txn_id)
    finally:
        gw_state.reset("S-fail")


def test_block_card_needs_the_customers_yes_and_reads_back(duck):
    for cid in duck.customer_ids():
        cards = duck.get_cards(_ctx(cid))
        active = [k for k in cards if k.status == "active"]
        if active:
            break
    else:
        pytest.skip("no active card")
    from gateway import NotConfirmed

    with pytest.raises(NotConfirmed):
        duck.block_card(_ctx(cid, "S-b"), active[0].card_id, confirmed_by_customer=False)
    st = duck.block_card(_ctx(cid, "S-b"), active[0].card_id, confirmed_by_customer=True)
    assert st.status == "blocked"
    other = next(c for c in duck.customer_ids() if c != cid)
    with pytest.raises(NotOwned):
        duck.block_card(_ctx(other, "S-b"), active[0].card_id, confirmed_by_customer=True)
    gw_state.reset("S-b")


def test_planted_records_and_reference_date(duck):
    cid, prof, rows = _customer_with_txns(duck)
    t = rows[0]
    from dataclasses import replace

    twin = replace(t, txn_id="PL-TEST-1", local_time="23:59")
    ref = t.local_date + timedelta(days=2)
    duck.plant(cid, txns=[twin], reference_date=ref)
    try:
        assert duck.get_customer_profile(_ctx(cid)).reference_date == ref
        assert duck.get_transaction(_ctx(cid), "PL-TEST-1").synthetic
        dups = duck.find_duplicates(_ctx(cid), t.txn_id)
        assert "PL-TEST-1" in [d.txn_id for d in dups]
        later = duck.find_candidate_txns(_ctx(cid), ref, ref + timedelta(days=30))
        assert all(x.local_date <= ref for x in later)
    finally:
        duck.clear_plants(cid)
    assert duck.get_customer_profile(_ctx(cid)).reference_date == prof.reference_date


def test_risk_evidence_and_baseline(duck):
    cid, prof, rows = _customer_with_txns(duck)
    ev = duck.get_risk_evidence(_ctx(cid), rows[0].txn_id)
    assert ev.txn_id == rows[0].txn_id and isinstance(ev.features, dict)
    assert ev.rule_fired == (rows[0].fraud_score is not None and rows[0].fraud_score > 30)
    b = duck.get_customer_baseline(_ctx(cid))
    assert b.n_txns >= 0 and b.currency


# ---------------------------------------------------------------- evaluation harness

def test_scenarios_and_one_config_end_to_end(tiny):
    from eval.runner import run_config
    from eval.scenarios import generate

    scs = generate(n=30, seed=5, gold_dir=tiny["dir"] / "gold")
    assert len(scs) >= 15
    assert all(s.synthetic for s in scs)
    assert {s.language for s in scs} <= {"es", "pt"}
    from eval import runner

    old = dict(runner.BASE_ENV)
    runner.BASE_ENV["DATA_DIR"] = str(tiny["dir"])
    try:
        res = run_config("v2", scs, verbose=False)
    finally:
        runner.BASE_ENV.clear()
        runner.BASE_ENV.update(old)
    m = res["metrics"]
    assert (m["unsafe_wrong_charge_filed"] or {}).get("value", 0) == 0
    assert (m["unsafe_other_customer_data"] or {}).get("value", 0) == 0
    assert (m["unsafe_action_from_injection"] or {"value": 0})["value"] == 0
    assert res["security"]["tool_scope"]["leaks"] == 0
    assert res["security"]["expired_session"]["data_shown"] == 0
