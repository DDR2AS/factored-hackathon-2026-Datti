"""Front end <-> backend integration checks (M1 integration run, 28 sep). SYNTHETIC data only.

The front end and the backend were built in parallel against frontend/src/api/types.ts.
These tests read the front end's own files as text (no Node needed) and replay them against
ChatApp, so a drift on either side fails here:

- the judge-mode suggestions of each demo customer (frontend/src/demo/customers.ts), sent
  exactly as a judge would, land on that customer's expectedLane and expectedRule;
- every lane rule id in lane_rules.yaml has its customer text (rule.<id>) in es.ts and pt.ts;
- every charge status the demo gateway can return has its label (chargeStatus.<status>);
- every case status and queue shown to the customer is covered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from conversation import demo_gateway
from conversation.templates import forbidden_hits

from _support import Conv, make_app, reset_gateway

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "frontend" / "src"
CUSTOMERS_TS = FRONT / "demo" / "customers.ts"
I18N = {"es": FRONT / "i18n" / "es.ts", "pt": FRONT / "i18n" / "pt.ts"}
RULES_YAML = ROOT / "src" / "conversation" / "rules" / "lane_rules.yaml"
DEMO_YAML = ROOT / "src" / "conversation" / "demo" / "customers.yaml"


@pytest.fixture(autouse=True)
def _clean_gateway():
    reset_gateway()
    yield
    reset_gateway()


# ---------------------------------------------------------------- reading the front end as text

def _ts_string(raw: str) -> str:
    return raw.replace("\\'", "'")


def front_customers() -> dict[str, dict]:
    """{demo_key: {status, expectedLane, expectedRule, suggestions: [text]}} from customers.ts."""
    text = CUSTOMERS_TS.read_text(encoding="utf-8")
    body = text.split("export const DEMO_CUSTOMERS", 1)[1].split("export const ROBUSTNESS_SUGGESTIONS", 1)[0]
    out: dict[str, dict] = {}
    for block in re.split(r"\n  \{\n", body)[1:]:
        key = re.search(r"key: '(\w+)'", block).group(1)
        out[key] = {
            "status": re.search(r"status: '(\w+)'", block).group(1),
            "expectedLane": re.search(r"expectedLane: '([ABC])'", block).group(1),
            "expectedRule": re.search(r"expectedRule: '(\w+)'", block).group(1),
            "suggestions": [_ts_string(s) for s in re.findall(r"text: '((?:[^'\\]|\\.)*)'", block)],
        }
    return out


def i18n_keys(lang: str) -> set[str]:
    return set(re.findall(r"^\s*'([\w.]+)':", I18N[lang].read_text(encoding="utf-8"), re.M))


def test_front_customer_list_parses():
    customers = front_customers()
    assert list(customers) == ["lucia", "sofia", "andres", "joao", "martina", "carlos"]
    assert all(c["suggestions"] for c in customers.values())


# ---------------------------------------------------------------- the judge's path, per customer

def _choice_with(conv: Conv, merchant: str) -> str:
    return next(b["label"] for b in conv.last["buttons"] if b["kind"] == "choice" and merchant in b["label"])


# Each script uses only the front end's own suggestions (by index) and the buttons the server
# issued, like a judge clicking through the UI.
def _script_lucia(app, s):
    conv = Conv(app, "lucia")
    conv.say(s[0])
    conv.press("confirm")          # Es este
    return conv, conv.press("deny")  # No lo reconozco


def _script_sofia(app, s):
    conv = Conv(app, "sofia")
    r = conv.say(s[0])             # "me cobraron algo raro": asks one detail
    assert r["case_card"] is None and r["progress"]["missing"]
    r = conv.say(s[1])             # amount + day: three near-identical charges
    assert [b["kind"] for b in r["buttons"]] == ["choice", "choice", "choice", "deny", "handoff"]
    conv.press(label=_choice_with(conv, "RAPPI*RESTAURANTE"))
    final = conv.press("deny")     # No lo reconozco
    case_id = final["case_card"]["case_id"]
    r = conv.say(s[2])             # loan: out of scope, no second case, nothing promised
    assert r["case_card"]["case_id"] == case_id and [b["kind"] for b in r["buttons"]] == ["handoff"]
    return conv, final


def _script_andres(app, s):
    conv = Conv(app, "andres")
    r = conv.say(s[0])
    assert not any(b["kind"] == "choice" for b in r["buttons"]), "asked to choose between identical charges"
    return conv, conv.press("confirm")


def _script_joao(app, s):
    conv = Conv(app, "joao")
    conv.say(s[0])
    final = conv.press("confirm")  # É esta (never asked whether he recognizes it)
    assert all(r["reply_language"] == "pt" for r in conv.replies)
    return conv, final


def _script_martina(app, s):
    conv = Conv(app, "martina")
    r = conv.say(s[0])             # "creo que me la clonaron": asks the amount
    assert r["case_card"] is None
    conv.say(s[1])
    final = conv.press("confirm")  # Es este -> C, fraud queue, block offer
    assert any(b["kind"] == "confirm" and "7730" in b["label"] for b in final["buttons"])
    return conv, final


def _script_carlos(app, s):
    conv = Conv(app, "carlos")
    return conv, conv.say(s[0])    # mentions the regulator: C at once


SCRIPTS = {
    "lucia": _script_lucia, "sofia": _script_sofia, "andres": _script_andres,
    "joao": _script_joao, "martina": _script_martina, "carlos": _script_carlos,
}


@pytest.mark.parametrize("key", list(SCRIPTS))
def test_front_suggestions_land_on_the_expected_lane_and_rule(key):
    front = front_customers()[key]
    assert front["status"] == "active"
    conv, final = SCRIPTS[key](make_app(), front["suggestions"])
    card = final["case_card"]
    assert card is not None and re.fullmatch(r"EV-[A-Z0-9]{8}", card["case_id"])
    assert final["lane"] == front["expectedLane"] == card["lane"]
    assert card["lane_reason_code"] == front["expectedRule"], (key, card["lane_reason_code"])
    for r in [conv.session["welcome"]] + conv.replies:
        assert forbidden_hits(r["reply_text"]) == [], r["reply_text"]


def test_second_suggestions_are_understood():
    """The alternative suggestions of João and Carlos (found not understood in the run)."""
    s = front_customers()
    conv = Conv(make_app(), "joao")
    r = conv.say(s["joao"]["suggestions"][1])
    assert r["progress"]["complaint_type"] == "wrong_fee" and r["reply_language"] == "pt"
    reset_gateway()
    conv = Conv(make_app(), "carlos")
    r = conv.say(s["carlos"]["suggestions"][1])
    assert r["lane"] == "C" and r["case_card"]["lane_reason_code"] == "repeat_complainer"


def test_handoff_without_a_story_does_not_claim_a_summary():
    conv = Conv(make_app(), "carlos")
    r = conv.press("handoff")
    assert r["lane"] == "C" and "resumen" not in r["reply_text"]
    conv = Conv(make_app(), "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    r = conv.press("handoff")
    assert "resumen" in r["reply_text"]


# ---------------------------------------------------------------- texts the UI needs

def test_every_lane_rule_has_customer_text_in_both_languages():
    rules = yaml.safe_load(RULES_YAML.read_text(encoding="utf-8"))
    ids = {r["id"] for r in rules["rules"]} | {rules["default"]["id"]}
    for lang in I18N:
        missing = sorted(i for i in ids if f"rule.{i}" not in i18n_keys(lang))
        assert missing == [], f"{lang}.ts lacks rule texts: {missing}"


def test_every_charge_status_has_a_label():
    demo = yaml.safe_load(DEMO_YAML.read_text(encoding="utf-8"))
    statuses = set()

    def walk(node):
        if isinstance(node, dict):
            if "txn_id" in node:
                statuses.add(str(node.get("status", "approved")).lower())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(demo)
    # plus the four values of transaction_status in the dataset (docs/data_profile.md)
    statuses |= {"approved", "declined", "pending", "reversed"}
    for lang in I18N:
        missing = sorted(s for s in statuses if f"chargeStatus.{s}" not in i18n_keys(lang))
        assert missing == [], f"{lang}.ts lacks charge status labels: {missing}"


def test_every_case_status_has_a_label():
    from conversation.contract import CaseStatus

    for lang in I18N:
        keys = i18n_keys(lang)
        assert {f"status.{s}" for s in CaseStatus.__args__} <= keys


def test_demo_keys_match_the_gateway_allow_list():
    assert set(front_customers()) == set(demo_gateway.DEMO_KEYS)
