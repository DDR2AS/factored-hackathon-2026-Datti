"""Chat API service (owner: arturo): sessions + orchestrator + stores behind the three
customer routes. ``src/handlers/api.py`` is only the HTTP router around this.

- ``create_session``: allow-listed demo_key -> token (returned once) + welcome turn.
- ``chat``: authenticate, idempotency by client_msg_id, 30-turn cap, button ids -> actions,
  one orchestrator turn, contract validation of the reply, save.
- ``get_case``: owner = the demo customer AND the session that created the case; anything
  else, including a case that does not exist, is the same 403.
- Lane B lifecycle (``conversation.lifecycle``): the case is started when the turn opens it
  and advanced on every read (chat turn, GET /cases, analyst routes). Once an analyst decided,
  the resolution is in the case card and is also said at the start of the next chat reply.
- Judge-mode switches (``demo_switches``, RF-22): ``tools_down``, ``model_slow`` and
  ``fast_clock`` (demo clock of the SLA timers, 1 day = 1 minute) per session, ``expire_session``. They exist only because every session today is a demo
  session (``source == "demo"``); a production session has no switches (400). In production
  this code path would not exist.
- ``analyst``: the console service (``conversation.analyst``), used by ``/analyst/*``.
- Conversation for the analyst: every turn appends {role, text, language, source, at} to the
  session's redacted transcript (the customer's text as redacted by the orchestrator; a
  button as "[botón] <label>"; the reply as the template text). When the session opens a case,
  the whole transcript goes into that case's conversation (ReviewStore) and every later turn
  is appended there too. Only this session's turns, only into its own case.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from pydantic import ValidationError

from conversation import contract as C
from conversation import demo_gateway, lane_rules
from conversation.analyst import AnalystService
from conversation.lifecycle import CaseLifecycle, make_lifecycle
from conversation.orchestrator import Orchestrator, SessionInfo, load_llm_client
from conversation.sessions import SessionRecord, SessionService, iso, utc_now
from conversation.store import CaseVersionConflict, Stores, make_stores
from conversation.templates import format_duration, render

NOT_AUTHORIZED = "not authorized"  # one message for "not yours" and "does not exist"
MAX_SESSION_TRANSCRIPT = 60  # turns kept in the session (the case keeps up to 200)
_BUTTON_PREFIX = {"es": "[botón]", "pt": "[botão]"}


@dataclass
class ChatApp:
    sessions: SessionService
    orchestrator: Orchestrator
    stores: Stores
    lifecycle: CaseLifecycle | None = None
    analyst: AnalystService | None = None

    # ------------------------------------------------------------ POST /session

    def create_session(self, body: Any, source_ip: str | None = None) -> dict[str, Any]:
        req = _validate(C.SessionRequest, body)
        self.sessions.check_create_rate(source_ip)
        try:
            profile = demo_gateway.profile_for_demo_key(req.demo_key)
            customer_id = demo_gateway.customer_id_for_demo_key(req.demo_key)
        except demo_gateway.NotFound:
            raise C.ApiFailure("invalid_request", "demo_key is not in the allow list")
        language = req.language or profile.language
        token, record = self.sessions.create(req.demo_key, customer_id, req.channel, language)
        record.conversation = self.orchestrator.new_state(profile, language)
        text, lang, buttons = self.orchestrator.welcome(record.conversation)
        welcome = {"turn": 0, "reply_text": text, "reply_language": lang, "reply_source": "template",
                   "buttons": self._issue(record, buttons), "input_mode": "free_text"}
        record.conversation["transcript"] = [self._turn_entry("system", text, lang, "template")]
        self.sessions.save(record)
        response = {
            "session_token": token,
            "expires_at": record.expires_at,
            "customer": {"display_name": profile.display_name, "language": profile.language,
                         "locale": profile.locale, "country": profile.country},
            "welcome": welcome,
            "synthetic": True,
        }
        return C.SessionResponse.model_validate(response).model_dump()

    # ------------------------------------------------------------ POST /chat

    def chat(self, token: str | None, body: Any) -> dict[str, Any]:
        record = self.sessions.authenticate(token)
        req = _validate(C.ChatRequest, body)
        with self.sessions.turn_guard(record.session_id, req.client_msg_id):
            record = self.sessions.authenticate(token)  # fresh copy under the session lock
            cached = record.replies.get(req.client_msg_id)
            if cached is not None:
                return cached
            if req.demo_switches is not None:
                if req.switches_only:
                    self.sessions.check_switch_limit(record)  # own cap: not turns (SEC-04)
                self._apply_switches(record, req.demo_switches)  # raises 401 on expire_session
                if req.switches_only:
                    return self._switches_reply(record, req)
            self.sessions.check_turn_limit(record)
            action = self.sessions.take_button(record, req.button_id) if req.button_id else None
            label = None
            if action is not None:
                action = dict(action)
                label = action.pop("_label", None)
            turn_no = record.turns + 1
            open_case = (record.conversation or {}).get("case_id")
            if open_case:
                self._advance(open_case)  # the orchestrator reads the case as it is now
            info = SessionInfo(session_id=record.session_id, customer_id=record.customer_id,
                               channel=record.channel, source=record.source, model_slow=record.model_slow)
            try:
                result = self.orchestrator.turn(info, record.conversation, turn_no, message=req.message,
                                                action=action)
            except CaseVersionConflict:
                # The analyst or the lifecycle wrote the case during this turn. The session was not
                # saved, so the same client_msg_id can simply be sent again.
                self.sessions.save(record)  # keep the switches applied above
                raise C.ApiFailure("conflict", "the case changed while answering; send again", retryable=True)
            record.conversation = result.state
            buttons = self._issue(record, result.buttons)
            steps = [{k: v for k, v in s.items() if not k.startswith("_")} for s in result.trace_summary["steps"]]
            case_card = result.case_card
            case_id = result.state.get("case_id")
            if case_id and self.lifecycle is not None:
                # no-op unless it is a lane B case still open; schedules its SLA timers
                self.lifecycle.start(case_id, fast_clock=record.fast_clock)
                case_card = self._advance(case_id) or case_card
            response = {
                "turn": turn_no,
                "reply_text": self._with_resolution(record, case_id, result.reply_text, result.state),
                "reply_language": result.reply_language,
                "reply_source": "template",
                "buttons": buttons,
                "input_mode": result.input_mode,
                "progress": result.progress,
                "case_card": case_card,
                "lane": result.lane,
                "degraded": result.degraded,
                "trace_id": result.trace_id,
                "trace_summary": {**result.trace_summary, "steps": steps},
                "poll_after_ms": self._poll(case_card),
                "demo_switches": self._switch_state(record),
                "synthetic": True,
            }
            response = C.ChatResponse.model_validate(response).model_dump()
            self._log_turn(record, result, label, response["reply_language"])
            record.turns = turn_no
            record.replies[req.client_msg_id] = response
            self.sessions.save(record)
            return response

    # ------------------------------------------------------------ GET /cases/{case_id}

    def get_case(self, token: str | None, case_id: str | None) -> dict[str, Any]:
        record = self.sessions.authenticate(token)
        stored = self.stores.cases.get(case_id) if case_id else None
        if (stored is None or stored.case.customer_id != record.customer_id
                or stored.session_id != record.session_id):
            raise C.ApiFailure("not_authorized", NOT_AUTHORIZED)
        view = self._advance(case_id)
        res = (view or {}).get("resolution")
        if res and _delivery_key(case_id, res["sent_at"]) not in record.resolutions_delivered:
            # The live card shows the resolution as a human message as soon as it reads it
            # here, so the next chat reply must not repeat it (integration 29 sep). A channel
            # that never reads GET /cases (WhatsApp) still gets it once in the chat.
            record.resolutions_delivered = record.resolutions_delivered + [_delivery_key(case_id, res["sent_at"])]
            self.sessions.save(record)
        return C.CaseResponse.model_validate({"case": view, "poll_after_ms": self._poll(view)}).model_dump()

    # ------------------------------------------------------------ demo switches (RF-22)

    def _apply_switches(self, record: SessionRecord, patch: C.DemoSwitchesPatch) -> None:
        """Only the keys present change, before the message of the same request. Demo only."""
        if record.source != "demo":
            raise C.ApiFailure("invalid_request", "demo_switches exist only in demo sessions")
        if patch.expire_session is True:
            # Only the session's own failure switch goes. A card blocked with the customer's
            # yes stays blocked: the analyst console must keep showing it (SEC-01).
            demo_gateway.set_tool_failure(record.session_id, False)
            self.sessions.expire(record)
            raise C.ApiFailure("session_expired", "session expired (demo switch)")
        if patch.tools_down is not None:
            demo_gateway.set_tool_failure(record.session_id, patch.tools_down)
        if patch.model_slow is not None:
            record.model_slow = patch.model_slow
        if patch.fast_clock is not None:
            record.fast_clock = patch.fast_clock
            case_id = (record.conversation or {}).get("case_id")
            if case_id and self.lifecycle is not None:
                self.lifecycle.set_fast_clock(case_id, patch.fast_clock)  # the case already open

    def _switches_reply(self, record: SessionRecord, req: C.ChatRequest) -> dict[str, Any]:
        """A short template reply that does not advance the conversation (not a turn)."""
        snap = self.orchestrator.snapshot(record.conversation)
        if snap["case_id"]:
            snap["case_card"] = self._advance(snap["case_id"]) or snap["case_card"]
        patch = req.demo_switches
        lang = snap["language"]
        parts = []
        if patch.tools_down is not None:
            parts.append(render("switch.tools_down_on" if patch.tools_down else "switch.tools_down_off", lang))
        if patch.model_slow is not None:
            parts.append(render("switch.model_slow_on" if patch.model_slow else "switch.model_slow_off", lang))
        if patch.fast_clock is not None:
            parts.append(self._fast_clock_text(patch.fast_clock, lang))
        state = self._switch_state(record)
        trace_id = "tr-" + secrets.token_hex(8)
        rules_version = lane_rules.load_rules()["version"]
        event = {
            "trace_id": trace_id, "session_id": record.session_id, "case_id": snap["case_id"],
            "turn": record.turns, "step": 1, "actor": "rule", "name": "demo_switches",
            "input_ref": f"msg:{record.session_id}:switches",
            "output_ref": (f"tools_down:{state['tools_down']};model_slow:{state['model_slow']};"
                           f"fast_clock:{state['fast_clock']}"),
            "latency_ms": 0, "tokens_in": None, "tokens_out": None, "cost_usd": 0.0,
            "version": rules_version, "error_code": None, "ts": utc_now().isoformat(), "source": record.source,
        }
        try:
            self.stores.traces.emit(event)
        except Exception:
            pass  # tracing never breaks a reply
        response = {
            "turn": record.turns,  # not a turn: repeats the last number, does not count for the cap
            "reply_text": " ".join(parts),
            "reply_language": lang,
            "reply_source": "template",
            "buttons": [],  # the pending buttons stay valid; the UI keeps showing them
            "input_mode": "free_text",
            "progress": snap["progress"],
            "case_card": snap["case_card"],
            "lane": snap["lane"],
            "degraded": [],
            "trace_id": trace_id,
            "trace_summary": {"steps": [{"actor": "rule", "name": "demo_switches", "latency_ms": 0,
                                         "version": rules_version, "error_code": None}],
                              "latency_ms": 0, "cost_usd": 0.0, "model_id": None, "tokens_in": None,
                              "tokens_out": None, "rule_id": "demo_switches", "rules_version": rules_version},
            "poll_after_ms": self._poll(snap["case_card"]),
            "demo_switches": state,
            "synthetic": True,
        }
        response = C.ChatResponse.model_validate(response).model_dump()
        record.replies[req.client_msg_id] = response
        record.switch_requests += 1
        self.sessions.save(record)
        return response

    # ------------------------------------------------------------ conversation for the analyst

    def _turn_entry(self, role: str, text: str, language: str, source: str) -> dict[str, Any]:
        return {"role": role, "text": text, "language": language if language in ("es", "pt") else "es",
                "source": source, "at": self.sessions.clock().isoformat()}

    def _log_turn(self, record: SessionRecord, result, label: str | None, language: str) -> None:
        """Append this turn (redacted) to the session transcript and, once there is a case, to
        the case's conversation. Never breaks a reply."""
        if label is not None:
            said = f"{_BUTTON_PREFIX.get(language, '[botón]')} {label}"
        else:
            said = result.customer_text or ""
        turns = [self._turn_entry("customer", said, language, "human"),
                 self._turn_entry("system", result.reply_text, language, "template")]
        conv = record.conversation
        conv["transcript"] = ((conv.get("transcript") or []) + turns)[-MAX_SESSION_TRANSCRIPT:]
        case_id = conv.get("case_id")
        if not case_id:
            return
        append = getattr(self.stores.reviews, "append_conversation", None)
        if append is None:
            return
        try:
            if conv.get("transcript_case") != case_id:
                append(case_id, conv["transcript"])  # the story so far, from the welcome
                conv["transcript_case"] = case_id
            else:
                append(case_id, turns)
        except Exception:
            pass

    # ------------------------------------------------------------ helpers

    def _advance(self, case_id: str) -> dict[str, Any] | None:
        """Apply the due lifecycle transitions and return the customer view of the case."""
        if self.lifecycle is not None:
            self.lifecycle.advance(case_id)
        return self.orchestrator.case_view(case_id)

    def _with_resolution(self, record: SessionRecord, case_id: str | None, reply: str,
                         state: dict[str, Any]) -> str:
        """The analyst-approved resolution, said once at the start of the next chat reply. A
        reopened case can get a second one (a new ``sent_at``), which is said once too."""
        if not case_id:
            return reply
        stored = self.stores.cases.get(case_id)
        resolution = stored.case.resolution if stored else None
        if resolution is None:
            return reply
        key = _delivery_key(case_id, resolution.sent_at.isoformat())
        if key in record.resolutions_delivered:
            return reply
        record.resolutions_delivered = record.resolutions_delivered + [key]
        intro = render("resolution_intro", resolution.language, state.get("country"), case_id=case_id)
        return f"{intro} {resolution.text} {reply}".strip()

    def _poll(self, view: dict[str, Any] | None) -> int | None:
        """poll_after_ms of a customer view: also while the case has SLA timers pending."""
        pending = bool(view and self.lifecycle is not None
                       and self.lifecycle.has_pending_timers(view["case_id"]))
        return poll_after_ms(view, pending_timers=pending)

    def _fast_clock_text(self, on: bool, lang: str) -> str:
        """Confirmation of the fast_clock switch with the scale the lifecycle really applies
        ("1 día = 1 minuto" at 1/1440, "1 día = 0,9 segundos" at 0.00001); neutral without one."""
        if not on:
            return render("switch.fast_clock_off", lang)
        scale = getattr(self.lifecycle, "sla_scale", None)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 1:
            return render("switch.fast_clock_on_neutral", lang)
        return render("switch.fast_clock_on", lang, day=format_duration(86400 * scale, lang))

    @staticmethod
    def _switch_state(record: SessionRecord) -> dict[str, bool]:
        """Current switches of the session (DemoSwitchState). ``tools_down`` also reports the
        process-wide ``local_api.py --fail tools``; ``--fail model`` is not a session switch."""
        return {"tools_down": demo_gateway.tool_failure_on(record.session_id), "model_slow": record.model_slow,
                "fast_clock": record.fast_clock}

    def _issue(self, record: SessionRecord, buttons) -> list[dict[str, str]]:
        """Replace the pending buttons with this turn's: new opaque single-use ids."""
        record.pending_buttons = {}
        out = []
        for b in buttons:
            bid = self.sessions.new_button_id()
            record.pending_buttons[bid] = {**b.action, "_label": b.label}  # label: for the transcript
            out.append({"id": bid, "label": b.label, "kind": b.kind})
        return out


