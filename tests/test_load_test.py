"""scripts/load_test.py without network: percentiles, aggregation, argument parser and the copy of
the judge-mode suggestions (frontend/src/demo/customers.ts)."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("load_test", REPO / "scripts" / "load_test.py")
lt = importlib.util.module_from_spec(SPEC)
sys.modules["load_test"] = lt  # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(lt)


# ---------------------------------------------------------------- percentiles

def test_percentile_interpolates_between_closest_ranks():
    v = [1, 2, 3, 4]
    assert lt.percentile(v, 50) == 2.5
    assert lt.percentile(v, 0) == 1
    assert lt.percentile(v, 100) == 4
    assert lt.percentile(range(1, 101), 95) == pytest.approx(95.05)
    assert lt.percentile(range(1, 101), 99) == pytest.approx(99.01)


def test_percentile_ignores_input_order_and_handles_edges():
    assert lt.percentile([40, 10, 30, 20], 50) == 25
    assert lt.percentile([7.5], 95) == 7.5
    assert lt.percentile([], 95) is None
    with pytest.raises(ValueError):
        lt.percentile([1, 2], 101)


def test_summarize_and_median_of():
    s = lt.summarize([10, 20, 30, 40, 50])
    assert s == {"n": 5, "p50": 30, "p95": pytest.approx(48), "p99": pytest.approx(49.6), "max": 50}
    assert lt.summarize([]) == {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}
    assert lt.median_of([3, None, 1, 2]) == 2
    assert lt.median_of([None]) is None


def _run(level, client_ms, errors=0):
    samples = [lt.Sample("text_g1", ms, ms / 2, 200, None) for ms in client_ms]
    samples += [lt.Sample("session", 5.0, None, 429, "rate_limited")] * errors
    return lt.RunResult("mock", level, 2, 2, 1.0, samples, {"mock-g1"})


def test_aggregate_takes_the_median_over_repetitions_and_sums_errors():
    runs = [_run(5, [10, 20]), _run(5, [30, 40], errors=1), _run(5, [100, 200], errors=2)]
    a = lt.aggregate(runs)
    # per-rep p95 of text_g1: 19.5, 39.5, 195 -> median 39.5
    assert a["kinds"]["text_g1"]["client"]["p95"] == pytest.approx(39.5)
    assert a["kinds"]["chat_all"]["client"]["max"] == 40
    assert a["kinds"]["text_g1"]["server"]["p50"] == pytest.approx(17.5)
    assert a["errors"] == {"429 rate_limited": 3}
    assert a["chat_p95_range"] == (pytest.approx(19.5), pytest.approx(195))
    assert "session" not in a["kinds"]  # only 200s are timed
    md = lt.markdown([a], {"base": "x", "repeat": 3, "per_worker": 6, "console": "no", "warmup": 1,
                           "when": "t", "warnings": []})
    assert "| mock | 5 | 6/6 |" in md and "429 rate_limited: 3" in md
    assert a["conversations_ok_sum"] == 6 and a["conversations_sum"] == 6


def test_errors_without_an_http_answer_are_named_by_the_exception():
    r = lt.RunResult("none", 1, 1, 0, 1.0, [lt.Sample("health", 1.0, None, 0, "exc:URLError:ConnectionRefusedError:10061"),
                                             lt.Sample("session", 1.0, None, 429, "rate_limited")])
    assert r.errors() == {"exc:URLError:ConnectionRefusedError:10061": 1, "429 rate_limited": 1}


def test_error_code_reads_the_api_error_or_the_gateway_message():
    assert lt.error_code(409, {"error": {"code": "conflict", "retryable": True}}) == "conflict"
    assert lt.error_code(401, {"message": "Unauthorized"}) == "Unauthorized"
    assert lt.error_code(502, "<html>") == "http_502"


def test_exc_code_keeps_the_os_reason():
    import urllib.error
    refused = ConnectionRefusedError(10061, "refused")
    assert lt.exc_code(urllib.error.URLError(refused)).startswith("exc:URLError:ConnectionRefusedError")
    assert lt.exc_code(TimeoutError()) == "exc:TimeoutError"


def test_api_base_adds_api_once():
    assert lt.api_base("http://127.0.0.1:8601") == "http://127.0.0.1:8601/api"
    assert lt.api_base("https://x.cloudfront.net/api/") == "https://x.cloudfront.net/api"


def test_mode_warning():
    assert lt.mode_warning("none", {"mock-g1"})
    assert lt.mode_warning("mock", set())
    assert lt.mode_warning("mock", {"mock-g1"}) is None
    assert lt.mode_warning("any", set()) is None


# ---------------------------------------------------------------- arguments

def test_parser_defaults_against_a_running_server(monkeypatch):
    monkeypatch.delenv("LOCAL_ANALYST_TOKEN", raising=False)
    a = lt.parse_args(["http://127.0.0.1:8601"])
    assert a.concurrency == [1] and a.repeat == 1 and a.per_worker == 6
    assert a.llm_mode == ["any"] and a.analyst_token is None and a.spawn_local is None


def test_parser_levels_modes_and_token_from_env(monkeypatch):
    monkeypatch.setenv("LOCAL_ANALYST_TOKEN", "t0k")
    a = lt.parse_args(["http://h", "--concurrency", "1,5,10,20", "--repeat", "3", "--llm-mode", "mock"])
    assert a.concurrency == [1, 5, 10, 20] and a.repeat == 3 and a.llm_mode == ["mock"]
    assert a.analyst_token == "t0k"
    assert lt.parse_args(["http://h", "--no-console"]).analyst_token is None


def test_parser_spawn_local_compares_modes():
    a = lt.parse_args(["--spawn-local", "8601", "--llm-mode", "none,mock", "--concurrency", "1,20"])
    assert a.spawn_local == 8601 and a.llm_mode == ["none", "mock"] and a.base_url is None


@pytest.mark.parametrize("argv", [
    [],                                                        # no base_url and no --spawn-local
    ["http://h", "--concurrency", "0"],
    ["http://h", "--concurrency", "1,x"],
    ["http://h", "--repeat", "0"],
    ["http://h", "--llm-mode", "gpt"],
    ["http://h", "--llm-mode", "none,mock"],                  # cannot switch a remote server
    ["http://h", "--spawn-local", "8601"],                    # both
    ["--spawn-local", "8601", "--llm-mode", "bedrock"],       # local_api.py has no bedrock client yet
    ["--spawn-local", "80"],
    ["http://h", "--server-arg=--fail"],
    ["http://h", "--warmup", "-1"],
    ["http://h", "--max-rps", "-1"],
])
def test_parser_rejects(argv, capsys):
    with pytest.raises(SystemExit):
        lt.parse_args(argv)


def test_pacer_spaces_requests_and_is_off_by_default():
    import time
    free = lt.Pacer()
    started = time.perf_counter()
    for _ in range(50):
        free.before_request()
    assert time.perf_counter() - started < 0.05
    capped = lt.Pacer(max_rps=100)
    started = time.perf_counter()
    for _ in range(6):  # the first goes at once, the other 5 wait 10 ms each
        capped.before_request()
    assert time.perf_counter() - started >= 0.045
    a = lt.parse_args(["http://h", "--max-rps", "15", "--think-ms", "1500"])
    assert a.max_rps == 15 and a.think_ms == 1500


# ---------------------------------------------------------------- scripts

def test_suggestions_are_the_front_end_ones():
    """The script sends the judge-mode suggestions of customers.ts, verbatim."""
    text = (REPO / "frontend" / "src" / "demo" / "customers.ts").read_text(encoding="utf-8")
    for key, sent in lt.SUGGESTIONS.items():
        block = re.search(rf"key: '{key}'.*?suggestions: \[(.*?)\]", text, re.S)
        assert block, key
        front = re.findall(r"text: '((?:[^'\\]|\\.)*)'", block.group(1))
        assert all(s in front for s in sent), (key, sent, front)


def test_plan_mixes_the_six_customers_at_every_level():
    assert set(lt.SCRIPTS) == set(lt.CUSTOMERS) == {"lucia", "sofia", "andres", "joao", "martina", "carlos"}
    for step in (s for steps in lt.SCRIPTS.values() for s in steps):
        assert step[0] in ("say", "press")
