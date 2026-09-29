"""Storage protocols and in-memory implementations (owner: arturo; DynamoDB side: Andrés).

PROPOSAL to Andrés for INTERFACES.md #9: the API code depends only on the three protocols
below. Arturo writes the in-memory versions (local and tests); Andrés writes the DynamoDB
and S3 versions against the same protocols. Selection is by ``STORE_BACKEND``
(``memory`` | ``dynamodb``). Outside ``STAGE=local`` a missing ``STORE_BACKEND`` is an error,
never a silent fallback to memory: in the cloud, sessions kept in one container's memory
would be lost between invocations.

What each store keeps:
- SessionStore: one JSON-ready dict per session, keyed by ``session_id`` and findable by the
  sha256 of its token (the token itself is never stored).
- CaseStore: one CaseRecord (#3) per case plus the session that created it (owner check).
- TraceSink: #7 trace events, one per step. No free text: messages go by reference.
- ReviewStore (console, docs/contrato_consola.md): the investigator report (#6) of each case,
  the stored reply of each analyst decision (idempotency by client_decision_id) and the case's
  redacted conversation (analyst only). Kept outside the CaseRecord on purpose: a chat turn
  appends to it without bumping ``case.version`` (the analyst's decision is not made stale by
  a "hola").

Optimistic concurrency on cases: ``CaseStore.put`` writes only if ``case.version`` equals the
stored version, then stores ``version + 1`` and stamps ``updated_at`` (the DynamoDB store does
the same with a conditional write on ``version``). A stale write raises
``CaseVersionConflict``; the analyst decision turns it into 409 ``conflict``.

The memory stores copy on read and write, so callers cannot rely on shared mutable objects
(the same behaviour a DynamoDB store will have).
"""

from __future__ import annotations

import copy
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from conversation.case import CaseRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE_DIR = REPO_ROOT / "data" / "traces"

# The #7 fields, in order. Every event written by a TraceSink has exactly these keys plus
# "source" ("demo" for judge-mode sessions, so they stay out of prompts and training).
TRACE_FIELDS = (
    "trace_id", "session_id", "case_id", "turn", "step", "actor", "name", "input_ref",
    "output_ref", "latency_ms", "tokens_in", "tokens_out", "cost_usd", "version",
    "error_code", "ts",
)


class StoreConfigError(RuntimeError):
    """Store selection is missing or wrong. Raised at start-up, with the fix in the message."""


class CaseVersionConflict(RuntimeError):
    """``CaseStore.put`` with a ``case.version`` that is not the stored one (someone wrote first)."""


# ---------------------------------------------------------------- protocols

class SessionStore(Protocol):
    def put(self, session: dict[str, Any]) -> None:
        """Insert or replace; ``session["session_id"]`` and ``session["token_hash"]`` required."""

    def get(self, session_id: str) -> dict[str, Any] | None: ...

    def get_by_token_hash(self, token_hash: str) -> dict[str, Any] | None: ...

    def delete(self, session_id: str) -> None: ...


@dataclass(frozen=True)
class StoredCase:
    case: CaseRecord
    session_id: str  # the session that created it; owner = case.customer_id + this


class CaseStore(Protocol):
    def put(self, case: CaseRecord, session_id: str) -> None:
        """Conditional write: ``case.version`` must equal the stored version (any version for a
        new case). Stores ``version + 1`` and ``updated_at`` = now, and updates both on the
        object passed. Raises CaseVersionConflict otherwise."""

    def get(self, case_id: str) -> StoredCase | None: ...

    def all(self) -> list[StoredCase]:
        """Every case (analyst queue). DynamoDB: a GSI by status/breach_at instead of a scan."""


class ReviewStore(Protocol):
    def put_report(self, case_id: str, report: dict[str, Any]) -> None: ...

    def get_report(self, case_id: str) -> dict[str, Any] | None: ...

    def put_decision_reply(self, key: str, body_hash: str, response: dict[str, Any]) -> None: ...

    def get_decision_reply(self, key: str) -> tuple[str, dict[str, Any]] | None: ...

    def append_conversation(self, case_id: str, turns: list[dict[str, Any]]) -> None:
        """Append redacted turns ({role, text, language, source, at}) to the case's conversation
        (proposal to Andrés: one item per case, list capped at MAX_CONVERSATION_TURNS)."""

    def get_conversation(self, case_id: str) -> list[dict[str, Any]]: ...


class TraceSink(Protocol):
    def emit(self, event: dict[str, Any]) -> None:
        """Write one #7 event. Must never raise into the request path."""


# ---------------------------------------------------------------- memory implementations

class MemorySessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, dict] = {}
        self._by_hash: dict[str, str] = {}

    def put(self, session: dict[str, Any]) -> None:
        data = json.loads(json.dumps(session))  # JSON-ready check and deep copy in one go
        with self._lock:
            old = self._by_id.get(data["session_id"])
            if old and old["token_hash"] != data["token_hash"]:
                self._by_hash.pop(old["token_hash"], None)
            self._by_id[data["session_id"]] = data
            self._by_hash[data["token_hash"]] = data["session_id"]

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._by_id.get(session_id)
            return copy.deepcopy(data) if data else None

    def get_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            sid = self._by_hash.get(token_hash)
            data = self._by_id.get(sid) if sid else None
            return copy.deepcopy(data) if data else None

    def delete(self, session_id: str) -> None:
        with self._lock:
            data = self._by_id.pop(session_id, None)
            if data:
                self._by_hash.pop(data["token_hash"], None)

    def purge_expired(self, now: datetime) -> int:
        """Drop every session whose ``expires_at`` is at or before ``now``. Returns how many.

        Optional in the protocol: a DynamoDB table does this with its TTL attribute. Without
        it, the memory store only forgot a session when someone used its expired token.
        """
        removed = 0
        with self._lock:
            for sid, data in list(self._by_id.items()):
                try:
                    expires = datetime.fromisoformat(str(data.get("expires_at", "")).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if expires <= now:
                    self._by_id.pop(sid, None)
                    self._by_hash.pop(data.get("token_hash"), None)
                    removed += 1
        return removed

    def __len__(self) -> int:
        return len(self._by_id)


class MemoryCaseStore:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._cases: dict[str, tuple[dict, str]] = {}
        self.clock = clock  # build_app wires the app clock; None = real UTC time

    def put(self, case: CaseRecord, session_id: str) -> None:
        now = (self.clock or (lambda: datetime.now(timezone.utc)))()
        with self._lock:
            old = self._cases.get(case.case_id)
            if old is not None:
                stored_version = int(old[0].get("version") or 1)
                if case.version != stored_version:
                    raise CaseVersionConflict(
                        f"case {case.case_id}: version {case.version} is stale (stored {stored_version})")
                new_version = stored_version + 1
            else:
                new_version = max(1, case.version)
            case.version = new_version
            case.updated_at = now
            self._cases[case.case_id] = (case.model_dump(mode="json", by_alias=True), session_id)

    def get(self, case_id: str) -> StoredCase | None:
        with self._lock:
            row = self._cases.get(case_id)
        if row is None:
            return None
        return StoredCase(case=CaseRecord.model_validate(row[0]), session_id=row[1])

    def all(self) -> list[StoredCase]:
        with self._lock:
            ids = list(self._cases)
        return [c for c in (self.get(i) for i in ids) if c is not None]

    def __len__(self) -> int:
        return len(self._cases)


MAX_CONVERSATION_TURNS = 200


class MemoryReviewStore:
    """Investigator reports by case, analyst decision replies by idempotency key, and the
    redacted conversation of each case."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reports: dict[str, dict] = {}
        self._decisions: dict[str, tuple[str, dict]] = {}
        self._conversations: dict[str, list[dict]] = {}

    def append_conversation(self, case_id: str, turns: list[dict[str, Any]]) -> None:
        with self._lock:
            conv = self._conversations.setdefault(case_id, [])
            conv.extend(copy.deepcopy(list(turns)))
            del conv[:-MAX_CONVERSATION_TURNS]

    def get_conversation(self, case_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._conversations.get(case_id, []))

    def put_report(self, case_id: str, report: dict[str, Any]) -> None:
        with self._lock:
            self._reports[case_id] = copy.deepcopy(report)

    def get_report(self, case_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._reports.get(case_id)
            return copy.deepcopy(r) if r is not None else None

    def put_decision_reply(self, key: str, body_hash: str, response: dict[str, Any]) -> None:
        with self._lock:
            self._decisions.setdefault(key, (body_hash, copy.deepcopy(response)))

    def get_decision_reply(self, key: str) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            row = self._decisions.get(key)
            return (row[0], copy.deepcopy(row[1])) if row else None


class MemoryTraceSink:
    """Keeps events in a list (tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(dict(event))


class JsonlTraceSink:
    """#7 locally: one JSON line per event in ``<dir>/<UTC date>.jsonl`` (data/ is git-ignored)."""

    def __init__(self, directory: Path | str = DEFAULT_TRACE_DIR) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def emit(self, event: dict[str, Any]) -> None:
        try:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            line = json.dumps(event, ensure_ascii=False, default=str)
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                with open(self.directory / f"{day}.jsonl", "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError:
            pass  # tracing never breaks a customer turn


# ---------------------------------------------------------------- selection (#9 proposal)

@dataclass
class Stores:
    sessions: SessionStore
    cases: CaseStore
    traces: TraceSink
    backend: str
    reviews: ReviewStore = field(default_factory=MemoryReviewStore)


def make_stores(env: dict[str, str] | None = None) -> Stores:
    """Build the stores from ``STORE_BACKEND`` and ``STAGE``.

    STAGE unset counts as "local" (the CDK always sets it in AWS). Outside local, a missing
    STORE_BACKEND raises StoreConfigError instead of silently using memory.
    ``TRACE_DIR`` overrides the local traces folder.
    """
    env = os.environ if env is None else env
    stage = env.get("STAGE") or "local"
    backend = (env.get("STORE_BACKEND") or "").strip().lower()
    if not backend:
        if stage != "local":
            raise StoreConfigError(
                f"STORE_BACKEND is not set and STAGE={stage!r} is not 'local'. Set "
                "STORE_BACKEND=dynamodb (Andrés, #9) or, only on purpose, STORE_BACKEND=memory; "
                "the memory store loses sessions between Lambda containers."
            )
        backend = "memory"
    if backend == "memory":
        return Stores(sessions=MemorySessionStore(), cases=MemoryCaseStore(),
                      traces=JsonlTraceSink(env.get("TRACE_DIR") or DEFAULT_TRACE_DIR), backend="memory",
                      reviews=MemoryReviewStore())
    if backend == "dynamodb":
        raise StoreConfigError(
            "STORE_BACKEND=dynamodb is not implemented in this repo yet: Andrés writes it "
            "against the SessionStore/CaseStore/TraceSink protocols in conversation/store.py (#9)."
        )
    raise StoreConfigError(f"unknown STORE_BACKEND={backend!r}; use 'memory' or 'dynamodb'")
