"""Customer chat sessions (INTERFACES.md #1, owner: arturo).

- ``POST /session`` takes a ``demo_key`` from the server allow list (never a customer_id).
- The token is ``secrets.token_urlsafe(32)``; the store keeps only its sha256. The token
  is returned once and never logged.
- ``expires_at`` = creation + ``SESSION_TTL_MINUTES`` (15 by default) and is checked on
  every read. An unknown, invalid or expired token is the same 401 ``session_expired``.
- Pending buttons: opaque single-use ids ``b_<nonce>`` mapped to a server-side action.
  Every turn replaces them, so an old or foreign id is a 409 ``conflict``. A "yes" is
  therefore never read from a label in ES or PT.
- Idempotency: the reply to each ``client_msg_id`` is stored in the session; the same id
  gets the same reply and never a second case. A request with the same id still in flight
  is a 409 ``conflict`` with ``retryable: true``.
- At most ``MAX_TURNS`` (30) turns per session; the next one is 409 ``session_limit``.
  Judge-mode requests with only ``demo_switches`` are not turns: they have their own cap,
  ``MAX_SWITCH_REQUESTS`` (60), so the session record and the traces stay bounded (SEC-04).
- Turns of one session are serialised. A second turn waits at most ``turn_wait_seconds``
  (1 s by default) for the one in flight, then gets 409 ``conflict`` with ``retryable: true``
  instead of holding a server thread (a ``model_slow`` turn takes 8 s; SEC-05).
- ``on_expire(session_id)`` runs when a read finds the session expired by TTL (the app uses it
  to drop the session's judge switch ``tools_down``; SEC-02).
- Memory hygiene: expired sessions are swept from the store (``purge_expired``, when the
  store has it) at most once per ``SWEEP_EVERY``; the per-session lock is dropped when no
  turn of that session is in flight.
- ``POST /session`` is limited per caller address to ``SESSION_RATE_PER_MINUTE`` (60 by
  default, 0 disables) in a sliding minute: 429 ``rate_limited``. In the cloud, API Gateway
  throttling does the same job; this keeps the local server and one container bounded.
- The clock is injectable (tests expire sessions without waiting).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

from pydantic import BaseModel, ConfigDict, Field

from conversation.contract import ApiFailure
from conversation.store import SessionStore

MAX_TURNS = 30
MAX_SWITCH_REQUESTS = 60
DEFAULT_TURN_WAIT_SECONDS = 1.0
DEFAULT_TTL_MINUTES = 15.0
DEFAULT_SESSION_RATE_PER_MINUTE = 60
SWEEP_EVERY = timedelta(seconds=60)
MAX_TRACKED_CALLERS = 10_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def iso(dt: datetime) -> str:
    """ISO 8601 UTC with a Z, seconds precision."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class SessionRecord(BaseModel):
    """What the SessionStore keeps (JSON-ready). Never contains the token itself."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    token_hash: str
    demo_key: str
    customer_id: str  # server side only; never in a response
    channel: str
    language: str
    created_at: str
    expires_at: str
    turns: int = 0
    switch_requests: int = 0  # judge-mode requests with only demo_switches (own cap)
    conversation: dict[str, Any] = Field(default_factory=dict)
    pending_buttons: dict[str, dict[str, Any]] = Field(default_factory=dict)
    replies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source: str = "demo"  # judge-mode sessions: traces stay out of prompts and training
    # Judge-mode switch (RF-22): G1 of this session times out. tools_down lives in the demo
    # gateway (per session); expire_session deletes the session. Demo sessions only.
    model_slow: bool = False
    # Judge-mode demo clock (RF-22): the SLA timers of this session's lane B cases run scaled
    # (lifecycle.py, LOCAL_SLA_SCALE; 1 day = 1 minute by default). Demo sessions only.
    fast_clock: bool = False
    # Cases whose analyst-approved resolution was already shown in a chat reply.
    resolutions_delivered: list[str] = Field(default_factory=list)


def ttl_minutes_from_env(env: dict[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = env.get("SESSION_TTL_MINUTES")
    try:
        value = float(raw) if raw else DEFAULT_TTL_MINUTES
    except ValueError:
        value = DEFAULT_TTL_MINUTES
    return value if value > 0 else DEFAULT_TTL_MINUTES


def session_rate_from_env(env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("SESSION_RATE_PER_MINUTE")
    try:
        value = int(raw) if raw not in (None, "") else DEFAULT_SESSION_RATE_PER_MINUTE
    except ValueError:
        value = DEFAULT_SESSION_RATE_PER_MINUTE
    return max(0, value)


class SessionService:
    def __init__(self, store: SessionStore, clock: Callable[[], datetime] = utc_now,
                 ttl_minutes: float | None = None, max_turns: int = MAX_TURNS,
                 token_factory: Callable[[], str] | None = None,
                 rate_per_minute: int | None = None,
                 turn_wait_seconds: float = DEFAULT_TURN_WAIT_SECONDS,
                 max_switch_requests: int = MAX_SWITCH_REQUESTS,
                 on_expire: Callable[[str], None] | None = None) -> None:
        self.store = store
        self.clock = clock
        self.turn_wait = max(0.0, turn_wait_seconds)
        self.max_switch_requests = max_switch_requests
        self.on_expire = on_expire
        self.ttl = timedelta(minutes=ttl_minutes if ttl_minutes is not None else ttl_minutes_from_env())
        self.max_turns = max_turns
        self.rate_per_minute = session_rate_from_env() if rate_per_minute is None else max(0, rate_per_minute)
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._locks_guard = threading.Lock()
        self._locks: dict[str, list] = {}  # session_id -> [Lock, turns holding or waiting for it]
        self._in_flight: set[tuple[str, str]] = set()
        self._rate_guard = threading.Lock()
        self._created_by: dict[str, deque] = {}
        self._last_sweep: datetime | None = None

    # ------------------------------------------------------------ create / read

    def check_create_rate(self, caller: str | None) -> None:
        """429 ``rate_limited`` once ``caller`` created ``rate_per_minute`` sessions in the last minute.

        ``caller`` is the source address; None means no limit (tests calling the app directly)."""
        if not caller or self.rate_per_minute <= 0:
            return
        now = self.clock()
        window_start = now - timedelta(minutes=1)
        with self._rate_guard:
            stamps = self._created_by.get(caller)
            if stamps is None:
                if len(self._created_by) >= MAX_TRACKED_CALLERS:
                    for key in [k for k, v in self._created_by.items() if not v or v[-1] <= window_start]:
                        self._created_by.pop(key, None)
                stamps = self._created_by.setdefault(caller, deque())
            while stamps and stamps[0] <= window_start:
                stamps.popleft()
            if len(stamps) >= self.rate_per_minute:
                raise ApiFailure("rate_limited", "too many new sessions from this address; retry in a minute")
            stamps.append(now)

    def sweep_expired(self, force: bool = False) -> int:
        """Remove expired sessions from a store that supports it (the memory one). Throttled."""
        purge = getattr(self.store, "purge_expired", None)
        if purge is None:
            return 0
        now = self.clock()
        if not force and self._last_sweep is not None and now - self._last_sweep < min(SWEEP_EVERY, self.ttl):
            return 0
        self._last_sweep = now
        return purge(now)

    def create(self, demo_key: str, customer_id: str, channel: str, language: str) -> tuple[str, SessionRecord]:
        """New session for an allow-listed customer (the caller resolved demo_key)."""
        self.sweep_expired()
        token = self._token_factory()
        now = self.clock()
        record = SessionRecord(
            session_id="S-" + secrets.token_hex(8),
            token_hash=token_hash(token),
            demo_key=demo_key,
            customer_id=customer_id,
            channel=channel,
            language=language,
            created_at=iso(now),
            expires_at=iso(now + self.ttl),
        )
        self.store.put(record.model_dump())
        return token, record

    def authenticate(self, token: str | None) -> SessionRecord:
        """The session of ``token``, or 401 ``session_expired`` (same for unknown and expired)."""
        if not token:
            raise ApiFailure("session_expired", "missing session token")
        data = self.store.get_by_token_hash(token_hash(token))
        if data is None:
            raise ApiFailure("session_expired", "session expired or unknown")
        record = SessionRecord.model_validate(data)
        if self.clock() >= parse_iso(record.expires_at):
            self.store.delete(record.session_id)
            if self.on_expire is not None:
                try:
                    self.on_expire(record.session_id)
                except Exception:
                    pass  # cleanup never changes the 401
            raise ApiFailure("session_expired", "session expired or unknown")
        return record

    def save(self, record: SessionRecord) -> None:
        self.store.put(record.model_dump())

    def expire(self, record: SessionRecord) -> None:
        """Judge-mode ``expire_session``: the token stops working now (same 401 as a real expiry)."""
        self.store.delete(record.session_id)

    # ------------------------------------------------------------ turns

    def check_turn_limit(self, record: SessionRecord) -> None:
        if record.turns >= self.max_turns:
            raise ApiFailure("session_limit", f"at most {self.max_turns} turns per session", retryable=False)

    def check_switch_limit(self, record: SessionRecord) -> None:
        if record.switch_requests >= self.max_switch_requests:
            raise ApiFailure("session_limit", f"at most {self.max_switch_requests} switch requests per session",
                             retryable=False)

    @staticmethod
    def new_button_id() -> str:
        return "b_" + secrets.token_urlsafe(12)

    def take_button(self, record: SessionRecord, button_id: str) -> dict[str, Any]:
        """The action behind a pending button; 409 ``conflict`` if it is not pending."""
        action = record.pending_buttons.get(button_id)
        if action is None:
            raise ApiFailure("conflict", "button expired or not issued in this session", retryable=False)
        return action

    @contextmanager
    def turn_guard(self, session_id: str, client_msg_id: str) -> Iterator[None]:
        """Serialises turns of one session; the same client_msg_id twice at once is a 409, and
        a different one waits at most ``turn_wait`` seconds, then 409 retryable.

        Process-local (enough for the local server and one Lambda container). The DynamoDB
        store needs a conditional write on the session version for the same guarantee.
        """
        key = (session_id, client_msg_id)
        with self._locks_guard:
            if key in self._in_flight:
                raise ApiFailure("conflict", "the same message is still being processed", retryable=True)
            self._in_flight.add(key)
            entry = self._locks.setdefault(session_id, [threading.Lock(), 0])
            entry[1] += 1
        try:
            if not entry[0].acquire(timeout=self.turn_wait):
                raise ApiFailure("conflict", "another message of this session is still being processed",
                                 retryable=True)
            try:
                yield
            finally:
                entry[0].release()
        finally:
            with self._locks_guard:
                self._in_flight.discard(key)
                entry[1] -= 1
                if entry[1] <= 0 and self._locks.get(session_id) is entry:
                    del self._locks[session_id]  # no turn of this session in flight: forget its lock
