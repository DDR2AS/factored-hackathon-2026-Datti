"""M1 runtime (pure Python, same contract as the v1.4 rule), M3 evidence, thresholds file."""

from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from gateway.types import Baseline, DigitalSession, Txn
from models import m1
from models.common import LgbmModel, read_thresholds
from models.m1 import features

REPO = Path(__file__).resolve().parents[1]


def _txn(i, amount, day, merchant, cat="food", last4="4821", ttype="purchase", status="approved", hhmm="12:00"):
    return Txn(txn_id=f"T{i}", product_id="P1", card_last4=last4, local_date=date(2026, 6, day), local_time=hhmm,
               amount=Decimal(amount), currency="MXN", amount_usd=None, merchant_id=f"M-{merchant.upper().replace(' ', '')}",
               merchant_name=merchant, merchant_category=cat, status=status, response_code="00", fraud_score=3.0,
               txn_type=ttype)


CANDS = [_txn(1, "449.90", 12, "SUPER AHORRO SA"), _txn(2, "452.00", 10, "SUPERCITO EXPRESS"),
         _txn(3, "450.00", 5, "SUPER AHORRO SA"), _txn(4, "45.00", 12, "OXXO 1123", cat="convenience")]


def test_artifacts_exist_and_are_not_json():
    art = m1.artifact_dir()
    if art is None:
        pytest.skip("M1 not trained")
    files = {p.name for p in art.iterdir()}
    assert {"model.txt", "confidence.txt", "calibration.yaml"} <= files
    assert not any(n.endswith(".json") for n in files)


def test_rank_candidates_contract():
    if not m1.available():
        pytest.skip("M1 not trained")
    out = m1.rank_candidates({"amount": "450", "date": "2026-06-12", "merchant_text": "super"}, CANDS)
    assert set(out) == {"ranked", "band", "model_version"}
    assert out["band"] in ("confirm", "choose", "ask")
    ps = [r["p"] for r in out["ranked"]]
    assert ps == sorted(ps, reverse=True) and sum(ps) <= 1.0 + 1e-9
    assert {r["txn_id"] for r in out["ranked"]} == {c.txn_id for c in CANDS}
    assert out["ranked"][0]["txn_id"] == "T1"
    assert m1.rank_candidates({"amount": "450"}, []) == {"ranked": [], "band": "ask", "model_version": out["model_version"]}


def test_model_beats_nothing_when_the_charge_is_absent():
    if not m1.available():
        pytest.skip("M1 not trained")
    # the customer describes 406.77 at an online store; only two small fees are candidates
    fees = [_txn(10, "2.50", 1, "COMISION MANTENIMIENTO CUENTA", cat="bank_fee", ttype="fee"),
            _txn(11, "9.90", 1, "CUOTA DE MANEJO MENSUAL", cat="bank_fee", ttype="fee")]
    out = m1.rank_candidates({"amount": "406.77", "date": "2026-06-02", "merchant_text": "una tienda online"}, fees)
    assert out["band"] != "confirm", out


def test_pure_python_evaluator_matches_lightgbm():
    lgb = pytest.importorskip("lightgbm")
    art = m1.artifact_dir()
    if art is None:
        pytest.skip("M1 not trained")
    booster = lgb.Booster(model_file=str(art / "model.txt"))
    pure = LgbmModel.load(art / "model.txt")
    rows = features.build({"amount": "450", "date": "2026-06-12", "merchant_text": "super"}, CANDS)
    import numpy as np

    ref = booster.predict(np.array(rows, dtype=float))
    ours = [pure.predict(r) for r in rows]
    assert max(abs(a - b) for a, b in zip(ref, ours)) < 1e-9


def test_runtime_does_not_import_numpy_or_lightgbm():
    code = ("import sys; sys.path.insert(0, 'src'); from models import m1; "
            "m1.rank_candidates({'amount': '450'}, []); "
            "print(any(m in sys.modules for m in ('numpy', 'lightgbm', 'sklearn')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "False", out


def test_thresholds_file_has_m1_and_m3():
    thr = read_thresholds()
    if "m1" not in thr:
        pytest.skip("not trained")
    assert 0 < thr["m1"]["choose_threshold"] < thr["m1"]["confirm_threshold"] <= 1
    assert thr["m1"]["primary"] in ("model", "rule")
    if "m3" in thr:
        assert thr["m3"]["decision"] in ("evidence_only", "lane_c_trigger_candidate")


def test_features_are_deterministic_and_handle_missing_slots():
    a = features.build({}, CANDS)
    b = features.build({}, CANDS)
    assert len(a) == 4 and all(len(r) == len(features.FEATURES) for r in a)
    assert str(a) == str(b)
    assert features.merchant_similarity("super", "SUPER AHORRO SA") == 1.0
    assert "food" in features.said_families("el súper de la esquina")


def test_m3_risk_features():
    from models.m3 import risk_features

    base = Baseline(window_from=date(2026, 1, 1), window_to=date(2026, 6, 1), n_txns=20, currency="MXN",
                    median_amount=Decimal("400"), p90_amount=Decimal("800"), top_categories=("food",),
                    top_merchants=("SUPER AHORRO SA",), usual_hours=(8, 20), median_amount_usd=25.0,
                    p95_amount_usd=60.0, known_merchant_ids=("M-SUPERAHORROSA",), usual_channels=("POS",),
                    usual_countries=("MX",), category_stats=(("food", 15, 20.0, 50.0),))
    t = _txn(1, "4499.90", 12, "TIENDA NUEVA", hhmm="03:10")
    t = Txn(**{**t.__dict__, "amount_usd": Decimal("250.00")})
    sess = [DigitalSession("S1", datetime(2026, 6, 12, 2, 55), datetime(2026, 6, 12, 3, 5), "Android App", "Android",
                           "MX", 5, True, False, True)]
    out = risk_features(t, base, recent_txns=[], sessions=sess, channel="Web", txn_country="BR", customer_country="MX")
    f = out["features"]
    assert f["first_time_merchant"] is True and f["unusual_hour"] is True and f["foreign"] is True
    assert f["amount_vs_cat_p95"] == 5.0 and f["login_before_30m"] and f["new_device_before"]
    assert out["lift_score"] is None
