"""Shared helpers for the API, session and orchestrator tests. SYNTHETIC data only."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from conversation import demo_gateway
from conversation.app import build_app
from conversation.store import MemoryCaseStore, MemorySessionStore, MemoryTraceSink, Stores


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


def make_app(clock: FakeClock | None = None, llm=None, sleeps: list | None = None, **kwargs):
    stores = Stores(MemorySessionStore(), MemoryCaseStore(), MemoryTraceSink(), "memory")
    sleeps = [] if sleeps is None else sleeps
    app = build_app(env={"STAGE": "local"}, clock=clock or FakeClock(), stores=stores,
                    sleep=sleeps.append, llm=llm, use_env_llm=False, **kwargs)
    return app


# ---------------------------------------------------------------- driving the app directly

class Conv:
    """A conversation through ChatApp (no HTTP)."""

    def __init__(self, app, demo_key: str, language: str | None = None):
        self.app = app
        body: dict[str, Any] = {"demo_key": demo_key, "channel": "web"}
        if language:
            body["language"] = language
        self.session = app.create_session(body)
        self.token = self.session["session_token"]
        self.last = self.session["welcome"]
        self.replies: list[dict] = []

    def send(self, message: str | None = None, button_id: str | None = None, cmid: str | None = None) -> dict:
        body: dict[str, Any] = {"client_msg_id": cmid or uuid.uuid4().hex}
        if message is not None:
            body["message"] = message
        if button_id is not None:
            body["button_id"] = button_id
        r = self.app.chat(self.token, body)
        self.last = r
        self.replies.append(r)
        return r

    def say(self, text: str) -> dict:
        return self.send(message=text)

    def button(self, kind: str | None = None, label: str | None = None, index: int = 0) -> dict:
        found = [b for b in self.last["buttons"]
                 if (kind is None or b["kind"] == kind) and (label is None or b["label"].startswith(label))]
        assert len(found) > index, (kind, label, self.last["buttons"])
        return found[index]

    def press(self, kind: str | None = None, label: str | None = None, index: int = 0) -> dict:
        return self.send(button_id=self.button(kind, label, index)["id"])

    @property
    def session_id(self) -> str:
        from conversation.sessions import token_hash

        return self.app.stores.sessions.get_by_token_hash(token_hash(self.token))["session_id"]


# ---------------------------------------------------------------- HTTP API v2 events

def v2_event(route_key: str, body: Any = None, token: str | None = None, header: str = "authorization",
             path_params: dict | None = None, raw_body: str | None = None) -> dict:
    method, path = route_key.split(" ", 1)
    headers = {"content-type": "application/json", "host": "localhost"}
    if token is not None:
        if header == "authorization":
            headers["authorization"] = f"Bearer {token}"
        else:
            headers[header] = token
    event = {
        "version": "2.0",
        "routeKey": route_key,
        "rawPath": path,
        "rawQueryString": "",
        "headers": headers,
        "requestContext": {"http": {"method": method, "path": path}, "routeKey": route_key, "stage": "$default"},
        "isBase64Encoded": False,
    }
    if path_params:
        event["pathParameters"] = path_params
    if raw_body is not None:
        event["body"] = raw_body
    elif body is not None:
        event["body"] = json.dumps(body)
    return event


def call(event: dict) -> tuple[int, Any, dict]:
    from handlers import api

    res = api.handler(event, None)
    body = json.loads(res["body"]) if res.get("body") else None
    return res["statusCode"], body, res.get("headers", {})


def reset_gateway() -> None:
    demo_gateway.reset_demo_state()
