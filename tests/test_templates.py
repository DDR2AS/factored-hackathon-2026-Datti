from datetime import date, datetime, timezone

import pytest

from conversation import templates as T

REQUIRED = {
    "welcome", "clarify_intent", "not_understood", "ask_language", "ask_amount", "ask_date",
    "ask_merchant", "ask_more_detail", "charge_line", "confirm_charge", "choose_charge",
    "none_of_these", "no_charge_found", "ask_recognize", "lane_a_recognized",
    "lane_a_already_reversed", "lane_a_duplicate_reversed", "lane_a_fee_matches",
    "lane_b_case_open", "lane_c_handoff", "lane_c_handoff_bare", "lane_c_priority_note", "human_request_ack",
    "compensation_ack", "lost_card_intro", "block_offer", "block_done", "block_declined",
    "block_failed", "case_status", "case_status_not_found", "out_of_scope", "manipulation",
    "session_expired", "session_limit", "tool_unavailable", "tool_unavailable_case", "model_slow",
    "btn.start_unrecognized", "btn.start_fee", "btn.start_case_status", "btn.start_lost_card",
    "btn.human", "btn.confirm_charge", "btn.none_of_these", "btn.recognize_yes",
    "btn.recognize_no", "btn.block_yes", "btn.block_no", "btn.restart", "btn.retry",
    "btn.lang_es", "btn.lang_pt", "queue.fraud", "queue.disputes", "queue.fees",
    "queue.cards", "queue.priority", "queue.general", "tz.MX", "tz.CO", "tz.AR", "tz.UTC",
}


def test_both_languages_have_the_same_keys_and_all_required():
    assert T.template_keys("es") == T.template_keys("pt")
    assert REQUIRED <= T.template_keys("es")


@pytest.mark.parametrize("key", sorted(REQUIRED))
def test_same_variables_in_every_language_and_register(key):
    variants = {frozenset(T.variables_of(key, "pt"))}
    for country in ("MX", "CO", "AR"):
        variants.add(frozenset(T.variables_of(key, "es", country)))
    assert len(variants) == 1, key


def test_spanish_entries_that_vary_have_all_three_registers():
    for key, entry in T._load("es")["texts"].items():
        if isinstance(entry, dict):
            assert set(entry) == {"tu", "usted", "vos"}, key


def test_registers_by_country():
    assert T.register_for("es", "MX") == "tu"
    assert T.register_for("es", "CO") == "usted"
    assert T.register_for("es", "AR") == "vos"
    assert T.register_for("es", None) == "tu"
    assert T.register_for("pt", "AR") == "voce"
    assert T.render("ask_recognize", "es", "MX") == "¿Reconoces este cargo?"
    assert T.render("ask_recognize", "es", "CO") == "¿Reconoce este cargo?"
    assert T.render("ask_recognize", "es", "AR") == "¿Reconocés este cargo?"
    assert T.render("ask_recognize", "pt", "AR") == "Você reconhece esta cobrança?"


@pytest.mark.parametrize("language", ["es", "pt"])
def test_no_template_promises_refund_or_compensation(language):
    for key, text in T.all_texts(language):
        assert T.forbidden_hits(text) == [], (key, text)


def test_forbidden_hits_catches_promises():
    assert T.forbidden_hits("Te reembolsaremos el cargo")
    assert T.forbidden_hits("Vamos a devolverte el dinero")
    assert T.forbidden_hits("Você receberá uma compensação")
    assert T.forbidden_hits("o valor será estornado")
    assert not T.forbidden_hits("Este cargo ya tiene una reversión aplicada")


def test_render_fills_variables_and_rejects_missing():
    text = T.render("lane_b_case_open", "es", "MX", case_id="EV-ABCD2345",
                    first_response_by="30 de septiembre de 2026, 15:40 (hora del centro de México)",
                    expected_date="26 de octubre de 2026", unused="x")
    assert "EV-ABCD2345" in text and "26 de octubre de 2026" in text and "{" not in text
    with pytest.raises(KeyError):
        T.render("lane_b_case_open", "es", "MX", case_id="EV-ABCD2345")
    with pytest.raises(KeyError):
        T.render("no_such_key", "es")


def test_unknown_language_falls_back_to_spanish():
    assert T.render("btn.human", "fr") == T.render("btn.human", "es") == "Hablar con una persona"
    assert T.render("btn.human", "pt") == "Falar com uma pessoa"


def test_welcome_and_buttons():
    assert T.render("welcome", "pt", name="João").startswith("Olá, João.")
    assert "sintéticos" in T.render("welcome", "es", "CO", name="Sofía")
    assert T.render("btn.block_yes", "es", last4="7730") == "Sí, bloquear tarjeta •••• 7730"


def test_format_money():
    assert T.format_money("449.90", "MXN", "es-MX") == "449.90 MXN"
    assert T.format_money("1299.00", "MXN", "es-MX") == "1,299.00 MXN"
    assert T.format_money("189900", "COP", "es-CO") == "189.900 COP"
    assert T.format_money("12500.00", "ARS", "pt-BR") == "12.500,00 ARS"
    assert T.format_money("-16900.00", "COP", "es-CO") == "-16.900,00 COP"
    with pytest.raises(ValueError):
        T.format_money("abc", "MXN")


def test_format_dates():
    assert T.format_date(date(2026, 6, 12), "es") == "12 de junio de 2026"
    assert T.format_date("2026-06-01", "pt") == "1º de junho de 2026"  # pt-BR ordinal for the 1st
    assert T.format_date("2026-06-02", "pt") == "2 de junho de 2026"
    assert T.format_date("2026-06-01", "es") == "1 de junio de 2026"
    dt = datetime(2026, 9, 30, 3, 30, tzinfo=timezone.utc)
    assert T.format_datetime(dt, "es", "CO") == "29 de septiembre de 2026, 22:30 (hora de Colombia)"
    assert T.format_datetime(dt, "pt", "AR") == "30 de setembro de 2026, 00:30 (horário da Argentina)"
    assert T.format_datetime(dt.replace(tzinfo=None), "es", None).endswith("03:30 (UTC)")


ORCHESTRATOR_KEYS = {
    "confirm_duplicate", "ask_resolved", "lane_a_closed", "case_status_short", "btn.resolved_yes",
    "btn.resolved_no", "queue.regulator", "queue.complaints_es", "queue.complaints_pt",
    "status.open", "status.investigating", "status.awaiting_analyst", "status.notified",
    "status.closed", "status.reopened", "status.resolved_in_contact", "status.handed_off",
}


@pytest.mark.parametrize("key", sorted(ORCHESTRATOR_KEYS))
def test_orchestrator_keys_exist_with_the_same_variables(key):
    assert key in T.template_keys("es") and key in T.template_keys("pt")
    variants = {frozenset(T.variables_of(key, "pt"))}
    for country in ("MX", "CO", "AR"):
        variants.add(frozenset(T.variables_of(key, "es", country)))
    assert len(variants) == 1, key


def test_spanish_queue_labels_read_well_after_al():
    # lane_c_handoff says "pasó al {queue}", so the labels carry no article.
    text = T.render("lane_c_handoff", "es", "MX", case_id="EV-X", queue=T.render("queue.fraud", "es"),
                    first_response_by="mañana")
    assert "pasó al equipo de Fraudes" in text and " a el " not in text
