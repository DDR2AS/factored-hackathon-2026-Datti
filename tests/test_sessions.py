"""Sessions, buttons, idempotency, turn cap and store selection. SYNTHETIC data only."""

import json
import threading

import pytest

from conversation.contract import ApiFailure
from conversation.sessions import MAX_TURNS, SessionService, token_hash, ttl_minutes_from_env
from conversation.store import (
    TRACE_FIELDS, JsonlTraceSink, MemorySessionStore, StoreConfigError, make_stores,
)

from _support import Conv, FakeClock, make_app, reset_gateway


@pytest.fixture(autouse=True)
def _clean_gateway():
    reset_gateway()
    yield
    reset_gateway()


def code_of(excinfo) -> tuple[str, int, bool]:
    e = excinfo.value
    return e.code, e.http_status, e.retryable


def test_token_is_opaque_and_only_its_hash_is_stored():
    app = make_app()
    s = app.create_session({"demo_key": "lucia", "channel": "web"})
    token = s["session_token"]
    assert len(token) >= 43 and all(ch.isalnum() or ch in "-_" for ch in token)
    stored = app.stores.sessions.get_by_token_hash(token_hash(token))
    assert token not in json.dumps(stored)
    assert stored["token_hash"] == token_hash(token)
    assert "DEMO-C-0001" not in json.dumps(s)  # customer_id never leaves the server
    assert s["synthetic"] is True and s["customer"]["display_name"] == "Lucía"
    assert s["expires_at"] == "2026-09-29T15:15:00Z"


def test_expired_unknown_and_missing_tokens_are_the_same_401():
    clock = FakeClock()
    app = make_app(clock=clock)
    conv = Conv(app, "lucia")
    conv.say("hola")
    clock.advance(minutes=14, seconds=59)
    conv.say("sigo aquí")
    clock.advance(seconds=1)
    errors = []
    for token in (conv.token, "unknown-token", None, ""):
        with pytest.raises(ApiFailure) as e:
            app.chat(token, {"client_msg_id": "m1", "message": "hola"})
        errors.append((code_of(e), e.value.body()["error"]["code"]))
    assert all(err == (("session_expired", 401, False), "session_expired") for err in errors)
    assert len(app.stores.sessions) == 0  # the expired session was removed on read


def test_ttl_comes_from_the_environment():
    assert ttl_minutes_from_env({}) == 15
    assert ttl_minutes_from_env({"SESSION_TTL_MINUTES": "1"}) == 1
    assert ttl_minutes_from_env({"SESSION_TTL_MINUTES": "0.05"}) == 0.05
    assert ttl_minutes_from_env({"SESSION_TTL_MINUTES": "nope"}) == 15
    clock = FakeClock()
    app = make_app(clock=clock, ttl_minutes=1)
    s = app.create_session({"demo_key": "joao", "channel": "web"})
    assert s["expires_at"] == "2026-09-29T15:01:00Z"


def test_invalid_demo_key_or_customer_id_in_the_body_is_400():
    app = make_app()
    for body in ({"demo_key": "mallory", "channel": "web"},
                 {"demo_key": "lucia", "channel": "web", "customer_id": "DEMO-C-0001"},
                 {"demo_key": "lucia", "channel": "fax"},
                 {"demo_key": "lucia"},
                 ["lucia"]):
        with pytest.raises(ApiFailure) as e:
            app.create_session(body)
        assert code_of(e) == ("invalid_request", 400, False)
    assert len(app.stores.sessions) == 0


def test_session_language_is_an_initial_value():
    app = make_app()
    s = app.create_session({"demo_key": "lucia", "channel": "web", "language": "pt"})
    assert s["welcome"]["reply_language"] == "pt" and s["customer"]["language"] == "es"


