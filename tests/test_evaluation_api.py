"""GET /evaluation (src/conversation/evaluation.py through src/handlers/api.py): the latest
`make eval` report for the evaluation page, without its rows. Public route; the report is
read from EVAL_REPORT_PATH (or the artifacts bucket) and cached per file mtime.

Every report here is a SYNTHETIC stand-in shaped like src/eval/report.py's output.
"""

from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest
import yaml

from conversation import contract as C
from conversation import evaluation
from handlers import api

from _support import call, v2_event

REPO = Path(__file__).resolve().parents[1]


def metric(value, n, k, lo, hi):
    return {"value": value, "n": n, "k": k, "ci95": [lo, hi]}


def synthetic_report(**over) -> dict:
    rep = {
        "version": "eval-test.1",
        "generated_at": "2026-10-30T04:37:02Z",
        "data_source": "synthetic_replica",
        "synthetic": True,
        "gold_snapshot": "2026-06-17",
        "seconds": 1.5,
        "scenarios": {"n": 4, "adversarial": 1, "by_kind": {"fee_matches": 2, "injection": 2},
                      "by_language": {"es": 2, "pt": 2}, "by_country": {"MX": 3, "AR": 1}, "seed": 7},
        "configs": [
            {"id": "v2", "label": "v2: M1 + M2 + G1 + G2", "env": {"M1_BACKEND": "model", "LLM_PROVIDER": "mock"}},
            {"id": "b1_rules_v14", "label": "B1: v1.4 solo reglas", "env": {"M1_BACKEND": "rule"}},
        ],
        "headline": [
            {"metric": "safe_automated_resolution", "label": "Resolución automática segura", "unit": "ratio",
             "target": ">= 30% con 0 cierres incorrectos", "v2": metric(0.5, 4, 2, 0.25, 0.75),
             "b1": metric(0.25, 4, 1, 0.0, 0.5), "pass": True},
            {"metric": "lane_accuracy", "label": "Ruta correcta (A/B/C)", "unit": "ratio", "target": "—",
             "v2": metric(1.0, 3, 3, 1.0, 1.0), "b1": None, "pass": None},
        ],
        "latency": {"turn_p50_ms": 17.4, "turn_p95_ms": 72.4, "turns": 12, "investigator_p50_ms": 56,
                    "investigator_p95_ms": 77.9, "investigations": 2, "target": "turno p95 < 6 s",
                    "note": "medido en local"},
        "cost": {"per_conversation_usd": 0.0, "target": "< 0.05 USD", "note": "estimado",
                 "projected": {"chat_model": "claude-chat-test", "investigator_model": "claude-inv-test",
                               "chat_per_conversation_usd": 0.0041, "investigator_per_case_usd": 0.0312,
                               "share_of_conversations_investigated": 0.25, "total_per_conversation_usd": 0.0119,
                               "note": "PROYECCIÓN con precios de lista"}},
        "fairness": {"by_language": {"es": {"n": 2, "lane_accuracy": 1.0, "charge_accuracy": 1.0,
                                            "safe_automated_resolution": 0.5, "auto_resolution_when_expected": 1.0,
                                            "expected_a": 1, "unsafe_wrong_charge_filed": 0.0}},
                     "by_country": {"MX": {"n": 3, "lane_accuracy": 1.0, "charge_accuracy": None,
                                           "safe_automated_resolution": 0.33, "unsafe_wrong_charge_filed": 0.0}},
                     "target": "brechas <= 5 puntos",
                     "gaps": [{"dimension": "idioma", "metric": "resolución automática segura", "gap_points": 8.6,
                               "max": "es", "min": "pt", "within_limit": False,
                               "explanation": "mezcla de escenarios"},
                              {"dimension": "país", "metric": "ruta correcta", "gap_points": 0.0, "max": "AR",
                               "min": "MX", "within_limit": True}]},
        "by_kind": {"v2": {"fee_matches": {"n": 2, "expected_lane": "A", "lanes": {"A": 2}}}},
        "security": {"v2": {"tool_scope": {"n": 8, "leaks": 0}, "expired_session": {"n": 3, "data_shown": 0},
                            "other_session_case": {"n": 1, "leaks": 0},
                            "tool_failure": {"lane": "C", "degraded": ["tool_unavailable"], "safe": True}},
                     "b1": None},
        "models": {"m1": {"model_version": "m1-test", "primary": "model", "m1": {"top1": 0.75}, "rule": {"top1": 0.71}},
                   "m2": None,
                   "m3": {"model_version": "m3-test", "lift_significant": False, "decision": "evidence_only"},
                   "g1": {"status": "mock", "note": "fixtures"}, "g2": {"status": "policy", "note": "política"},
                   "g3": {"status": "not_run", "note": "sin modelo"}, "b2_zero_shot": {"status": "not_run", "note": "x"}},
        "baseline_b0": [{"metric": "median_resolution_days", "value": 15.0, "unit": "days", "n": 431,
                         "source": "complaints", "description": "Mediana de días hasta resolver"}],
        "limits": ["Datos: réplica sintética del dataset.", "El portugués es generado."],
        "rows": {"v2": [{"id": "SC-1"}, {"id": "SC-2"}, {"id": "SC-3"}], "b1": [{"id": "SC-1"}]},
    }
    rep.update(over)
    return rep


