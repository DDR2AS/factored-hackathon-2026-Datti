from datetime import date

import pytest

from conversation.extract import EXTRACTOR_VERSION, Extraction, extract, parse_amount

REF = date(2026, 6, 15)


def slots(text, lang="es", ref=REF):
    return extract(text, lang, ref).slots


# ---------------------------------------------------------------- demo scripts

def test_lucia():
    e = extract("me cobraron como 450 en el super el 12", "es", REF)
    assert e.slots.amount == "450" and e.slots.currency is None
    assert e.slots.date == "2026-06-12" and e.slots.date_is_relative is True
    assert e.slots.merchant_text == "super"
    assert e.complaint_type == "unrecognized_charge"
    assert not any(v for _, v in e.flags)


def test_joao_in_portuguese():
    e = extract("Olá, cobraram uma tarifa de manutenção de 12.500 pesos no dia 1 e acho que está errada", "pt", REF)
    assert e.slots.amount == "12500" and e.slots.currency is None  # "pesos" alone is ambiguous
    assert e.slots.date == "2026-06-01" and e.slots.date_is_relative is True
    assert e.slots.merchant_text is None  # "no dia 1" is a date, not a merchant
    assert e.complaint_type == "wrong_fee"


def test_andres_duplicate():
    e = extract("Me cobraron dos veces 189.900 en Tienda Tecno el 14", "es", REF)
    assert (e.slots.amount, e.slots.date, e.slots.merchant_text) == ("189900", "2026-06-14", "Tienda Tecno")
    assert e.complaint_type == "wrong_fee"


def test_martina_and_carlos():
    m = extract("No reconozco una compra de 245 mil en ELECTRO MUNDO del 15 de junio", "es", REF)
    assert (m.slots.amount, m.slots.date, m.slots.date_is_relative, m.slots.merchant_text) == (
        "245000", "2026-06-15", False, "ELECTRO MUNDO")
    assert m.complaint_type == "unrecognized_charge"
    c = extract("Me cobraron $1,299 de STREAMING PLUS el 11 y voy a ir a la CONDUSEF", "es", date(2026, 6, 12))
    assert (c.slots.amount, c.slots.date, c.slots.merchant_text) == ("1299", "2026-06-11", "STREAMING PLUS")
    assert c.flags.mentions_regulator


# ---------------------------------------------------------------- amounts

@pytest.mark.parametrize("text, amount, currency", [
    ("como 450", "450", None),
    ("12.500 pesos", "12500", None),
    ("R$ 35,90", "35.90", "BRL"),
    ("$1,299", "1299", None),
    ("un cargo de 449.90", "449.90", None),
    ("1.299,00 MXN", "1299", "MXN"),
    ("245 mil pesos argentinos", "245000", "ARS"),
    ("52 mil", "52000", None),
    ("USD 30", "30", "USD"),
    ("50 dólares", "50", "USD"),
    ("189.900 COP", "189900", "COP"),
    ("paguei 35 reais", "35", "BRL"),
])
def test_amounts(text, amount, currency):
    s = slots(text)
    assert (s.amount, s.currency) == (amount, currency)


def test_amount_is_not_a_day_time_year_or_card():
    s = slots("el 12 a las 10:02 en 2026 con la tarjeta terminada en 4821")
    assert s.amount is None and s.card_last4 == "4821" and s.date == "2026-06-12"
    assert slots("me cobraron 3 veces").amount is None


def test_parse_amount():
    assert str(parse_amount("12.500")) == "12500"
    assert str(parse_amount("35,90")) == "35.90"
    assert str(parse_amount("1,299.50")) == "1299.50"
    assert str(parse_amount("1.234.567")) == "1234567"


# ---------------------------------------------------------------- dates

@pytest.mark.parametrize("text, iso, relative", [
    ("el 12", "2026-06-12", True),
    ("el día 12", "2026-06-12", True),
    ("dia 1", "2026-06-01", True),
    ("el 20", "2026-05-20", True),  # after the reference day -> previous month
    ("el 31", "2026-05-31", True),
    ("12 de junio", "2026-06-12", False),
    ("1 de junho de 2026", "2026-06-01", False),
    ("20 de diciembre", "2025-12-20", False),  # future without year -> last year
    ("12/06", "2026-06-12", False),
    ("2026-06-12", "2026-06-12", False),
    ("ayer", "2026-06-14", True),
    ("ontem", "2026-06-14", True),
    ("anteayer", "2026-06-13", True),
    ("hoy", "2026-06-15", True),
    ("hace 3 días", "2026-06-12", True),
    ("há 2 dias", "2026-06-13", True),
])
def test_dates(text, iso, relative):
    s = slots(text, "pt" if "junho" in text or "ontem" in text or "há" in text else "es")
    assert (s.date, s.date_is_relative) == (iso, relative)


