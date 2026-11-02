"""Simulated state shared by every gateway backend (process memory).

- Failure switch: ``set_tool_failure(None, True)`` makes every tool raise ToolUnavailable
  (``local_api.py --fail tools``); with a session id, only that session's tools fail (the judge's
  "transactions tool down" switch). The analyst console is never affected by a session switch.
- Card blocks of the simulated core: per (session, card) so two judges using the same demo
  customer don't see each other's blocks. A card blocked with the customer's yes stays blocked
  for the rest of that session; a NEW session of the same demo customer starts with the card
  active again (judge isolation), so there is no unblock to simulate. A real core would block
  per card, not per session. Nothing here moves money.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_blocks: dict[tuple[str, str], datetime] = {}  # (session_id, card_id) -> blocked_at
_fail_all = False
_fail_sessions: set[str] = set()


def set_tool_failure(session_id: str | None, on: bool) -> None:
    global _fail_all
    with _lock:
        if session_id is None:
            _fail_all = on
        elif on:
            _fail_sessions.add(session_id)
        else:
            _fail_sessions.discard(session_id)


def tool_failure_on(session_id: str | None) -> bool:
    """Whether tools fail for this session (its own switch or the process-wide one)."""
    with _lock:
        return _fail_all or (session_id is not None and session_id in _fail_sessions)


def failing_for(session_id: str | None, actor: str) -> bool:
    """The rule the backends apply: a session switch never affects the analyst."""
    with _lock:
        return _fail_all or (actor != "analyst" and session_id is not None and session_id in _fail_sessions)


def reset(session_id: str | None = None) -> None:
    """Forget card blocks and failure switches: of one session, or of all when None."""
    global _fail_all
    with _lock:
        if session_id is None:
            _blocks.clear()
            _fail_sessions.clear()
            _fail_all = False
        else:
            for k in [k for k in _blocks if k[0] == session_id]:
                del _blocks[k]
            _fail_sessions.discard(session_id)


def block(session_id: str, card_id: str) -> datetime:
    """Idempotent: returns when the card was blocked in this session."""
    with _lock:
        _blocks.setdefault((session_id, card_id), datetime.now(timezone.utc))
        return _blocks[(session_id, card_id)]


def blocked_cards(session_id: str) -> set[str]:
    with _lock:
        return {cid for (sid, cid) in _blocks if sid == session_id}
