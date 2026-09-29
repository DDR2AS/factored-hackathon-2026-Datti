"""Conversation orchestrator (owner: arturo): one customer turn in, one reply out.

understand -> find -> verify -> decide -> case_open | done
(docs/interfaz_chat_requerimientos.md sections 3.2, 4 and 7).

Hard rules this module keeps:
- The lane is decided ONLY by ``lane_rules.evaluate`` (rules/lane_rules.yaml). Models and
  keyword stand-ins (classifier, extractor, ranker) only produce evidence.
- Nothing moves money. No reply promises a refund or compensation (templates are checked
  by tests; there is no free generation here).
- A card is blocked only from the server-issued "yes" button, and the reply reports the
  status read back from the gateway.
- Free text is redacted (``redact``) before the extractor/LLM sees it and before it is
  stored as ``customer_statement``. Traces carry no free text: messages go by reference.
- Tools go through ``_tool``: 2 retries with backoff, then ``tool_failure`` -> lane C with
  the case marked incomplete and ``degraded: ["tool_unavailable"]``.
- The LLM extractor (G1, #8 ``complete()`` through ``conversation.llm_port``; details in
  ``conversation.g1``) is optional: ``LLM_PROVIDER`` mock | none | bedrock. Its output is
  validated with the schema and guarded against hallucination; the lane C flags of the model
  are OR-ed with the pattern lists. On timeout (``LLM_TIMEOUT_SECONDS``) or an unavailable
  provider the regex extractor answers with ``degraded: ["model_timeout"]``; without a mock
  fixture the regex answers without degrading. Replies always come from templates in M1
  (``reply_source: "template"``): the model writes no text the customer reads.

The session layer (``conversation.app``) owns tokens, button ids and idempotency; this module
receives the conversation state as a dict, returns the new one, and never sees a token.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from conversation import classifier as classifier_mod
from conversation import demo_gateway as default_gateway
from conversation import extract as extract_mod
from conversation import g1 as g1_mod
from conversation import lane_rules, ranker as ranker_mod
from conversation.case import (
    Action, CaseEvidence, CaseRecord, CaseStatus, Clock, Handoff, Intent, Lane, Match, MatchCandidate,
    Promise, RiskEvidence, customer_view, new_case_id,
)
from conversation.lang import detect_language
from conversation.llm_port import LLMClient, load_llm_client  # noqa: F401 (re-exported for app.py)
from conversation.promise import compute_promise
from conversation.redact import redact
from conversation.store import CaseStore, TraceSink
from conversation.templates import format_date, format_datetime, format_money, render
from conversation.textnorm import fold

MONEY_TYPES = ("unrecognized_charge", "wrong_fee")
OTHER_TYPES = ("app", "branch", "service")
SEARCH_SLOTS = ("amount", "date", "merchant_text")
CLAIMED_SLOTS = ("amount", "currency", "date", "merchant_text", "card_last4")
C_FLAGS = ("asks_for_human", "mentions_regulator", "asks_compensation", "complaint_about_person")
HIGH_PRIORITY_RULES = {"regulator_or_legal", "repeat_complainer", "high_amount", "high_fraud_score",
                       "lost_card", "compensation_requested"}
SEARCH_WINDOW_DAYS = 7      # +-7 days around a date the customer said
DEFAULT_LOOKBACK_DAYS = 30  # otherwise the last 30 days up to the reference date
FRAUD_SCORE_QUEUE = 30      # fraud_score above this goes to the fraud queue, whatever C rule fired
MAX_CHOICES = 3
MAX_STATEMENTS = 5
MAX_SIDE_REQUESTS = 3
RESTORABLE_PHASES = ("details", "confirm", "choose", "not_found", "recognize", "resolved")

_CASE_NUMBER = re.compile(r"\bEV-[A-Z0-9]{4,}\b", re.IGNORECASE)
# The customer disagrees with the answer of a person (folded text: no accents, lowercase).
_DISAGREE = re.compile(
    r"\b(?:no\s+estoy\s+(?:de\s+acuerdo|conforme)|no\s+acepto\s+(?:la|esa|su|tu)\s+respuesta|segunda\s+opini[oa]o?n?|"
    r"reabr(?:ir|an|a|e|am)\s+(?:el|mi|o|meu)\s+caso|nao\s+concordo|discordo|nao\s+aceito\s+a\s+resposta)\b")
# Queues that a later "talk to a person" must not downgrade to the general complaints queue.
_QUEUE_RANK = {"fraud": 3, "regulator": 3, "senior": 2, "cards": 2}
_LANG_ES_WORDS = re.compile(r"\b(?:espanol|espanhol|castellano)\b")
_LANG_PT_WORDS = re.compile(r"\bportugues\b")
_FEE_WORDS = re.compile(r"\b(?:comision\w*|comiss\w*|tarifa\w*|cuota\s+de\s+manejo|mantenimiento|anualidad|taxa\w*|manutencao|anuidade)\b")
_DUP_WORDS = re.compile(r"\b(?:dos\s+veces|2\s+veces|doble|duplicad\w*|repetid\w*|duas\s+vezes|em\s+dobro|dobrad\w*)\b")


# ---------------------------------------------------------------- LLM client (#8, conversation.llm_port)

LlmClient = LLMClient  # old name, kept for callers

_LLM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")


# ---------------------------------------------------------------- conversation state

class ConversationState(BaseModel):
    """Per-session conversation state, JSON-ready (stored inside the session record)."""

    model_config = ConfigDict(extra="ignore")

    language: str = "es"
    country: Optional[str] = None
    locale: Optional[str] = None
    display_name: str = ""
    reference_date: Optional[str] = None

    phase: str = "start"  # start|details|confirm|choose|not_found|recognize|resolved|block|language|closed
    complaint_type: Optional[str] = None
    kind: Optional[str] = None  # "fee" | "duplicate" | None: narrows the search
    intent: Optional[dict[str, Any]] = None  # last classifier output (#4)
    lost_card: bool = False

    claimed: dict[str, Any] = Field(default_factory=lambda: {k: None for k in CLAIMED_SLOTS})
    flags: dict[str, bool] = Field(default_factory=lambda: {k: False for k in C_FLAGS})
    statements: list[str] = Field(default_factory=list)
    asked: list[str] = Field(default_factory=list)

    search_key: Optional[str] = None
    offered: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    match: Optional[dict[str, Any]] = None
    txns: dict[str, dict[str, Any]] = Field(default_factory=dict)  # offered/confirmed txns (to_dict)

    chosen_id: Optional[str] = None
    charge_confirmed: Optional[bool] = None
    customer_recognizes: Optional[bool] = None
    customer_accepts_explanation: Optional[bool] = None
    verified: bool = False
    verification: dict[str, Any] = Field(default_factory=dict)
    prior_complaints: Optional[int] = None
    tool_failure: bool = False
    # An optional lookup failed (e.g. prior contacts during a handoff): the case is marked
    # incomplete, but the reply is the normal one (the customer did not ask for movements).
    evidence_incomplete: bool = False
    actions: list[dict[str, Any]] = Field(default_factory=list)
    prev_phase: Optional[str] = None  # the phase to go back to after the language question
    # Requests outside the open complaint (out of scope, another charge), redacted, for the person.
    side_requests: list[str] = Field(default_factory=list)
    # Redacted transcript of this session (written by the app layer, conversation.app). Copied
    # into the case's conversation for the analyst once a case exists (transcript_case).
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    transcript_case: Optional[str] = None

    turns_without_progress: int = 0
    lane: Optional[str] = None
    rule_id: Optional[str] = None
    case_id: Optional[str] = None
    block: Optional[dict[str, Any]] = None  # {card_id, last4, status}


@dataclass
class ButtonSpec:
    label: str
    kind: str  # confirm | deny | choice | handoff
    action: dict[str, Any]


@dataclass
class TurnResult:
    """What the session layer needs: the ChatResponse fields (buttons without ids yet), the
    button actions to register, and the new state."""

    reply_text: str
    reply_language: str
    buttons: list[ButtonSpec]
    input_mode: str
    progress: dict[str, Any]
    case_card: dict[str, Any] | None
    lane: str | None
    degraded: list[str]
    trace_id: str
    trace_summary: dict[str, Any]
    state: dict[str, Any]
    # The customer's message as stored and as the model saw it (redacted); None for a button.
    customer_text: str | None = None


@dataclass
class SessionInfo:
    """The verified session as the orchestrator sees it (no token)."""

    session_id: str
    customer_id: str
    channel: str
    source: str = "demo"
    model_slow: bool = False  # judge-mode switch: G1 of this session times out (demo only)


class _ToolFailed(Exception):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


@dataclass
class _Turn:
    session: SessionInfo
    turn: int
    trace_id: str
    started: float
    steps: list[dict[str, Any]] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    model_id: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    customer_text: str | None = None
    rule_id: str | None = None
    rules_version: str | None = None
    parts: list[str] = field(default_factory=list)
    buttons: list[ButtonSpec] = field(default_factory=list)
    input_mode: str = "free_text"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def degrade(self, what: str) -> None:
        if what not in self.degraded:
            self.degraded.append(what)


# ---------------------------------------------------------------- orchestrator

class Orchestrator:
    def __init__(self, cases: CaseStore, traces: TraceSink, gateway: Any = default_gateway,
                 llm: LlmClient | None = None, clock: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], None] = time.sleep, retries: int = 2,
                 backoff: tuple[float, ...] = (0.2, 0.6), llm_timeout: float | None = None) -> None:
        self.cases = cases
        self.traces = traces
        self.gw = gateway
        self.llm = llm
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.retries = retries
        self.backoff = backoff
        if llm_timeout is None:
            try:
                llm_timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS") or 8)
            except ValueError:
                llm_timeout = 8.0
        self.llm_timeout = llm_timeout

    # ------------------------------------------------------------ public

    def new_state(self, profile: Any, language: str) -> dict[str, Any]:
        """Initial state from the server-side profile (demo_gateway.CustomerProfile)."""
        return ConversationState(
            language=language if language in ("es", "pt") else "es",
            country=profile.country, locale=profile.locale, display_name=profile.display_name,
            reference_date=profile.reference_date.isoformat(),
        ).model_dump(mode="json")

    def welcome(self, state: dict[str, Any]) -> tuple[str, str, list[ButtonSpec]]:
        """(reply_text, reply_language, buttons) of turn 0."""
        st = ConversationState.model_validate(state)
        lang, country = st.language, st.country
        buttons = [
            ButtonSpec(render("btn.start_unrecognized", lang, country), "choice",
                       {"a": "start", "ct": "unrecognized_charge"}),
            ButtonSpec(render("btn.start_fee", lang, country), "choice", {"a": "start", "ct": "wrong_fee"}),
            ButtonSpec(render("btn.start_lost_card", lang, country), "choice", {"a": "lost_card"}),
            ButtonSpec(render("btn.human", lang, country), "handoff", {"a": "human"}),
        ]
        return render("welcome", lang, country, name=st.display_name), lang, buttons

    def turn(self, session: SessionInfo, state: dict[str, Any], turn_no: int,
             message: str | None = None, action: dict[str, Any] | None = None) -> TurnResult:
        st = ConversationState.model_validate(state)
        t = _Turn(session=session, turn=turn_no, trace_id="tr-" + secrets.token_hex(8),
                  started=time.perf_counter())
        before = self._signature(st)
        try:
            if action is not None:
                self._on_action(st, t, action, before)
            else:
                self._on_message(st, t, message or "", before)
        except _ToolFailed:
            st.tool_failure = True
            t.degrade("tool_unavailable")
            t.parts, t.buttons, t.input_mode = [], [], "free_text"
            self._decide_and_act(st, t)
        return self._result(st, t)

    def snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """Progress, case card, lane and language of a conversation without running a turn
        (a switches-only /chat request answers with these)."""
        st = ConversationState.model_validate(state)
        return {"progress": self._progress(st), "case_card": self.case_view(st.case_id), "lane": st.lane,
                "language": st.language if st.language in ("es", "pt") else "es", "country": st.country,
                "case_id": st.case_id}

    def case_view(self, case_id: str | None) -> dict[str, Any] | None:
        if not case_id:
            return None
        stored = self.cases.get(case_id)
        return customer_view(stored.case) if stored else None

    # ------------------------------------------------------------ inputs

    def _on_message(self, st: ConversationState, t: _Turn, text: str, before: tuple) -> None:
        lang = self._step(t, "rule", "detect_language", None, lambda: detect_language(text, default=st.language),
                          out=lambda r: f"lang:{r}")
        if lang in ("es", "pt") and lang != st.language:
            st.language = lang  # the reply follows the message (RF-27); the switch is in the trace
        clean = self._step(t, "rule", "redact", None, lambda: redact(text, st.language))
        t.customer_text = clean
        intent = self._step(t, "model", "classify", classifier_mod.MODEL_VERSION,
                            lambda: classifier_mod.classify(clean, st.language),
                            out=lambda r: f"intent:{r['intent_class']}:{r['p']}")
        if intent["intent_class"] == "manipulation":
            # Template only: nothing merged, no tool, no case, no buttons.
            self._step(t, "rule", "manipulation_guard", None, lambda: None, error="manipulation")
            self._guard(t, "guard.manipulation")
            t.parts.append(render("manipulation", st.language, st.country))
            self._count_progress(st, before)
            return
        if st.phase == "block" and (st.block or {}).get("status") == "offered":
            # A card is blocked only with the server-issued button: free text (API, WhatsApp)
            # never counts as "yes", and the offer stays open instead of disappearing.
            self._repeat_block_offer(st, t)
            return
        if st.phase == "language":
            typed, only_language = self._typed_language(clean)
            chosen = typed or (lang if lang in ("es", "pt") else None)
            if chosen:
                st.language = chosen
                self._restore_phase(st)
                if only_language:  # "en español por favor": nothing else to read in the message
                    self._guard(t, "guard.language_chosen")
                    self._advance(st, t, before)
                    return
        st.intent = intent
        ext = self._extract(st, t, clean)
        no_keywords = not any(classifier_mod.scores(clean).values())
        has_slots = any(getattr(ext.slots, s) for s in SEARCH_SLOTS)
        if ("model_timeout" in t.degraded and st.case_id is None and no_keywords and not has_slots
                and not ext.complaint_type and not any(ext.flags.model_dump().values())):
            # The model did not answer and the patterns found nothing: fixed options.
            self._guard(t, "guard.model_slow")
            t.parts.append(render("model_slow", st.language, st.country))
            self._start_buttons(st, t)
            self._count_progress(st, before)
            return
        if st.case_id is not None:
            self._on_message_with_case(st, t, text, clean, intent, ext, no_keywords)
            return
        new_flags = self._merge(st, ext, intent)
        if intent["intent_class"] == "human_request" and not st.flags["asks_for_human"]:
            new_flags.add("asks_for_human")
            st.flags["asks_for_human"] = True
        if intent["intent_class"] == "lost_card" or ext.flags.lost_card:
            st.lost_card = True

        # A message with no keyword at all ("hola", "gracias", an emoji) is not a statement.
        # A regulator threat, a compensation request or a complaint about staff is content for
        # the person even without a charge; a bare "quiero hablar con una persona" is not.
        told_something = any(v for k, v in ext.flags.model_dump().items() if k != "asks_for_human")
        complaintish = bool(ext.complaint_type or has_slots or told_something
                            or intent["intent_class"] in ("dispute_charge", "dispute_fee")
                            or (intent["intent_class"] == "complaint_other" and not no_keywords))
        if complaintish:
            st.statements = (st.statements + [clean])[-MAX_STATEMENTS:]
            self._detect_kind(st, clean)

        c_trigger = bool(new_flags) or any(st.flags.values()) or st.lost_card
        if lang == "mixed" and not c_trigger:
            if st.phase != "language":
                st.prev_phase = st.phase
            t.parts.append(render("ask_language", st.language, st.country))
            t.buttons = [ButtonSpec(render("btn.lang_es", st.language), "choice", {"a": "lang", "lang": "es"}),
                         ButtonSpec(render("btn.lang_pt", st.language), "choice", {"a": "lang", "lang": "pt"})]
            t.input_mode = "buttons_only"
            st.phase = "language"
            self._guard(t, "guard.ask_language")
            self._count_progress(st, before)
            self._no_progress_handoff(st, t)
            return
        if not c_trigger and intent["intent_class"] == "case_status" and not ext.complaint_type:
            self._say_case_status(st, t, asked_number=bool(_CASE_NUMBER.search(text)))
            self._count_progress(st, before)
            self._no_progress_handoff(st, t)
            return
        if (not c_trigger and intent["intent_class"] in ("out_of_scope", "account_query")
                and not ext.complaint_type):
            self._note_side_request(st, clean)
            self._say_out_of_scope(st, t, offer_human=True)
            self._count_progress(st, before)
            if not self._no_progress_handoff(st, t):
                self._reask_pending(st, t)  # keep the open question and its buttons on screen
            return
        self._advance(st, t, before)

    def _on_message_with_case(self, st: ConversationState, t: _Turn, text: str, clean: str, intent: dict,
                              ext: extract_mod.Extraction, no_keywords: bool) -> None:
        """A message after the case exists. The case is updated (never a second one) and the
        slots of the case already opened are never overwritten by a new story."""
        stored = self.cases.get(st.case_id)
        if stored is None:
            self._say_case_status(st, t, asked_number=bool(_CASE_NUMBER.search(text)))
            return
        case = stored.case
        new_flags = self._merge_flags(st, ext, intent)
        lost_now = intent["intent_class"] == "lost_card" or ext.flags.lost_card
        if lost_now and (st.block or {}).get("status") != "blocked":
            st.lost_card = True
            if case.lane != Lane.C:
                self._decide_and_act(st, t)  # escalate the same case (lost_card rule), offer the block
                return
            self._lost_card_on_c_case(st, t, stored)
            return
        if new_flags and case.lane != Lane.C:
            self._decide_and_act(st, t)  # escalate the same case; never a second one
            return
        has_story = bool(ext.complaint_type or any(getattr(ext.slots, s) for s in SEARCH_SLOTS)
                         or intent["intent_class"] in ("dispute_charge", "dispute_fee")
                         or (intent["intent_class"] == "complaint_other" and not no_keywords))
        status_question = intent["intent_class"] == "case_status" and not has_story
        if case.lane == Lane.B and case.awaiting_customer and not status_question:
            self._customer_answer_to_b_case(st, t, stored, clean)  # CX-01/H1
            return
        if (case.lane == Lane.B and case.status in (CaseStatus.notified, CaseStatus.closed)
                and case.resolution is not None and _DISAGREE.search(fold(clean))):
            self._reopen_for_second_opinion(st, t, stored, clean)  # H6
            return
        if intent["intent_class"] in ("out_of_scope", "account_query") and not ext.complaint_type:
            self._note_side_request(st, clean)
            self._say_out_of_scope(st, t, offer_human=case.lane != Lane.C)
            self._guard(t, "guard.out_of_scope")
            return
        if case.lane == Lane.C and (has_story or new_flags):
            self._add_to_c_case(st, t, stored, clean, ext, intent, new_flags)
            return
        if has_story:
            # Another complaint after a case in A or B: say so; the open case keeps its charge.
            self._note_side_request(st, clean)
            self._guard(t, "guard.another_complaint")
            t.parts.append(render("other_charge_after_case", st.language, st.country, case_id=case.case_id))
            self._add_human_button(st, t, about="side")
            return
        self._say_case_status(st, t, asked_number=bool(_CASE_NUMBER.search(text)))

    # ------------------------------------------------------------ small helpers for messages

    @staticmethod
    def _typed_language(clean: str) -> tuple[str | None, bool]:
        """("es"|"pt"|None, whether the message is only that choice) for a typed answer to
        the language question ("en español por favor", "português")."""
        f = fold(clean)
        es, pt = bool(_LANG_ES_WORDS.search(f)), bool(_LANG_PT_WORDS.search(f))
        if es == pt:
            return None, False
        return ("es" if es else "pt"), len(f.split()) <= 5

    @staticmethod
    def _restore_phase(st: ConversationState) -> None:
        st.phase = st.prev_phase if st.prev_phase in RESTORABLE_PHASES else "details"
        st.prev_phase = None

    def _no_progress_handoff(self, st: ConversationState, t: _Turn) -> bool:
        """Three turns without progress hand off in every branch, not only in the main one."""
        if st.case_id is None and st.turns_without_progress >= 3:
            t.parts, t.buttons, t.input_mode = [], [], "free_text"
            self._decide_and_act(st, t)
            return True
        return False

    def _note_side_request(self, st: ConversationState, clean: str) -> None:
        note = " ".join(clean.split())[:200]
        if note and note not in st.side_requests:
            st.side_requests = (st.side_requests + [note])[-MAX_SIDE_REQUESTS:]

    def _reask_pending(self, st: ConversationState, t: _Turn) -> None:
        """After an out-of-scope answer, show again the question the customer had open."""
        if st.charge_confirmed is not True and st.phase in ("confirm", "choose") and st.offered:
            self._ask_next(st, t)
        elif st.phase == "recognize" and st.customer_recognizes is None:
            self._ask_next(st, t)
        elif st.phase == "resolved" and st.customer_accepts_explanation is None:
            self._ask_resolved(st, t)

    def _merge_flags(self, st: ConversationState, ext: extract_mod.Extraction, intent: dict) -> set[str]:
        """Only the C flags (and a person asked by intent); the slots are left alone."""
        new_flags = set()
        for k in C_FLAGS:
            if getattr(ext.flags, k) and not st.flags[k]:
                st.flags[k] = True
                new_flags.add(k)
        if intent["intent_class"] == "human_request" and not st.flags["asks_for_human"]:
            st.flags["asks_for_human"] = True
            new_flags.add("asks_for_human")
        return new_flags

    def _add_to_c_case(self, st: ConversationState, t: _Turn, stored, clean: str,
                       ext: extract_mod.Extraction, intent: dict, new_flags: set[str]) -> None:
        """What the customer tells after the handoff goes into the same case for the person."""
        case = stored.case
        self._guard(t, "guard.case_note")
        if clean and clean not in st.statements:
            st.statements = (st.statements + [clean])[-MAX_STATEMENTS:]
            previous = case.customer_statement or ""
            case.customer_statement = ((previous + " / " if previous else "") + clean)[:1000] or None
        said = ", ".join(f"{k}={getattr(ext.slots, k)}" for k in CLAIMED_SLOTS if getattr(ext.slots, k))
        case.handoff.open_questions.append(
            "Agregó después del traspaso" + (f" (dijo, sin verificar: {said})" if said else ""))
        ctype = ext.complaint_type or {"dispute_charge": "unrecognized_charge",
                                       "dispute_fee": "wrong_fee"}.get(intent["intent_class"])
        if case.subcategory is None and ctype:
            case.subcategory = ctype
        if st.complaint_type is None and ctype:
            st.complaint_type = ctype
        if case.evidence.transaction is None:  # nothing verified yet: what was said is shown as said
            for k in CLAIMED_SLOTS:
                value = getattr(ext.slots, k)
                if value and not st.claimed.get(k):
                    st.claimed[k] = value
        for f in sorted(new_flags):
            if f not in case.urgency_flags:
                case.urgency_flags.append(f)
        self.cases.put(case, stored.session_id)
        t.parts.append(render("case_note_added", st.language, st.country, case_id=case.case_id))

    def _reopen(self, case: CaseRecord, notes: list[str]) -> None:
        """Back to a person: status reopened (the lifecycle moves it to awaiting_analyst on the
        next read). The previous decision stays in open_questions, the labels and the trace;
        the case can be decided again."""
        d = case.analyst_decision
        if d is not None:
            what = d.action + (f" + {d.next}" if d.next else "")
            when = d.decided_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if d.decided_at else "-"
            notes = [f"Decisión anterior: {what}, por {d.decided_by or '-'} el {when}"] + notes
        for n in notes:
            if n not in case.handoff.open_questions:
                case.handoff.open_questions.append(n)
        case.analyst_decision = None
        case.resolution = None
        case.awaiting_customer = False
        case.status = CaseStatus.reopened

    def _add_statement(self, st: ConversationState, case: CaseRecord, clean: str) -> str:
        note = " ".join((clean or "").split())[:300]
        if clean and clean not in st.statements:
            st.statements = (st.statements + [clean])[-MAX_STATEMENTS:]
            previous = case.customer_statement or ""
            case.customer_statement = ((previous + " / " if previous else "") + clean)[:1000] or None
        return note

    def _customer_answer_to_b_case(self, st: ConversationState, t: _Turn, stored, clean: str) -> None:
        """The analyst asked for information and the customer answered: the answer (redacted)
        goes into the case and the case goes back to the analyst (CX-01/H1)."""
        case = stored.case
        self._guard(t, "guard.case_info_added")
        note = self._add_statement(st, case, clean)
        self._reopen(case, [f"El cliente respondió al pedido de información (sin verificar): «{note}»"])
        self.cases.put(case, stored.session_id)
        t.parts.append(render("case_info_added", st.language, st.country, case_id=case.case_id))

    def _reopen_for_second_opinion(self, st: ConversationState, t: _Turn, stored, clean: str) -> None:
        """The customer disagrees with the answer: the case goes back to a person, senior queue,
        high priority (H6; minimal version of the plan's second opinion: the console does not
        yet require a different analyst)."""
        case = stored.case
        self._guard(t, "guard.case_reopened")
        note = self._add_statement(st, case, clean)
        self._reopen(case, [f"Segunda opinión: el cliente no está de acuerdo con la respuesta (dijo: «{note}»)"])
        case.handoff.queue = "senior"
        case.handoff.priority = "high"
        self.cases.put(case, stored.session_id)
        t.parts.append(render("case_reopened", st.language, st.country, case_id=case.case_id))

    def _lost_card_on_c_case(self, st: ConversationState, t: _Turn, stored) -> None:
        """Lost or stolen card told after a lane C handoff: update the case and offer the block."""
        case = stored.case
        self._guard(t, "guard.lost_card_update")
        if "lost_card" not in case.urgency_flags:
            case.urgency_flags.append("lost_card")
        note = "Tarjeta perdida o robada: confirmar el bloqueo y la reposición"
        if note not in case.handoff.open_questions:
            case.handoff.open_questions.append(note)
        if case.handoff.queue not in ("fraud", "regulator"):
            case.handoff.queue = "cards"
        case.handoff.priority = "high"
        self.cases.put(case, stored.session_id)
        t.parts.append(render("lost_card_intro", st.language, st.country))
        if not self._offer_block(st, t):
            t.parts.append(render("lost_card_noted", st.language, st.country, case_id=case.case_id))

    def _repeat_block_offer(self, st: ConversationState, t: _Turn) -> None:
        block = st.block or {}
        self._guard(t, "guard.block_needs_button")
        t.parts.append(render("block_use_button", st.language, st.country))
        t.parts.append(render("block_offer", st.language, st.country, last4=block.get("last4", "")))
        self._block_buttons(st, t, block.get("card_id"), block.get("last4", ""))

    def _guard(self, t: _Turn, rule_id: str) -> None:
        """A fixed, versioned rule for turns that do not go through lane_rules (template-only
        answers, button follow-ups), so trace_summary.rule_id and rules_version are never empty."""
        if t.rule_id is None:
            version = lane_rules.load_rules()["version"]
            self._record(t, "rule", rule_id, 0, version, None, inp=self._msg_ref(t), out=f"rule:{rule_id}")
            t.rule_id, t.rules_version = rule_id, version

    def _on_action(self, st: ConversationState, t: _Turn, action: dict[str, Any], before: tuple) -> None:
        a = action.get("a")
        self._step(t, "rule", "button", None, lambda: None, inp=f"button:{a}")
        if a == "start":
            if st.complaint_type is None:
                st.complaint_type = action["ct"]
                if action["ct"] == "wrong_fee" and st.kind is None:
                    st.kind = "fee"
        elif a == "human":
            stored = self.cases.get(st.case_id) if st.case_id else None
            if action.get("about") == "side" and stored is not None and stored.case.lane == Lane.B:
                # A person for a request outside the open lane B complaint (a loan, another
                # charge): noted in that case; its lane and rule stay as they are.
                self._side_request_to_case(st, t, stored)
                return
            st.flags["asks_for_human"] = True
        elif a == "lost_card":
            st.lost_card = True
        elif a == "case_status":
            self._say_case_status(st, t, asked_number=False)
            return
        elif a == "lang":
            st.language = action["lang"]
            self._restore_phase(st)
        elif a in ("confirm", "choose"):
            txn_id = action["txn"]
            st.chosen_id = txn_id
            st.charge_confirmed = True
            st.duplicate_ids = action.get("dups", [])
            st.offered = []
            if st.match is not None:
                st.match["chosen_id"] = txn_id
        elif a == "none":
            st.excluded = sorted(set(st.excluded) | set(st.offered))
            st.offered, st.duplicate_ids = [], []
            st.phase = "details"
            st.search_key = None
            t.parts.append(render("none_of_these", st.language, st.country))
            self._count_progress(st, before, force_no_progress=True)
            if st.turns_without_progress >= 3:
                t.parts = []
                self._decide_and_act(st, t)
                return
            self._guard(t, "guard.none_of_these")
            self._add_human_button(st, t)
            return
        elif a == "recognize":
            st.customer_recognizes = bool(action["yes"])
        elif a == "resolved":
            st.customer_accepts_explanation = bool(action["yes"])
        elif a == "block":
            self._on_block(st, t, bool(action["yes"]), action.get("card"))
            return
        self._advance(st, t, before)

    # ------------------------------------------------------------ the pipeline

    def _advance(self, st: ConversationState, t: _Turn, before: tuple) -> None:
        """Gather the evidence this state needs (tools), count progress, decide, act."""
        c_now = any(st.flags.values()) or st.lost_card  # C already decided by the message: skip tools
        if not c_now:
            if st.complaint_type in MONEY_TYPES or st.complaint_type in OTHER_TYPES:
                if st.prior_complaints is None:
                    contacts = self._tool(st, t, "get_prior_contacts", lambda ctx: self.gw.get_prior_contacts(ctx),
                                          out=lambda r: f"contacts:{len(r)}")
                    st.prior_complaints = sum(1 for c in contacts if c.contact_type == "complaint")
            if st.complaint_type in MONEY_TYPES and st.phase != "language":
                if st.charge_confirmed is not True:
                    self._find(st, t)
                elif not st.verified:
                    self._verify(st, t)
        self._count_progress(st, before)
        self._decide_and_act(st, t)

    def _decide_and_act(self, st: ConversationState, t: _Turn) -> None:
        decision = self._evaluate(st, t)
        if decision.lane == Lane.C:
            self._handoff(st, t, decision)
        elif decision.lane == Lane.B:
            self._open_case_b(st, t, decision)
        elif decision.lane == Lane.A:
            self._lane_a(st, t, decision)
        else:
            self._ask_next(st, t)

    # ------------------------------------------------------------ evidence

    def _evidence(self, st: ConversationState) -> lane_rules.Evidence:
        v = st.verification if st.charge_confirmed else {}
        intent_class = "lost_card" if st.lost_card else (st.intent or {}).get("intent_class")
        return lane_rules.Evidence(
            intent_class=intent_class,
            complaint_type=st.complaint_type,
            identity_verified=True,  # the session is valid (allow list + live token); never from text
            tool_failure=st.tool_failure,
            turns_without_progress=st.turns_without_progress,
            asks_for_human=st.flags["asks_for_human"],
            mentions_regulator=st.flags["mentions_regulator"],
            asks_compensation=st.flags["asks_compensation"],
            complaint_about_person=st.flags["complaint_about_person"],
            prior_complaints=st.prior_complaints,
            amount_usd=Decimal(v["amount_usd"]) if v.get("amount_usd") is not None else None,
            fraud_score=v.get("fraud_score"),
            evidence_complete=self._evidence_complete(st),
            charge_found=True if st.chosen_id else None,
            charge_confirmed=st.charge_confirmed,
            customer_recognizes=st.customer_recognizes,
            already_reversed=v.get("already_reversed"),
            fee_matches_schedule=v.get("fee_matches_schedule"),
            charge_is_fee=v.get("charge_is_fee"),
            duplicate_found=v.get("duplicate_found"),
            duplicate_reversed=v.get("duplicate_reversed"),
            customer_accepts_explanation=st.customer_accepts_explanation,
        )

    @staticmethod
    def _evidence_complete(st: ConversationState) -> bool:
        """Minimum list per complaint type (section 7). Lives here, not in the YAML."""
        if st.complaint_type == "unrecognized_charge":
            return bool(st.charge_confirmed and st.verified and st.customer_recognizes is not None)
        if st.complaint_type == "wrong_fee":
            return bool(st.charge_confirmed and st.verified)
        if st.complaint_type in OTHER_TYPES:
            return bool(st.statements)
        return False

    def _evaluate(self, st: ConversationState, t: _Turn) -> lane_rules.LaneDecision:
        ev = self._evidence(st)
        d = self._step(t, "rule", "lane_rules", None, lambda: lane_rules.evaluate(ev),
                       out=lambda r: f"rule:{r.rule_id}:lane:{r.lane.value if r.lane else None}")
        t.steps[-1]["version"] = d.rules_version
        t.rule_id, t.rules_version = d.rule_id, d.rules_version
        return d

    # ------------------------------------------------------------ find

    def _find(self, st: ConversationState, t: _Turn) -> None:
        slots = {k: st.claimed.get(k) for k in CLAIMED_SLOTS}
        has_criteria = any(slots.get(s) for s in SEARCH_SLOTS) or st.kind in ("fee", "duplicate")
        key = repr((sorted(slots.items()), st.kind, sorted(st.excluded)))
        if st.search_key == key and st.phase in ("confirm", "choose", "not_found", "details"):
            return  # nothing new since the last search: re-ask the same question
        st.search_key = key
        st.offered, st.duplicate_ids = [], []
        if not has_criteria:
            st.phase = "details"
            return
        ref = date.fromisoformat(st.reference_date) if st.reference_date else self.clock().date()
        if slots.get("date"):
            said = date.fromisoformat(slots["date"])
            d_from, d_to = said - timedelta(days=SEARCH_WINDOW_DAYS), said + timedelta(days=SEARCH_WINDOW_DAYS)
        else:
            d_from, d_to = ref - timedelta(days=DEFAULT_LOOKBACK_DAYS), ref
        cands = self._tool(st, t, "find_candidate_txns",
                           lambda ctx: self.gw.find_candidate_txns(ctx, d_from, d_to, amount_hint=slots.get("amount"),
                                                                   currency=slots.get("currency")),
                           inp=f"window:{d_from.isoformat()}..{d_to.isoformat()}",
                           out=lambda r: f"txns:{len(r)}")
        cands = [c for c in cands if c.txn_id not in st.excluded]
        if st.kind == "fee":
            fees = [c for c in cands if c.txn_type == "fee"]
            if fees:
                cands = fees
        if st.kind == "duplicate":  # "dos veces": only charges that have an identical twin
            groups = {}
            for c in cands:
                groups.setdefault((c.merchant_id, c.amount, c.currency), []).append(c)
            dup = [c for g in groups.values() if len(g) > 1 for c in g]
            if dup:
                cands = dup
        if not cands:
            st.phase = "not_found"
            return
        ranked = self._step(t, "model", "rank_candidates", ranker_mod.MODEL_VERSION,
                            lambda: ranker_mod.rank_candidates(slots, cands),
                            out=lambda r: f"band:{r['band']}:top:{r['ranked'][0]['txn_id']}:{r['ranked'][0]['p']}")
        by_id = {c.txn_id: c for c in cands}
        probs = {r["txn_id"]: r["p"] for r in ranked["ranked"]}
        top_id = ranked["ranked"][0]["txn_id"]
        # Two identical charges share the probability (ranker note): ask the gateway.
        dups = self._tool(st, t, "find_duplicates", lambda ctx: self.gw.find_duplicates(ctx, top_id),
                          inp=f"txn:{top_id}", out=lambda r: "txns:" + ",".join(x.txn_id for x in r))
        group = [top_id] + [d.txn_id for d in dups if d.txn_id in by_id]
        p_group = sum(probs.get(x, 0.0) for x in group)
        band = ranker_mod.band_for(p_group) if len(group) > 1 else ranked["band"]
        st.match = {"candidates": ranked["ranked"][:10], "band": band, "p_top1": round(p_group, 4),
                    "model_version": ranked["model_version"], "chosen_id": None}
        choices: list[str] = []
        if band == "choose":
            seen = set(group[1:])
            for r in ranked["ranked"]:
                if r["txn_id"] in seen or r["p"] < 0.05:
                    continue
                choices.append(r["txn_id"])
                if len(choices) == MAX_CHOICES:
                    break
            if choices == [top_id]:
                # Only one plausible charge left: ask "¿Es este?", not "choose 1". The model band
                # stays "choose" in st.match, so the question says it is not a sure match.
                band = "confirm"
        if band == "confirm":
            # For a duplicate, the disputed one is the latest of the group.
            shown = max((by_id[x] for x in group), key=lambda x: (x.local_date, x.local_time, x.txn_id))
            st.offered = [shown.txn_id]
            st.duplicate_ids = sorted(x for x in group if x != shown.txn_id)
            st.txns = {x: by_id[x].to_dict() for x in group}
            st.phase = "confirm"
        elif band == "choose":
            st.offered = choices
            st.txns = {x: by_id[x].to_dict() for x in choices}
            st.phase = "choose"
        else:
            st.phase = "details" if self._next_slot(st) else "not_found"

    # ------------------------------------------------------------ verify

    def _verify(self, st: ConversationState, t: _Turn) -> None:
        txn_id = st.chosen_id
        txn = self._tool(st, t, "get_transaction", lambda ctx: self.gw.get_transaction(ctx, txn_id),
                         inp=f"txn:{txn_id}", out=lambda r: f"txn:{r.txn_id}")
        st.txns[txn_id] = txn.to_dict()
        dups = self._tool(st, t, "find_duplicates", lambda ctx: self.gw.find_duplicates(ctx, txn_id),
                          inp=f"txn:{txn_id}", out=lambda r: "txns:" + ",".join(x.txn_id for x in r))
        v: dict[str, Any] = {
            "fraud_score": txn.fraud_score,
            "amount_usd": str(txn.amount_usd) if txn.amount_usd is not None else None,
            "charge_is_fee": txn.txn_type == "fee",  # from the record's type, never from the message
            "duplicate_found": bool(dups),
            "duplicate_ids": [d.txn_id for d in dups],
        }
        reversal = None
        members = [txn_id] + [d.txn_id for d in dups]
        rev_by_member = {}
        for m in members:
            revs = self._tool(st, t, "get_reversals", lambda ctx, m=m: self.gw.get_reversals(ctx, m),
                              inp=f"txn:{m}", out=lambda r: "txns:" + ",".join(x.txn_id for x in r))
            rev_by_member[m] = revs
        own = rev_by_member[txn_id]
        v["already_reversed"] = bool(own)
        if own:
            reversal = own[0]
        if dups:
            dup_revs = [r for m in members for r in rev_by_member[m]]
            v["duplicate_reversed"] = bool(dup_revs)
            if dup_revs and reversal is None:
                reversal = dup_revs[0]
        if reversal is not None:
            v["reversal_date"] = reversal.local_date.isoformat()
            v["reversal_amount"] = str(abs(reversal.amount))
            v["reversal_currency"] = reversal.currency
        if st.complaint_type == "wrong_fee" or txn.txn_type == "fee":
            rule = self._tool(st, t, "get_fee_schedule", lambda ctx: self.gw.get_fee_schedule(ctx, txn.product_id),
                              inp=f"product:{txn.product_id}", out=lambda r: f"fees:{len(r.fees)}")
            item = self.gw.fee_item_for_txn(rule, txn)
            if item is not None:
                v["fee_matches_schedule"] = item.amount == abs(txn.amount)
                v["fee_name"] = item.description
                v["fee_amount"] = str(item.amount)
                v["fee_currency"] = rule.currency
        st.verification = v
        st.verified = True
        result = {k: v[k] for k in ("duplicate_found", "duplicate_reversed", "already_reversed",
                                    "fee_matches_schedule", "charge_is_fee") if k in v}
        st.actions.append({"tool": "verify_charge", "args": {"txn_id": txn_id}, "result": result,
                           "verified_at": self.clock().isoformat()})

    # ------------------------------------------------------------ ask

    @staticmethod
    def _next_slot(st: ConversationState) -> str | None:
        for s in SEARCH_SLOTS:
            if not st.claimed.get(s) and s not in st.asked:
                return s
        return None

    def _charge_text(self, st: ConversationState, txn: dict[str, Any]) -> str:
        return render("charge_line", st.language, st.country,
                      date=format_date(txn["local_date"], st.language), merchant=txn["merchant_name"],
                      amount=format_money(txn["amount"], txn["currency"], st.locale),
                      last4=txn.get("card_last4") or "----")

    def _add_human_button(self, st: ConversationState, t: _Turn, about: str | None = None) -> None:
        if not any(b.action.get("a") == "human" for b in t.buttons):
            action = {"a": "human", "about": about} if about else {"a": "human"}
            t.buttons.append(ButtonSpec(render("btn.human", st.language, st.country), "handoff", action))

    def _start_buttons(self, st: ConversationState, t: _Turn) -> None:
        lang, country = st.language, st.country
        t.buttons = [
            ButtonSpec(render("btn.start_unrecognized", lang, country), "choice",
                       {"a": "start", "ct": "unrecognized_charge"}),
            ButtonSpec(render("btn.start_fee", lang, country), "choice", {"a": "start", "ct": "wrong_fee"}),
            ButtonSpec(render("btn.start_case_status", lang, country), "choice", {"a": "case_status"}),
        ]
        self._add_human_button(st, t)

    def _confirm_key(self, st: ConversationState, txn: dict[str, Any]) -> str:
        """"confirm_charge" for a sure match; a softer question when the ranker was not sure
        (a single charge left in the "choose" band), and says so when the merchant named by the
        customer shares nothing with the charge's."""
        match = st.match or {}
        if match.get("band") == "confirm" or (match.get("p_top1") or 0) >= ranker_mod.CONFIRM_THRESHOLD:
            return "confirm_charge"
        said = st.claimed.get("merchant_text")
        if said and ranker_mod.merchant_similarity(said, txn.get("merchant_name") or "") == 0:
            return "confirm_charge_other_merchant"
        return "confirm_charge_uncertain"

    def _ask_next(self, st: ConversationState, t: _Turn) -> None:
        lang, country = st.language, st.country
        if st.phase == "language":
            t.parts.append(render("ask_language", lang, country))
            t.buttons = [ButtonSpec(render("btn.lang_es", lang), "choice", {"a": "lang", "lang": "es"}),
                         ButtonSpec(render("btn.lang_pt", lang), "choice", {"a": "lang", "lang": "pt"})]
            t.input_mode = "buttons_only"
            return
        if st.complaint_type is None or (st.complaint_type in OTHER_TYPES and not st.statements):
            t.parts.append(render("clarify_intent" if st.intent is None or st.intent.get("gate_action") != "human"
                                  else "not_understood", lang, country))
            self._start_buttons(st, t)
            return
        if st.charge_confirmed is not True:
            if st.phase == "confirm" and st.offered:
                txn = st.txns[st.offered[0]]
                charge = self._charge_text(st, txn)
                if st.duplicate_ids:
                    t.parts.append(render("confirm_duplicate", lang, country, charge=charge,
                                          n=len(st.duplicate_ids) + 1))
                else:
                    t.parts.append(render(self._confirm_key(st, txn), lang, country, charge=charge))
                t.buttons = [
                    ButtonSpec(render("btn.confirm_charge", lang, country), "confirm",
                               {"a": "confirm", "txn": st.offered[0], "dups": st.duplicate_ids}),
                    ButtonSpec(render("btn.none_of_these", lang, country), "deny", {"a": "none"}),
                ]
                self._add_human_button(st, t)
                t.input_mode = "buttons_only"
                return
            if st.phase == "choose" and st.offered:
                t.parts.append(render("choose_charge", lang, country, n=len(st.offered)))
                t.buttons = [ButtonSpec(self._charge_text(st, st.txns[x]), "choice", {"a": "choose", "txn": x})
                             for x in st.offered]
                t.buttons.append(ButtonSpec(render("btn.none_of_these", lang, country), "deny", {"a": "none"}))
                self._add_human_button(st, t)
                t.input_mode = "buttons_only"
                return
            slot = self._next_slot(st)
            if st.phase != "not_found" and slot:
                st.asked.append(slot)
                t.parts.append(render({"amount": "ask_amount", "date": "ask_date",
                                       "merchant_text": "ask_merchant"}[slot], lang, country))
                self._add_human_button(st, t)
                return
            t.parts.append(render("no_charge_found", lang, country))
            self._add_human_button(st, t)
            return
        if st.complaint_type == "unrecognized_charge" and st.customer_recognizes is None:
            st.phase = "recognize"
            t.parts.append(render("ask_recognize", lang, country))
            t.buttons = [
                ButtonSpec(render("btn.recognize_yes", lang, country), "confirm", {"a": "recognize", "yes": True}),
                ButtonSpec(render("btn.recognize_no", lang, country), "deny", {"a": "recognize", "yes": False}),
            ]
            self._add_human_button(st, t)
            t.input_mode = "buttons_only"
            return
        t.parts.append(render("not_understood", lang, country))
        self._add_human_button(st, t)

    def _say_case_status(self, st: ConversationState, t: _Turn, asked_number: bool = False) -> None:
        lang, country = st.language, st.country
        self._guard(t, "guard.case_status")
        stored = self.cases.get(st.case_id) if st.case_id else None
        if stored is None:
            # "con ese número" only when the customer gave one
            t.parts.append(render("case_status_not_found" if asked_number else "case_status_none", lang, country))
            if st.complaint_type is None:
                self._ask_next(st, t)
            else:
                self._add_human_button(st, t)
            return
        case = stored.case
        status = render(f"status.{case.status.value}", lang)
        # Once a person answered (notified/closed) the promised date is history: saying
        # "estimated resolution date: <future>" next to "closed" contradicts itself.
        answered = case.resolution is not None or case.status.value in ("notified", "closed", "resolved_in_contact")
        if case.promise.expected_date and not answered:
            t.parts.append(render("case_status", lang, country, case_id=case.case_id, status=status,
                                  expected_date=format_date(case.promise.expected_date, lang)))
        else:
            t.parts.append(render("case_status_short", lang, country, case_id=case.case_id, status=status))
        if case.awaiting_customer:
            t.parts.append(render("case_awaiting_answer", lang, country))
        # A case already with a person (lane C or escalated by the analyst) has no "talk to a
        # person" button: it would only move it to another queue (H2).
        if case.lane != Lane.C and case.status != CaseStatus.handed_off:
            self._add_human_button(st, t)

    def _say_out_of_scope(self, st: ConversationState, t: _Turn, offer_human: bool) -> None:
        self._guard(t, "guard.out_of_scope")
        t.parts.append(render("out_of_scope", st.language, st.country))
        if offer_human:
            self._add_human_button(st, t, about="side")

    def _ask_resolved(self, st: ConversationState, t: _Turn) -> None:
        lang, country = st.language, st.country
        t.parts.append(render("ask_resolved", lang, country))
        t.buttons = [ButtonSpec(render("btn.resolved_yes", lang, country), "confirm", {"a": "resolved", "yes": True}),
                     ButtonSpec(render("btn.resolved_no", lang, country), "deny", {"a": "resolved", "yes": False})]
        self._add_human_button(st, t)
        t.input_mode = "buttons_only"

    def _side_request_to_case(self, st: ConversationState, t: _Turn, stored) -> None:
        case = stored.case
        self._guard(t, "guard.side_request")
        notes = st.side_requests or ["(sin texto)"]
        for n in notes:
            q = f"Pidió hablar con una persona sobre otra solicitud (sin verificar): «{n}»"
            if q not in case.handoff.open_questions:
                case.handoff.open_questions.append(q)
        st.side_requests = []
        self.cases.put(case, stored.session_id)
        t.parts.append(render("side_request_noted", st.language, st.country, case_id=case.case_id))

    # ------------------------------------------------------------ lanes

    def _lane_a(self, st: ConversationState, t: _Turn, d: lane_rules.LaneDecision) -> None:
        lang, country = st.language, st.country
        st.lane, st.rule_id = "A", d.rule_id
        if st.customer_accepts_explanation is True:
            case = self._build_case(st, t, d, CaseStatus.resolved_in_contact)
            self._save_case(st, t, case)
            st.phase = "closed"
            t.parts.append(render("lane_a_closed", lang, country, case_id=case.case_id))
            return
        v = st.verification
        txn = st.txns.get(st.chosen_id or "", {})
        if d.rule_id == "customer_recognizes":
            t.parts.append(render("lane_a_recognized", lang, country))
        elif d.rule_id == "already_reversed":
            t.parts.append(render("lane_a_already_reversed", lang, country,
                                  reversal_date=format_date(v["reversal_date"], lang),
                                  amount=format_money(v["reversal_amount"], v["reversal_currency"], st.locale)))
        elif d.rule_id == "duplicate_already_reversed":
            t.parts.append(render("lane_a_duplicate_reversed", lang, country,
                                  reversal_date=format_date(v["reversal_date"], lang)))
        elif d.rule_id == "fee_matches_schedule":
            t.parts.append(render("lane_a_fee_matches", lang, country,
                                  amount=format_money(txn.get("amount", "0"), txn.get("currency", ""), st.locale),
                                  fee_name=v.get("fee_name", ""),
                                  fee_amount=format_money(v.get("fee_amount", "0"), v.get("fee_currency", ""), st.locale)))
        self._ask_resolved(st, t)
        st.phase = "resolved"

    def _open_case_b(self, st: ConversationState, t: _Turn, d: lane_rules.LaneDecision) -> None:
        st.lane, st.rule_id = "B", d.rule_id
        case = self._build_case(st, t, d, CaseStatus.open)
        self._save_case(st, t, case)
        st.phase = "closed"
        # App, branch or service complaints have no charge: no sentence about "the charge".
        key = "lane_b_case_open_other" if st.complaint_type in OTHER_TYPES else "lane_b_case_open"
        t.parts.append(render(key, st.language, st.country, case_id=case.case_id,
                              first_response_by=format_datetime(case.clock.first_response_by, st.language, st.country),
                              expected_date=format_date(case.promise.expected_date, st.language)))

    def _queue_for(self, st: ConversationState, rule_id: str) -> str:
        score = st.verification.get("fraud_score") if st.charge_confirmed else None
        if rule_id == "high_fraud_score" or (score is not None and score > FRAUD_SCORE_QUEUE):
            return "fraud"
        if rule_id == "regulator_or_legal":
            return "regulator"
        if rule_id == "lost_card":
            return "cards"
        if rule_id == "no_progress" and st.complaint_type is None:
            # Nothing about a complaint was said (greetings, out-of-scope requests): not the
            # complaints team; the request, if any, is in open_questions.
            return "general"
        return "complaints_pt" if st.language == "pt" else "complaints_es"

    def _handoff(self, st: ConversationState, t: _Turn, d: lane_rules.LaneDecision) -> None:
        lang, country = st.language, st.country
        st.lane, st.rule_id = "C", d.rule_id
        queue = self._queue_for(st, d.rule_id)
        priority = "high" if d.rule_id in HIGH_PRIORITY_RULES or queue == "fraud" else "normal"
        if st.prior_complaints is None and not st.tool_failure:
            try:
                contacts = self._tool(st, t, "get_prior_contacts", lambda ctx: self.gw.get_prior_contacts(ctx),
                                      out=lambda r: f"contacts:{len(r)}")
                st.prior_complaints = sum(1 for c in contacts if c.contact_type == "complaint")
            except _ToolFailed:
                # Optional lookup: the case is marked incomplete, the reply stays the normal
                # handoff (the customer asked for a person or reported a card, not movements).
                st.evidence_incomplete = True
                t.degrade("tool_unavailable")
        if (st.prior_complaints or 0) >= 2:
            priority = "high"
        existing = self.cases.get(st.case_id) if st.case_id else None
        if existing is not None:
            # An escalation of a case already with a person (senior, fraud, regulator, cards)
            # keeps that queue and never lowers the priority (H2).
            old_queue, old_priority = existing.case.handoff.queue, existing.case.handoff.priority
            if old_queue and _QUEUE_RANK.get(old_queue, 0) > _QUEUE_RANK.get(queue, 0):
                queue = old_queue
            if old_priority == "high":
                priority = "high"
        case = self._build_case(st, t, d, CaseStatus.handed_off, queue=queue, priority=priority,
                                existing=existing.case if existing else None)
        self._save_case(st, t, case)
        st.phase = "closed"
        if st.tool_failure:
            t.parts.append(render("tool_unavailable_case", lang, country, case_id=case.case_id,
                                  first_response_by=format_datetime(case.clock.first_response_by, lang, country)))
            return
        if st.lost_card:
            t.parts.append(render("lost_card_intro", lang, country))
        if d.rule_id == "asks_for_human":
            t.parts.append(render("human_request_ack", lang, country))
        if st.flags["asks_compensation"]:
            t.parts.append(render("compensation_ack", lang, country))
        # "I left the summary of what you told me" only when the customer told something.
        handoff_key = "lane_c_handoff" if st.statements else "lane_c_handoff_bare"
        t.parts.append(render(handoff_key, lang, country, case_id=case.case_id,
                              queue=render(f"queue.{queue}", lang),
                              first_response_by=format_datetime(case.clock.first_response_by, lang, country)))
        if priority == "high":
            t.parts.append(render("lane_c_priority_note", lang, country))
        if queue in ("fraud", "cards") and st.block is None:
            self._offer_block(st, t)

    # ------------------------------------------------------------ card block (explicit yes only)

    def _offer_block(self, st: ConversationState, t: _Turn) -> bool:
        """Offer the block of the card with buttons. True if offered. When the cards cannot be
        read, the "I can block it first" intro is withdrawn and the customer is told."""
        intro = render("lost_card_intro", st.language, st.country)
        try:
            cards = self._tool(st, t, "get_cards", lambda ctx: self.gw.get_cards(ctx),
                               out=lambda r: f"cards:{len(r)}")
        except _ToolFailed:
            t.degrade("tool_unavailable")
            st.evidence_incomplete = True
            stored = self.cases.get(st.case_id) if st.case_id else None
            if stored is not None:
                case = stored.case
                if "evidence_incomplete" not in case.urgency_flags:
                    case.urgency_flags.append("evidence_incomplete")
                note = "No se pudo leer el estado de las tarjetas: bloquear la tarjeta con el cliente"
                if note not in case.handoff.open_questions:
                    case.handoff.open_questions.append(note)
                self.cases.put(case, stored.session_id)
            t.parts = [p for p in t.parts if p != intro]
            t.parts.append(render("block_unavailable", st.language, st.country))
            return False
        last4 = (st.txns.get(st.chosen_id or "") or {}).get("card_last4") or st.claimed.get("card_last4")
        active = [c for c in cards if c.status == "active"]
        card = next((c for c in active if c.card_last4 == last4), None) or (active[0] if len(active) == 1 else None)
        if card is None:
            t.parts = [p for p in t.parts if p != intro]
            return False
        st.block = {"card_id": card.card_id, "last4": card.card_last4, "status": "offered"}
        t.parts.append(render("block_offer", st.language, st.country, last4=card.card_last4))
        self._block_buttons(st, t, card.card_id, card.card_last4)
        return True

    def _block_buttons(self, st: ConversationState, t: _Turn, card_id: str | None, last4: str) -> None:
        t.buttons = [
            ButtonSpec(render("btn.block_yes", st.language, st.country, last4=last4), "confirm",
                       {"a": "block", "yes": True, "card": card_id}),
            ButtonSpec(render("btn.block_no", st.language, st.country), "deny",
                       {"a": "block", "yes": False, "card": card_id}),
        ]
        t.input_mode = "buttons_only"
        st.phase = "block"

    def _on_block(self, st: ConversationState, t: _Turn, yes: bool, card_id: str | None) -> None:
        lang, country = st.language, st.country
        last4 = (st.block or {}).get("last4", "")
        st.phase = "closed"
        self._guard(t, "guard.block_confirmed" if yes and card_id else "guard.block_declined")
        if not yes or not card_id:
            st.block = {**(st.block or {}), "status": "declined"}
            t.parts.append(render("block_declined", lang, country))
            return
        try:
            status = self._tool(st, t, "block_card",
                                lambda ctx: self.gw.block_card(ctx, card_id, confirmed_by_customer=True),
                                inp=f"card:{card_id}", out=lambda r: f"card_status:{r.status}")
        except _ToolFailed:
            t.degrade("tool_unavailable")
            t.parts.append(render("block_failed", lang, country, last4=last4))
            return
        st.block = {**(st.block or {}), "status": status.status}
        if status.status == "blocked":
            t.parts.append(render("block_done", lang, country, last4=status.card_last4))
        else:
            t.parts.append(render("block_failed", lang, country, last4=last4))
        stored = self.cases.get(st.case_id) if st.case_id else None
        if stored is not None:
            case = stored.case
            case.actions.append(Action(tool="block_card", args={"card_id": card_id, "confirmed_by_customer": True},
                                       result={"status": status.status}, verified_at=self.clock()))
            case.handoff.facts_verified.append(
                f"Tarjeta •••• {status.card_last4}: bloqueo con sí explícito del cliente; estado leído: {status.status}")
            self.cases.put(case, stored.session_id)

    # ------------------------------------------------------------ case record

    def _build_case(self, st: ConversationState, t: _Turn, d: lane_rules.LaneDecision, status: CaseStatus,
                    queue: str | None = None, priority: str | None = None,
                    existing: CaseRecord | None = None) -> CaseRecord:
        now = self.clock().astimezone(timezone.utc).replace(microsecond=0)
        lane = d.lane
        plan = compute_promise(now, st.complaint_type, queue=queue if queue == "fraud" else None, country=st.country)
        # B promises a date. A and C do not (C: the person decides); an escalated case keeps
        # the date it was already given.
        promise = plan.promise if lane == Lane.B else Promise(sla_days=plan.promise.sla_days,
                                                              p90_days=plan.promise.p90_days)
        if existing is not None and existing.promise.expected_date:
            promise = existing.promise
        txn = st.txns.get(st.chosen_id) if (st.chosen_id and st.charge_confirmed) else None
        v = st.verification if st.charge_confirmed else {}
        handoff = Handoff()
        urgency: list[str] = []
        if lane == Lane.C:
            handoff = Handoff(queue=queue, priority=priority, open_questions=self._open_questions(st, d),
                              facts_verified=self._facts(st, txn, v))
            if st.tool_failure or st.evidence_incomplete:
                urgency.append("evidence_incomplete")
            if existing is not None:  # notes added to the case before this escalation are kept
                handoff.open_questions += [q for q in existing.handoff.open_questions
                                           if q not in handoff.open_questions]
        for f in C_FLAGS:
            if st.flags.get(f):
                urgency.append(f)
        if st.lost_card:
            urgency.append("lost_card")
        intent = st.intent or {}
        match = None
        if st.match:
            match = Match(candidates=[MatchCandidate(**c) for c in st.match.get("candidates", [])],
                          chosen_id=st.chosen_id if st.charge_confirmed else None, p_top1=st.match.get("p_top1"),
                          band=st.match.get("band"), model_version=st.match.get("model_version"))
        actions = [Action(tool=a["tool"], args=a.get("args", {}), result=a.get("result"),
                          verified_at=a.get("verified_at")) for a in st.actions]
        if existing is not None:
            actions = existing.actions + [a for a in actions if a.model_dump() not in
                                          [x.model_dump() for x in existing.actions]]
        return CaseRecord(
            case_id=existing.case_id if existing else new_case_id(),
            created_at=existing.created_at if existing else now,
            channel=t.session.channel if t.session.channel in ("app", "whatsapp", "web") else "web",
            language=st.language if st.language in ("es", "pt") else "es",
            customer_id=t.session.customer_id,
            segment=None,
            country=st.country,
            subcategory=st.complaint_type,
            intent_confidence=intent.get("p"),
            urgency_flags=urgency,
            evidence=CaseEvidence(transaction={k: txn[k] for k in txn if k != "synthetic"} if txn else None,
                                  product={"product_id": txn["product_id"]} if txn else None),
            customer_statement=(" / ".join(st.statements)[:1000] or None),
            lane=lane,
            lane_reason=d.rule_id,
            rules_version=d.rules_version,
            promise=promise,
            actions=actions,
            handoff=handoff,
            clock=self._keep_clock(existing, plan.clock) if lane != Lane.A else Clock(),  # A: nobody has to answer
            status=status,
            trace_id=t.trace_id,
            match=match,
            intent=Intent(**{"class": intent["intent_class"], "p": intent["p"],
                             "gate_action": intent["gate_action"],
                             "model_version": intent.get("model_version")}) if intent else None,
            risk_evidence=RiskEvidence(fraud_score=v.get("fraud_score"),
                                       rule_fired=d.rule_id if d.rule_id in ("high_fraud_score", "high_amount") else None)
            if v else None,
            # An escalated case is the same record: it keeps its version (conditional write in
            # the store), the investigation, an analyst decision and what was sent already.
            **({"version": existing.version, "investigation_id": existing.investigation_id,
                "analyst_decision": existing.analyst_decision, "labels_emitted": list(existing.labels_emitted),
                "resolution": existing.resolution} if existing is not None else {}),
        )

    @staticmethod
    def _keep_clock(existing: CaseRecord | None, new: Clock) -> Clock:
        """The SLA runs from the first contact: an escalation never moves a deadline later, it
        can only bring one closer (e.g. the 2 h first response of the fraud queue; H2)."""
        if existing is None or existing.clock.breach_at is None:
            return new
        old = existing.clock

        def earliest(a, b):
            return min(x for x in (a, b) if x is not None) if (a or b) else None

        return Clock(assigned_by=earliest(old.assigned_by, new.assigned_by),
                     first_response_by=earliest(old.first_response_by, new.first_response_by),
                     sla_alert_at=earliest(old.sla_alert_at, new.sla_alert_at),
                     breach_at=earliest(old.breach_at, new.breach_at))

    def _save_case(self, st: ConversationState, t: _Turn, case: CaseRecord) -> None:
        self._step(t, "rule", "case_store.put", None, lambda: self.cases.put(case, t.session.session_id),
                   inp=f"case:{case.case_id}", out=lambda r: f"case:{case.case_id}:{case.status.value}")
        st.case_id = case.case_id

    @staticmethod
    def _facts(st: ConversationState, txn: dict | None, v: dict) -> list[str]:
        facts = ["Identidad: sesión demo válida (lista permitida y token vigente)"]
        if txn:
            facts.append(f"Cargo confirmado por el cliente con botón: {txn['txn_id']} · {txn['local_date']} · "
                         f"{txn['merchant_name']} · {txn['amount']} {txn['currency']} · •••• {txn.get('card_last4')}")
            if v.get("amount_usd") is not None:
                facts.append(f"Monto en USD (tipo de cambio sintético): {v['amount_usd']}")
            if v.get("fraud_score") is not None:
                facts.append(f"fraud_score del registro: {v['fraud_score']:g}")
            if v.get("duplicate_found"):
                facts.append("Duplicado en 24 h: " + ", ".join(v.get("duplicate_ids", []))
                             + ("; con reverso" if v.get("duplicate_reversed") else "; sin reverso"))
            if v.get("already_reversed"):
                facts.append(f"Reverso aplicado el {v.get('reversal_date')}")
            if "fee_matches_schedule" in v:
                facts.append(f"Tarifa vigente {v.get('fee_amount')} {v.get('fee_currency')}; "
                             f"coincide con el cargo: {'sí' if v['fee_matches_schedule'] else 'no'}")
        if st.prior_complaints is not None:
            facts.append(f"Reclamos previos en el historial: {st.prior_complaints}")
        if st.customer_recognizes is not None:
            facts.append("El cliente " + ("reconoce" if st.customer_recognizes else "no reconoce") + " el cargo (botón)")
        return facts

    @staticmethod
    def _open_questions(st: ConversationState, d: lane_rules.LaneDecision) -> list[str]:
        q = [f"Regla {d.rule_id}: {d.reason}"]
        if st.tool_failure:
            q.append("Caso incompleto: una herramienta de datos no respondió; repetir la consulta de movimientos")
        elif st.evidence_incomplete:
            q.append("Caso incompleto: no se pudo leer el historial de contactos o las tarjetas; revisarlo")
        if not st.charge_confirmed:
            said = ", ".join(f"{k}={st.claimed[k]}" for k in CLAIMED_SLOTS if st.claimed.get(k))
            q.append("Identificar el cargo con el cliente" + (f" (dijo, sin verificar: {said})" if said else ""))
        elif st.complaint_type == "unrecognized_charge" and st.customer_recognizes is None:
            # The customer already said it (a charge "I do not recognize"), just not with the
            # button: confirm it, do not ask it as if it were new (CX-16).
            q.append("Confirmar que no reconoce el cargo (lo dijo en el chat; no lo confirmó con el botón)")
        if st.flags.get("asks_compensation"):
            q.append("Pide compensación: no se le prometió nada")
        if st.flags.get("mentions_regulator"):
            q.append("Mencionó al regulador o una acción legal")
        if st.flags.get("complaint_about_person"):
            q.append("La queja es sobre una persona del banco")
        if st.lost_card:
            q.append("Tarjeta perdida o robada: confirmar el bloqueo y la reposición")
        for s in st.side_requests:
            q.append(f"Pidió además, fuera del reclamo (sin verificar): «{s}»")
        return q

    # ------------------------------------------------------------ extraction (G1 or regex)

    def _extract(self, st: ConversationState, t: _Turn, clean: str) -> extract_mod.Extraction:
        ref = date.fromisoformat(st.reference_date) if st.reference_date else None
        regex = self._step(t, "rule", "extract", extract_mod.EXTRACTOR_VERSION,
                           lambda: extract_mod.extract(clean, st.language, ref),
                           out=lambda r: "slots:" + ",".join(k for k, v in r.slots.model_dump().items() if v))
        if t.session.model_slow:
            # Judge-mode "model_slow" (RF-22): the turn waits the real timeout, as if G1 had not
            # answered, then the regex extractor answers and the reply says model_timeout.
            started = time.perf_counter()
            self.sleep(self.llm_timeout)
            self._record(t, "model", g1_mod.PROMPT_ID, int((time.perf_counter() - started) * 1000),
                         g1_mod.current_prompt_version(), "model_timeout", inp=self._msg_ref(t),
                         out="demo_switch:model_slow")
            t.degrade("model_timeout")
            return regex
        if self.llm is None:
            return regex
        o = g1_mod.run(self.llm, clean, st.language, st.reference_date, self.llm_timeout, regex, _LLM_POOL)
        self._record(t, "model", g1_mod.PROMPT_ID, o.latency_ms, o.prompt_version, o.error_code,
                     inp=self._msg_ref(t), out=o.output_ref, tokens_in=o.tokens_in, tokens_out=o.tokens_out,
                     cost=o.cost_usd)
        if o.degraded:
            t.degrade("model_timeout")
        if o.model_id:  # the model answered (even an invalid output, which is billed)
            t.model_id = o.model_id
            t.cost_usd += o.cost_usd
            if o.tokens_in is not None:
                t.tokens_in = (t.tokens_in or 0) + int(o.tokens_in)
            if o.tokens_out is not None:
                t.tokens_out = (t.tokens_out or 0) + int(o.tokens_out)
        return o.extraction

    def _merge(self, st: ConversationState, ext: extract_mod.Extraction, intent: dict) -> set[str]:
        """Fold one message into the state. Returns the C flags that turned on now."""
        new_flags = set()
        for k in C_FLAGS:
            if getattr(ext.flags, k) and not st.flags[k]:
                st.flags[k] = True
                new_flags.add(k)
        if ext.flags.lost_card:
            st.lost_card = True
        for k in CLAIMED_SLOTS:
            value = getattr(ext.slots, k)
            if value:
                st.claimed[k] = value
        ctype = ext.complaint_type
        if ctype is None:
            ctype = {"dispute_charge": "unrecognized_charge", "dispute_fee": "wrong_fee"}.get(intent["intent_class"])
        if st.complaint_type is None and ctype is not None:
            st.complaint_type = ctype
        return new_flags

    def _detect_kind(self, st: ConversationState, clean: str) -> None:
        f = fold(clean)
        if _DUP_WORDS.search(f):
            st.kind = "duplicate"
        elif _FEE_WORDS.search(f) and st.kind is None:
            st.kind = "fee"

    # ------------------------------------------------------------ progress

    @staticmethod
    def _signature(st: ConversationState) -> tuple:
        return (st.complaint_type, tuple(st.claimed.get(k) for k in CLAIMED_SLOTS), st.chosen_id,
                st.charge_confirmed, st.customer_recognizes, st.customer_accepts_explanation, st.case_id,
                tuple(st.offered), st.language, st.lost_card, tuple(sorted(k for k, v in st.flags.items() if v)),
                (st.block or {}).get("status"))

    def _count_progress(self, st: ConversationState, before: tuple, force_no_progress: bool = False) -> None:
        if st.case_id is not None:
            return
        if force_no_progress or self._signature(st) == before:
            st.turns_without_progress += 1
        else:
            st.turns_without_progress = 0

    def _progress(self, st: ConversationState) -> dict[str, Any]:
        stored = self.cases.get(st.case_id) if st.case_id else None
        if stored is not None:
            step = "done" if stored.case.status == CaseStatus.resolved_in_contact else "case_open"
        elif st.phase in ("confirm", "recognize", "resolved"):
            step = "verify"
        elif st.complaint_type is None:
            step = "understand"
        else:
            step = "find"
        missing: list[str] = []
        if stored is None:
            if st.complaint_type is None:
                missing = ["complaint_type"]
            elif st.complaint_type in MONEY_TYPES:
                if st.charge_confirmed is not True:
                    if st.phase in ("confirm", "choose"):
                        missing = ["charge_confirmed"]
                    else:
                        missing = [s for s in SEARCH_SLOTS if not st.claimed.get(s)]
                elif st.complaint_type == "unrecognized_charge" and st.customer_recognizes is None:
                    missing = ["customer_recognizes"]
        claimed = {k: (str(st.claimed[k]) if st.claimed.get(k) is not None else None) for k in CLAIMED_SLOTS}
        return {"step": step, "complaint_type": st.complaint_type, "claimed": claimed, "missing": missing}

    # ------------------------------------------------------------ tools, steps and trace

    def _ctx(self, t: _Turn):
        return self.gw.GatewayContext(customer_id=t.session.customer_id, session_id=t.session.session_id,
                                      trace_id=t.trace_id)

    def _tool(self, st: ConversationState, t: _Turn, name: str, call: Callable, inp: str | None = None,
              out: Callable[[Any], str] | None = None):
        """A gateway call with bounded retries. Raises _ToolFailed after the last one."""
        ctx = self._ctx(t)
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                result = call(ctx)
            except self.gw.ToolUnavailable:
                self._record(t, "tool", name, self._ms(started), None, "tool_unavailable",
                             inp=inp or f"tool:{name}", out=f"attempt:{attempt + 1}")
                if attempt < self.retries:
                    self.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
                    continue
                raise _ToolFailed(name)
            except (self.gw.NotOwned, self.gw.NotFound) as e:
                # Never a customer-visible distinction; the orchestrator treats it as a failure.
                self._record(t, "tool", name, self._ms(started), None, type(e).__name__.lower(),
                             inp=inp or f"tool:{name}", out=None)
                raise _ToolFailed(name)
            self._record(t, "tool", name, self._ms(started), None, None, inp=inp or f"tool:{name}",
                         out=out(result) if out else None)
            return result
        raise _ToolFailed(name)  # pragma: no cover

    def _step(self, t: _Turn, actor: str, name: str, version: str | None, fn: Callable, inp: str | None = None,
              out: Callable[[Any], str] | None = None, error: str | None = None):
        started = time.perf_counter()
        result = fn()
        self._record(t, actor, name, self._ms(started), version, error, inp=inp or self._msg_ref(t),
                     out=out(result) if out else None)
        return result

    @staticmethod
    def _ms(started: float) -> int:
        return int(round((time.perf_counter() - started) * 1000))

    @staticmethod
    def _msg_ref(t: _Turn) -> str:
        # The message itself is never traced: only a pointer to it (turn of the session).
        return f"msg:{t.session.session_id}:{t.turn}"

    def _record(self, t: _Turn, actor: str, name: str, latency_ms: int, version: str | None,
                error_code: str | None, inp: str | None = None, out: str | None = None,
                tokens_in: int | None = None, tokens_out: int | None = None, cost: float | None = None) -> None:
        step = {"actor": actor, "name": name, "latency_ms": latency_ms, "version": version, "error_code": error_code}
        t.steps.append(step)
        event = {
            "trace_id": t.trace_id, "session_id": t.session.session_id, "case_id": None, "turn": t.turn,
            "step": len(t.steps), "actor": actor, "name": name, "input_ref": inp, "output_ref": out,
            "latency_ms": latency_ms, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost_usd": cost or 0.0, "version": version, "error_code": error_code,
            "ts": datetime.now(timezone.utc).isoformat(), "source": t.session.source,
        }
        t.steps[-1]["_event"] = event

    def _result(self, st: ConversationState, t: _Turn) -> TurnResult:
        self._guard(t, "guard.reply")  # any turn still without a rule gets the generic fixed one
        latency = self._ms(t.started)
        # Emit trace events now that the case id (if any) is known.
        for s in t.steps:
            event = s.pop("_event")
            event["case_id"] = st.case_id
            if s["name"] == "lane_rules":
                event["version"] = s["version"]
            try:
                self.traces.emit(event)
            except Exception:
                pass
        reply = " ".join(p for p in t.parts if p).strip() or render("not_understood", st.language, st.country)
        return TurnResult(
            reply_text=reply,
            reply_language=st.language if st.language in ("es", "pt") else "es",
            buttons=t.buttons,
            input_mode=t.input_mode if t.buttons else "free_text",
            progress=self._progress(st),
            case_card=self.case_view(st.case_id),
            lane=st.lane,
            degraded=list(t.degraded),
            trace_id=t.trace_id,
            trace_summary={"steps": t.steps, "latency_ms": latency, "cost_usd": round(t.cost_usd, 8),
                           "model_id": t.model_id, "tokens_in": t.tokens_in, "tokens_out": t.tokens_out,
                           "rule_id": t.rule_id, "rules_version": t.rules_version},
            state=st.model_dump(mode="json"),
            customer_text=t.customer_text,
        )