def test_relative_date_without_reference_is_none():
    s = extract("me cobraron el 12", "es", None).slots
    assert s.date is None and s.date_is_relative is True


# ---------------------------------------------------------------- merchant and card

@pytest.mark.parametrize("text, lang, merchant", [
    ("fue en el super", "es", "super"),
    ("compré en OXXO ayer", "es", "OXXO"),
    ("paguei na Padaria Pão Quente ontem", "pt", "Padaria Pão Quente"),
    ("no reconozco el cargo", "es", None),  # Spanish "no" is not a preposition
    ("me cobraron en la app", "es", None),
    ("me cobraron en junio", "es", None),
    ("un cargo de STREAMING PLUS", "es", "STREAMING PLUS"),
])
def test_merchant(text, lang, merchant):
    assert slots(text, lang).merchant_text == merchant


@pytest.mark.parametrize("text", [
    "tarjeta terminada en 4821", "la que termina en 4821", "final 4821", "•••• 4821",
    "últimos 4 dígitos 4821", "cartão final 4821", "[tarjeta ••••4821]",
])
def test_card_last4(text):
    assert slots(text).card_last4 == "4821"


# ---------------------------------------------------------------- flags and type

@pytest.mark.parametrize("text, flag", [
    ("quiero hablar con una persona", "asks_for_human"),
    ("pásame con un asesor", "asks_for_human"),
    ("quero falar com um atendente", "asks_for_human"),
    ("voy a ir a la CONDUSEF", "mentions_regulator"),
    ("ya puse la queja en la Superintendencia Financiera", "mentions_regulator"),
    ("lo voy a denunciar al BCRA", "mentions_regulator"),
    ("vou reclamar no Procon", "mentions_regulator"),
    ("mi abogado ya lo sabe", "mentions_regulator"),
    # found in the M1 integration run: the judge-mode suggestion for Carlos says "regulador"
    ("si no lo arreglan voy al regulador", "mentions_regulator"),
    ("vou reclamar no órgão regulador", "mentions_regulator"),
    ("exijo una compensación", "asks_compensation"),
    ("quero indenização por danos morais", "asks_compensation"),
    ("el cajero fue grosero conmigo", "complaint_about_person"),
    ("o atendente me tratou mal", "complaint_about_person"),
    ("perdí mi tarjeta", "lost_card"),
    ("me robaron la billetera con la tarjeta", "lost_card"),
    ("roubaram meu cartão", "lost_card"),
])
def test_flags(text, flag):
    f = extract(text, "pt" if any(w in text for w in ("quero", "vou", "roubaram", "atendente")) else "es", REF).flags
    assert getattr(f, flag) is True
    assert sum(v for _, v in f) == 1, f


@pytest.mark.parametrize("text, ctype", [
    ("no reconozco un cargo de 300", "unrecognized_charge"),
    ("não reconheço essa compra", "unrecognized_charge"),
    ("me cobraron una comisión de más", "wrong_fee"),
    ("cobraram duas vezes", "wrong_fee"),
    ("essa comissão está errada", "wrong_fee"),  # PT spelling (M1 integration run)
    ("la app se cierra", "app"),
    ("en la sucursal me hicieron esperar", "branch"),
    ("pésima atención en el call center", "service"),
    ("me cobraron 300 en el super", "unrecognized_charge"),
    ("hola", None),
])
def test_complaint_type(text, ctype):
    assert extract(text, "es", REF).complaint_type == ctype


# ---------------------------------------------------------------- contract

def test_schema_for_the_llm_client():
    schema = Extraction.model_json_schema()
    assert {"slots", "flags", "complaint_type"} <= set(schema["properties"])
    e = Extraction.model_validate({"slots": {"amount": "450"}, "flags": {"asks_for_human": True}})
    assert e.slots.amount == "450" and e.flags.asks_for_human and e.extractor_version == EXTRACTOR_VERSION


@pytest.mark.parametrize("text", ["", "   ", "¿¿??", "0", "$", "el 99", "99/99/9999", "x" * 2000, None])
def test_never_raises(text):
    e = extract(text, "es", REF)
    assert isinstance(e, Extraction)


def test_day_followed_by_punctuation_is_still_a_date():
    # "el 12, mi ..." (comma or period after the day) must resolve; "el 12,50" must not be a day.
    from datetime import date as _d

    ref = _d(2026, 6, 15)
    assert extract("me cobraron como 450 en el super el 12, mi celular es [teléfono]", "es", ref).slots.date == "2026-06-12"
    assert extract("fue el 12. no lo reconozco", "es", ref).slots.date == "2026-06-12"
    assert extract("pagué el 12,50 de propina", "es", ref).slots.date is None
