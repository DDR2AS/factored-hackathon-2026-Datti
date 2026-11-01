"""scripts/local_api.py --config: v2 by default (fills only what .env left unset), b1 forced to compare,
and the composite gateway only when `make pipeline` has built data/gold."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("GATEWAY_BACKEND", "M1_BACKEND", "M2_BACKEND", "INVESTIGATOR_BACKEND", "LLM_PROVIDER", "DATA_DIR")


def _local_api():
    spec = importlib.util.spec_from_file_location("local_api_config", ROOT / "scripts" / "local_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clean(monkeypatch, tmp_path, gold: bool):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    if gold:
        (tmp_path / "gold").mkdir()
        (tmp_path / "gold" / "_meta.yaml").write_text("data_source: synthetic_replica\n", encoding="utf-8")


def test_v2_is_the_default_and_keeps_what_the_environment_set(monkeypatch, tmp_path):
    local_api = _local_api()
    _clean(monkeypatch, tmp_path, gold=True)
    monkeypatch.setenv("M1_BACKEND", "rule")  # e.g. from .env
    local_api.apply_config("v2")
    assert os.environ["GATEWAY_BACKEND"] == "composite"
    assert os.environ["M1_BACKEND"] == "rule"
    assert os.environ["M2_BACKEND"] == "model" and os.environ["INVESTIGATOR_BACKEND"] == "g2"


def test_without_gold_the_gateway_is_the_demo_store(monkeypatch, tmp_path):
    local_api = _local_api()
    _clean(monkeypatch, tmp_path, gold=False)
    local_api.apply_config("v2")
    assert os.environ["GATEWAY_BACKEND"] == "demo"


def test_b1_is_forced(monkeypatch, tmp_path):
    local_api = _local_api()
    _clean(monkeypatch, tmp_path, gold=False)
    monkeypatch.setenv("M1_BACKEND", "model")
    local_api.apply_config("b1")
    assert (os.environ["M1_BACKEND"], os.environ["M2_BACKEND"], os.environ["INVESTIGATOR_BACKEND"],
            os.environ["LLM_PROVIDER"]) == ("rule", "keywords", "stub", "none")
    assert "M1_BACKEND=rule" in local_api.describe_config("b1")