def test_buttons_are_single_use_and_session_bound():
    app = make_app()
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    old = conv.button("confirm")
    conv.press("confirm")
    with pytest.raises(ApiFailure) as e:
        conv.send(button_id=old["id"])
    assert code_of(e) == ("conflict", 409, False)
    other = Conv(app, "lucia")
    with pytest.raises(ApiFailure) as e:
        other.send(button_id=conv.button("deny")["id"])  # a live button of another session
    assert code_of(e) == ("conflict", 409, False)
    with pytest.raises(ApiFailure) as e:
        other.send(button_id="b_forged")
    assert code_of(e) == ("conflict", 409, False)


def test_same_client_msg_id_gives_the_same_reply_and_one_case():
    app = make_app()
    conv = Conv(app, "lucia")
    conv.say("me cobraron como 450 en el super el 12")
    conv.press("confirm")
    deny = conv.button("deny")["id"]
    r1 = conv.send(button_id=deny, cmid="same-id")
    r2 = conv.send(button_id=deny, cmid="same-id")
    assert r1 == r2 and r1["case_card"]["case_id"].startswith("EV-")
    assert len(app.stores.cases) == 1
    record = app.sessions.authenticate(conv.token)
    assert record.turns == 3  # the replay is not a turn


def test_turn_31_is_session_limit_and_replays_still_work():
    app = make_app()
    conv = Conv(app, "carlos")
    first = conv.send(message="hola", cmid="t1")
    for i in range(2, MAX_TURNS + 1):
        conv.send(message="¿cómo va mi caso?", cmid=f"t{i}")
    with pytest.raises(ApiFailure) as e:
        conv.send(message="una más", cmid="t31")
    assert code_of(e) == ("session_limit", 409, False)
    assert conv.send(message="hola", cmid="t1") == first


def test_same_message_in_flight_is_a_retryable_conflict():
    svc = SessionService(MemorySessionStore())
    entered, release = threading.Event(), threading.Event()

    def hold():
        with svc.turn_guard("S-1", "m-1"):
            entered.set()
            release.wait(2)

    th = threading.Thread(target=hold)
    th.start()
    entered.wait(2)
    with pytest.raises(ApiFailure) as e:
        with svc.turn_guard("S-1", "m-1"):
            pass
    release.set()
    th.join()
    assert code_of(e) == ("conflict", 409, True)
    with svc.turn_guard("S-1", "m-1"):  # free again afterwards
        pass


# ---------------------------------------------------------------- stores (#9 proposal)

def test_store_selection():
    assert make_stores({}).backend == "memory"  # STAGE unset counts as local
    assert make_stores({"STAGE": "local"}).backend == "memory"
    assert make_stores({"STAGE": "dev", "STORE_BACKEND": "memory"}).backend == "memory"
    with pytest.raises(StoreConfigError, match="STORE_BACKEND is not set"):
        make_stores({"STAGE": "dev"})
    with pytest.raises(StoreConfigError, match="Andrés"):
        make_stores({"STAGE": "dev", "STORE_BACKEND": "dynamodb"})
    with pytest.raises(StoreConfigError, match="unknown"):
        make_stores({"STAGE": "local", "STORE_BACKEND": "redis"})


def test_memory_session_store_copies_and_rotates_hashes():
    store = MemorySessionStore()
    store.put({"session_id": "S-1", "token_hash": "h1", "x": [1]})
    got = store.get("S-1")
    got["x"].append(2)
    assert store.get("S-1")["x"] == [1]
    store.put({"session_id": "S-1", "token_hash": "h2", "x": [1]})
    assert store.get_by_token_hash("h1") is None and store.get_by_token_hash("h2")["session_id"] == "S-1"
    store.delete("S-1")
    assert store.get("S-1") is None and store.get_by_token_hash("h2") is None


def test_jsonl_trace_sink_writes_one_line_per_event(tmp_path):
    sink = JsonlTraceSink(tmp_path)
    event = {k: None for k in TRACE_FIELDS} | {"trace_id": "tr-1", "name": "lane_rules", "source": "demo"}
    sink.emit(event)
    sink.emit(event)
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["name"] == "lane_rules"


def test_jsonl_trace_sink_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    JsonlTraceSink(blocker / "sub").emit({"trace_id": "x"})  # cannot create a dir under a file