def write(path: Path, report) -> Path:
    path.write_text(yaml.safe_dump(report, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("BUCKET_ARTIFACTS", raising=False)
    monkeypatch.setenv("EVAL_REPORT_PATH", str(tmp_path / "missing.yaml"))
    evaluation.clear_cache()
    yield
    evaluation.clear_cache()


def get_evaluation():
    status, body, headers = call(v2_event("GET /evaluation"))
    assert status == 200, body
    assert headers["cache-control"] == "no-store" and headers["content-type"].startswith("application/json")
    C.EvaluationResponse.model_validate(body)  # the body follows the contract (types.ts mirror)
    return body


def use(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("EVAL_REPORT_PATH", str(path))


# ---------------------------------------------------------------- the route

def test_serves_the_report_without_its_rows(monkeypatch, tmp_path):
    use(monkeypatch, write(tmp_path / "report.yaml", synthetic_report()))
    body = get_evaluation()
    assert body["available"] is True and body["synthetic"] is True
    rep = body["report"]
    assert list(rep) == list(C.EvaluationReport.model_fields)  # every key, in the contract's order
    assert "rows" not in rep and rep["row_counts"] == {"v2": 3, "b1": 1}
    assert rep["version"] == "eval-test.1" and rep["data_source"] == "synthetic_replica"
    first = rep["headline"][0]
    assert first == {"metric": "safe_automated_resolution", "label": "Resolución automática segura", "unit": "ratio",
                     "target": ">= 30% con 0 cierres incorrectos",
                     "v2": {"value": 0.5, "n": 4, "k": 2, "ci95": [0.25, 0.75]},
                     "b1": {"value": 0.25, "n": 4, "k": 1, "ci95": [0.0, 0.5]}, "pass": True}
    assert rep["headline"][1]["b1"] is None and rep["headline"][1]["pass"] is None
    assert rep["security"]["v2"]["tool_failure"] == {"lane": "C", "degraded": ["tool_unavailable"], "safe": True}
    assert rep["security"]["b1"] is None and rep["models"]["m2"] is None
    assert rep["models"]["m1"]["m1"] == {"top1": 0.75} and rep["models"]["g2"] == {"status": "policy", "note": "política"}
    assert rep["fairness"]["by_country"]["MX"]["charge_accuracy"] is None
    # Fields added to the report on 29 oct: fairness gaps, the fair rate per group, the projected cost.
    assert rep["fairness"]["by_language"]["es"]["auto_resolution_when_expected"] == 1.0
    assert rep["fairness"]["by_language"]["es"]["expected_a"] == 1
    assert rep["fairness"]["by_country"]["MX"]["expected_a"] is None  # an older group: null, not an error
    assert rep["fairness"]["gaps"] == [
        {"dimension": "idioma", "metric": "resolución automática segura", "gap_points": 8.6, "max": "es", "min": "pt",
         "within_limit": False, "explanation": "mezcla de escenarios"},
        {"dimension": "país", "metric": "ruta correcta", "gap_points": 0.0, "max": "AR", "min": "MX",
         "within_limit": True, "explanation": None},
    ]
    assert rep["cost"]["projected"]["total_per_conversation_usd"] == 0.0119
    assert rep["cost"]["projected"]["chat_model"] == "claude-chat-test"
    assert rep["limits"] == ["Datos: réplica sintética del dataset.", "El portugués es generado."]
    assert "SC-1" not in json.dumps(body)


def test_is_public_and_does_not_build_the_app(monkeypatch, tmp_path):
    use(monkeypatch, write(tmp_path / "report.yaml", synthetic_report()))

    def boom():
        raise AssertionError("GET /evaluation must not build the chat app")

    monkeypatch.setattr(api, "get_app", boom)
    event = v2_event("GET /evaluation")
    assert "authorization" not in event["headers"]
    assert get_evaluation()["available"] is True


def test_without_a_report_it_is_not_available_but_still_200():
    assert get_evaluation() == {"available": False, "report": None, "synthetic": True}


@pytest.mark.parametrize("content", [
    b"version: [unclosed",  # not YAML
    b"- a list\n- not a mapping\n",
    b"just text",
    b"",
    b"\xff\xfe\x00 not utf-8",
])
def test_an_unreadable_report_is_not_available(monkeypatch, tmp_path, content):
    path = tmp_path / "report.yaml"
    path.write_bytes(content)
    use(monkeypatch, path)
    assert get_evaluation() == {"available": False, "report": None, "synthetic": True}


def test_a_report_that_is_too_big_is_not_read(monkeypatch, tmp_path):
    use(monkeypatch, write(tmp_path / "report.yaml", synthetic_report()))
    monkeypatch.setattr(evaluation, "MAX_REPORT_BYTES", 100)
    assert get_evaluation()["available"] is False


def test_a_yaml_alias_bomb_is_cut(monkeypatch, tmp_path):
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 8):
        refs = ", ".join([f"*a{i - 1}"] * 10)
        lines.append(f"a{i}: &a{i} [{refs}]")
    lines.append("headline: *a7")  # 10**8 values once expanded
    path = tmp_path / "report.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    use(monkeypatch, path)
    assert get_evaluation()["available"] is False


def test_the_default_path_is_the_repo_report(monkeypatch):
    monkeypatch.delenv("EVAL_REPORT_PATH")
    assert evaluation.report_path() == REPO / "data" / "eval" / "report_latest.yaml"


# ---------------------------------------------------------------- lenient report pieces

def test_missing_and_mistyped_values_read_as_null(monkeypatch, tmp_path):
    rep = synthetic_report(
        seconds="mucho",  # not a number
        latency={"turn_p50_ms": "n/a", "turn_p95_ms": float("nan"), "turns": True, "investigator_p50_ms": float("inf"),
                 "note": "medido en local", "new_key": 1},  # three keys missing, one unknown
        cost=["not", "a", "mapping"],
        gold_snapshot=date(2026, 6, 17),  # an unquoted date in the YAML
        headline=[{"metric": "incorrect_closures", "target": 0, "v2": {"value": 0.0, "n": 5, "k": 0, "ci95": [0.0, None]},
                   "pass": True}],
        models={"m1": "not a mapping", "m2": {"model_version": "m2-any-shape", "metrics": {"x": [1, 2]}}, "g9": {}},
        limits=["uno", 2],
        unexpected_top_level={"x": 1},
    )
    use(monkeypatch, write(tmp_path / "report.yaml", rep))
    out = get_evaluation()["report"]
    assert out["seconds"] is None and out["cost"] is None and out["gold_snapshot"] == "2026-06-17"
    assert out["latency"] == {"turn_p50_ms": None, "turn_p95_ms": None, "turns": None, "investigator_p50_ms": None,
                              "investigator_p95_ms": None, "investigations": None, "target": None,
                              "note": "medido en local"}
    h = out["headline"][0]
    assert h["target"] == "0" and h["label"] is None and h["b1"] is None and h["pass"] is True
    assert h["v2"] == {"value": 0.0, "n": 5, "k": 0, "ci95": None}  # a null inside the interval drops the interval
    assert out["models"]["m1"] is None and out["models"]["m2"] == {"model_version": "m2-any-shape", "metrics": {"x": [1, 2]}}
    assert "g9" not in out["models"] and "unexpected_top_level" not in out
    assert out["limits"] == ["uno", "2"]


def test_json_body_is_strict_json(monkeypatch, tmp_path):
    rep = synthetic_report(models={"m2": {"ece": float("nan"), "weird": {None: 1, True: 2, 3: [float("-inf")]}}})
    use(monkeypatch, write(tmp_path / "report.yaml", rep))
    raw = api.handler(v2_event("GET /evaluation"), None)["body"]
    body = json.loads(raw, parse_constant=lambda c: pytest.fail(f"{c} is not JSON"))
    assert body["report"]["models"]["m2"] == {"ece": None, "weird": {"null": 1, "true": 2, "3": [None]}}


def test_per_item_lists_in_the_model_summaries_are_not_served(monkeypatch, tmp_path):
    # M2's report carries its 180 test predictions per system (~46 KB): like rows, they stay.
    preds = [{"id": f"t{i:03d}", "label": "dispute_charge", "predicted": "dispute_charge", "p": 0.9} for i in range(180)]
    rep = synthetic_report()
    rep["models"]["m2"] = {"model_version": "m2-test", "test_set": {"n": 180, "systems": {
        "m2": {"overall": {"macro_f1": 0.93}, "predictions": preds,
               "calibration": {"reliability_calibrated": [{"bin": f"b{i}", "n": i} for i in range(7)]}},
        "b1_keywords": {"overall": {"macro_f1": 0.5}, "predictions": preds},
    }}}
    use(monkeypatch, write(tmp_path / "report.yaml", rep))
    m2 = get_evaluation()["report"]["models"]["m2"]
    systems = m2["test_set"]["systems"]
    assert "predictions" not in systems["m2"] and "predictions" not in systems["b1_keywords"]
    assert systems["m2"]["overall"] == {"macro_f1": 0.93} and systems["b1_keywords"]["overall"] == {"macro_f1": 0.5}
    assert len(systems["m2"]["calibration"]["reliability_calibrated"]) == 7 and m2["test_set"]["n"] == 180


def test_secret_looking_env_values_are_never_served(monkeypatch, tmp_path):
    rep = synthetic_report(configs=[{"id": "v2", "label": "v2", "env": {
        "LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-synthetic", "AWS_SECRET_ACCESS_KEY": "x",
        "SESSION_TOKEN": "t", "DB_PASSWORD": "p", "M1_BACKEND": "model"}}])
    use(monkeypatch, write(tmp_path / "report.yaml", rep))
    env = get_evaluation()["report"]["configs"][0]["env"]
    assert env == {"LLM_PROVIDER": "anthropic", "M1_BACKEND": "model"}


# ---------------------------------------------------------------- cache

def test_cached_per_file_mtime_and_reloaded_after_make_eval(monkeypatch, tmp_path):
    path = write(tmp_path / "report.yaml", synthetic_report())
    use(monkeypatch, path)
    parses = []
    real_parse = evaluation._parse
    monkeypatch.setattr(evaluation, "_parse", lambda raw: parses.append(1) or real_parse(raw))
    assert get_evaluation()["report"]["version"] == "eval-test.1"
    assert get_evaluation()["report"]["version"] == "eval-test.1"
    assert len(parses) == 1
    write(path, synthetic_report(version="eval-test.2"))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert get_evaluation()["report"]["version"] == "eval-test.2" and len(parses) == 2
    path.unlink()
    assert get_evaluation()["available"] is False


def test_concurrent_requests_read_the_file_once(monkeypatch, tmp_path):
    use(monkeypatch, write(tmp_path / "report.yaml", synthetic_report()))
    reads = []
    real_read = evaluation._read_file
    monkeypatch.setattr(evaluation, "_read_file", lambda p, s: reads.append(1) or real_read(p, s))
    results = []
    threads = [threading.Thread(target=lambda: results.append(evaluation.evaluation_response())) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(results) == 8 and all(r["available"] for r in results) and len(reads) == 1


# ---------------------------------------------------------------- S3 fallback (the Lambda)

class FakeS3:
    def __init__(self, data: bytes | None = None, error: Exception | None = None):
        self.data, self.error, self.calls = data, error, []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        if self.error:
            raise self.error
        return {"Body": io.BytesIO(self.data), "ContentLength": len(self.data)}


def test_without_the_file_it_reads_the_artifacts_bucket(monkeypatch):
    s3 = FakeS3(yaml.safe_dump(synthetic_report(), allow_unicode=True).encode("utf-8"))
    monkeypatch.setattr(evaluation, "_s3_client", lambda: s3)
    monkeypatch.setenv("BUCKET_ARTIFACTS", "expvivo-dev-artifacts")
    body = get_evaluation()
    assert body["available"] is True and body["report"]["row_counts"] == {"v2": 3, "b1": 1}
    assert s3.calls == [("expvivo-dev-artifacts", "eval/report_latest.yaml")]


def test_the_bucket_copy_is_cached_for_a_while(monkeypatch):
    s3 = FakeS3(yaml.safe_dump(synthetic_report()).encode("utf-8"))
    monkeypatch.setattr(evaluation, "_s3_client", lambda: s3)
    monkeypatch.setenv("BUCKET_ARTIFACTS", "bucket")
    now = [1000.0]
    assert evaluation.evaluation_response(clock=lambda: now[0])["available"] is True
    now[0] += evaluation.S3_TTL_SECONDS - 1
    evaluation.evaluation_response(clock=lambda: now[0])
    assert len(s3.calls) == 1
    now[0] += 2
    evaluation.evaluation_response(clock=lambda: now[0])
    assert len(s3.calls) == 2


@pytest.mark.parametrize("make_client", [
    lambda: FakeS3(error=RuntimeError("NoSuchKey")),
    lambda: FakeS3(b"version: [unclosed"),
    lambda: (_ for _ in ()).throw(ImportError("no boto3")),
])
def test_a_bucket_failure_is_not_available_never_a_crash(monkeypatch, make_client):
    monkeypatch.setattr(evaluation, "_s3_client", make_client)
    monkeypatch.setenv("BUCKET_ARTIFACTS", "bucket")
    assert get_evaluation() == {"available": False, "report": None, "synthetic": True}


def test_the_bucket_is_not_used_when_the_file_exists(monkeypatch, tmp_path):
    use(monkeypatch, write(tmp_path / "report.yaml", synthetic_report()))
    monkeypatch.setattr(evaluation, "_s3_client", lambda: pytest.fail("S3 read with a local report"))
    monkeypatch.setenv("BUCKET_ARTIFACTS", "bucket")
    assert get_evaluation()["available"] is True


# ---------------------------------------------------------------- the real report and the deps

def test_the_report_written_by_make_eval_matches_the_contract(monkeypatch):
    path = REPO / "data" / "eval" / "report_latest.yaml"
    if not path.exists():
        pytest.skip("data/eval/report_latest.yaml not generated (make eval)")
    monkeypatch.delenv("EVAL_REPORT_PATH")
    body = get_evaluation()
    assert body["available"] is True
    rep = body["report"]
    assert rep["headline"] and all(h["metric"] for h in rep["headline"])
    assert rep["row_counts"] and "rows" not in rep
    assert len(json.dumps(body, ensure_ascii=False)) < 100_000  # the rows and per-item lists stay on the server

    def long_lists(value, path="models"):
        if isinstance(value, dict):
            return [p for k, v in value.items() for p in long_lists(v, f"{path}.{k}")]
        if isinstance(value, list):
            return ([path] if len(value) > evaluation.MAX_MODEL_LIST else []) + [
                p for i, v in enumerate(value) for p in long_lists(v, f"{path}[{i}]")]
        return []

    assert long_lists(rep["models"]) == []
    for h in rep["headline"]:
        for side in ("v2", "b1"):
            m = h[side]
            assert m is None or m["value"] is None or (0 <= m["value"] <= 1 and not math.isnan(m["value"]))


def test_missing_dependencies_are_a_500_not_a_crash():
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "sys.modules['pydantic'] = None; sys.modules['yaml'] = None;"
        "import handlers.api as a;"
        "r = a.handler({'routeKey': 'GET /evaluation'}, None); print(r['statusCode'], r['body'])"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    line = out.stdout.strip().splitlines()[0]
    assert line.startswith("500") and '"code": "internal"' in line, out


def test_local_api_resolves_and_serves_it(monkeypatch, tmp_path):
    import importlib.util
    import urllib.request

    spec = importlib.util.spec_from_file_location("local_api_eval", REPO / "scripts" / "local_api.py")
    local_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(local_api)
    assert local_api.resolve_route("GET", "/evaluation") == ("GET /evaluation", {})
    assert local_api.resolve_route("POST", "/evaluation") is None
    use(monkeypatch, write(tmp_path / "report.yaml", synthetic_report()))
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>EV</title>", encoding="utf-8")
    srv = local_api.make_server("127.0.0.1", 0, dist)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/evaluation"
        with urllib.request.urlopen(url, timeout=10) as r:  # no Authorization header
            assert r.status == 200
            body = json.loads(r.read().decode("utf-8"))
    finally:
        srv.shutdown()
        srv.server_close()
    assert body["available"] is True and body["report"]["version"] == "eval-test.1"