def _delivery_key(case_id: str, sent_at_iso: str) -> str:
    """One resolution of one case (a reopened case may get another one later)."""
    return f"{case_id}@{sent_at_iso}"


def poll_after_ms(view: dict[str, Any] | None, pending_timers: bool = False) -> int | None:
    """> 0 while a lane B case can still change on its own (open, investigating,
    awaiting_analyst, or SLA timers still pending); None otherwise, so the live card stops."""
    if view and view.get("lane") == "B" and (view.get("status") in C.POLLING_STATUSES or pending_timers):
        return C.CASE_POLL_MS
    return None


def _validate(model, body: Any):
    if not isinstance(body, dict):
        raise C.ApiFailure("invalid_request", "body must be a JSON object")
    try:
        return model.model_validate(body)
    except ValidationError as e:
        fields = sorted({".".join(str(p) for p in err["loc"]) or "body" for err in e.errors()})
        raise C.ApiFailure("invalid_request", "invalid fields: " + ", ".join(fields))


def build_app(env: dict[str, str] | None = None, clock: Callable[[], datetime] | None = None,
              stores: Stores | None = None, sleep: Callable[[float], None] | None = None,
              llm=None, use_env_llm: bool = True, ttl_minutes: float | None = None,
              investigation_delay: float | None = None, llm_timeout: float | None = None,
              sla_scale: float | None = None) -> ChatApp:
    """Wire the app from environment variables (#9); tests inject clock, stores, sleep, llm,
    the investigation delay (``LOCAL_INVESTIGATION_DELAY_SECONDS``, 4 s by default), the G1
    timeout (``LLM_TIMEOUT_SECONDS``) and the fast_clock factor (``LOCAL_SLA_SCALE``, 1/1440)."""
    env = os.environ if env is None else env
    stores = stores or make_stores(env)
    clock = clock or utc_now
    if getattr(stores.cases, "clock", "absent") is None:
        stores.cases.clock = clock  # updated_at of the memory store follows the app clock
    if llm is None and use_env_llm:
        llm = load_llm_client(env)
    # A session that expires by TTL takes its judge switch tools_down with it (SEC-02).
    sessions = SessionService(stores.sessions, clock=clock, ttl_minutes=ttl_minutes,
                              on_expire=lambda sid: demo_gateway.set_tool_failure(sid, False))
    kwargs: dict[str, Any] = {} if sleep is None else {"sleep": sleep}
    if llm_timeout is not None:
        kwargs["llm_timeout"] = llm_timeout
    orch = Orchestrator(stores.cases, stores.traces, llm=llm, clock=clock, **kwargs)
    lifecycle = make_lifecycle(env, stores, clock=clock, delay_seconds=investigation_delay, sla_scale=sla_scale,
                               **({} if sleep is None else {"sleep": sleep}))
    analyst = AnalystService(stores, lifecycle, clock=clock)
    return ChatApp(sessions=sessions, orchestrator=orch, stores=stores, lifecycle=lifecycle, analyst=analyst)


__all__ = ["ChatApp", "build_app", "iso"]
