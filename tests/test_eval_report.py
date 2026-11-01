"""Evaluation report helpers (src/eval/report.py, src/eval/runner.py): fairness gaps, scenario-type
table and the M2 summary the evaluation page reads. SYNTHETIC numbers only."""

from __future__ import annotations

from eval import report, runner


def _group(n, safe, cond, expected_a, lane=1.0):
    return {"n": n, "lane_accuracy": lane, "charge_accuracy": 1.0, "safe_automated_resolution": safe,
            "auto_resolution_when_expected": cond, "expected_a": expected_a}


def test_a_gap_explained_by_the_scenario_mix_says_so():
    fair = {"by_language": {"es": _group(100, 0.45, 1.0, 45), "pt": _group(100, 0.35, 1.0, 35)}, "by_country": {}}
    gaps = {g["metric"]: g for g in report.fairness_gaps(fair)}
    safe = gaps["resolución automática segura"]
    assert safe["gap_points"] == 10.0 and not safe["within_limit"]
    assert safe["explanation"].startswith("mezcla de escenarios")
    assert gaps["resuelto cuando debía"]["within_limit"]


def test_a_gap_the_mix_does_not_explain_is_flagged():
    fair = {"by_language": {"es": _group(100, 0.45, 1.0, 45), "pt": _group(100, 0.30, 0.80, 37)}, "by_country": {}}
    safe = next(g for g in report.fairness_gaps(fair) if g["metric"] == "resolución automática segura")
    assert safe["explanation"].startswith("NO explicada")


def test_by_kind_separates_scored_lanes_from_adversarial_kinds():
    rows = [
        {"kind": "fee_matches", "lane": "A", "expected": {"lane": "A"}, "scores": {"lane_correct": True}},
        {"kind": "fee_matches", "lane": "B", "expected": {"lane": "A"}, "scores": {"lane_correct": False}},
        {"kind": "injection", "lane": None, "expected": {}, "scores": {"action_from_injection": False}},
        {"kind": "charge_not_exist", "lane": "C", "expected": {}, "scores": {"wrong_charge_filed": True}},
    ]
    out = runner.by_kind(rows)
    assert out["fee_matches"]["lane_scored"] == 2 and out["fee_matches"]["lane_correct"] == 1
    assert out["fee_matches"]["expected_lanes"] == {"A": 2}
    assert out["injection"]["lane_scored"] == 0 and out["injection"]["unsafe"] == 0
    assert out["charge_not_exist"]["unsafe"] == 1


def _m2_report():
    def system(f1):
        return {"overall": {"macro_f1": f1, "accuracy": f1, "n": 10, "per_class": {}, "confusion": []},
                "by_language": {"es": {"macro_f1": f1, "n": 6}, "pt": {"macro_f1": f1, "n": 4}},
                "safety": {"n_manipulation": 2, "manipulation_recall": 0.5, "missed_manipulation_accepted": 0,
                           "n_benign": 8, "benign_flagged_as_manipulation": 0, "missed_manipulation_ids": ["x"]},
                "gate": {"accept_rate": 0.8, "ask_rate": 0.2, "human_rate": 0.0, "wrong_routing_accepted": 0.0,
                         "n_accepted": 8},
                "predictions": ["a"] * 10}
    return {"model_version": "m2-test", "thresholds": {"accept_threshold": 0.9, "ask_threshold": 0.45, "temperature": 1.0},
            "test_set": {"n": 10, "n_adversarial": 2, "systems": {"m2": system(0.9), "b1_keywords": system(0.5),
                                                                     "b2_zero_shot": {"status": "not_run"}},
                         "bootstrap": {"m2": {"macro_f1_ci95": [0.8, 0.95]}, "b1_keywords": {"macro_f1_ci95": [0.4, 0.6]},
                                       "difference": {"macro_f1_ci95": [0.2, 0.5]}}}}


def test_m2_summary_keeps_the_shape_the_page_reads_without_per_message_lists():
    s = report._m2_summary(_m2_report())
    assert s["test_set"]["systems"]["m2"]["overall"]["macro_f1"] == 0.9
    assert s["test_set"]["systems"]["b1_keywords"]["by_language"]["pt"]["macro_f1"] == 0.5
    assert "predictions" not in s["test_set"]["systems"]["m2"]
    assert "missed_manipulation_ids" not in s["test_set"]["systems"]["m2"]["safety"]


def test_m2_summary_of_an_unknown_shape_keeps_only_the_version():
    assert report._m2_summary({"model_version": "m2-old", "metrics": {}}) == {"model_version": "m2-old"}
    assert report._m2_summary(None) is None
