"""Trace queries (#7) over JSONL files (synthetic events)."""

import json

from eval import traces


def _ev(trace_id, step, actor, name, ms, cost=0.0, err=None, session="S-1", turn=1, version=None):
    return {"trace_id": trace_id, "session_id": session, "case_id": None, "turn": turn, "step": step, "actor": actor,
            "name": name, "input_ref": None, "output_ref": None, "latency_ms": ms, "tokens_in": 10 if actor == "model" else None,
            "tokens_out": 5 if actor == "model" else None, "cost_usd": cost, "version": version, "error_code": err,
            "ts": "2026-10-29T10:00:00Z", "source": "demo"}


def test_trace_summary(tmp_path):
    rows = [_ev("tr-1", 1, "rule", "redact", 1), _ev("tr-1", 2, "model", "g1_extract", 120, 0.001, version="g1_extract@v1"),
            _ev("tr-1", 3, "tool", "find_candidate_txns", 15),
            _ev("tr-2", 1, "rule", "redact", 1, turn=2), _ev("tr-2", 2, "tool", "get_transaction", 5, err="tool_unavailable", turn=2),
            _ev("lc-EV-AAAA", 1, "model", "investigator", 300, version="g2_investigator@v1")]
    (tmp_path / "2026-10-29.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s = traces.summary(tmp_path)
    assert s["available"]
    assert s["latency"][0]["turns"] == 2
    assert any(e["error_code"] == "tool_unavailable" and e["n"] == 1 for e in s["errors"])
    assert any(m["name"] == "g1_extract" and m["calls"] == 1 for m in s["models"])
    assert s["sessions"][0]["sessions"] == 1


def test_no_traces(tmp_path):
    assert traces.summary(tmp_path) == {"available": False}
