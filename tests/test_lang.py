import pytest

from conversation.lang import detect_language, marker_counts


@pytest.mark.parametrize("text, expected", [
    ("me cobraron como 450 en el super el 12", "es"),
    ("No reconozco una compra", "es"),
    ("¿Cómo va mi caso?", "es"),
    ("Olá, cobraram uma tarifa de manutenção de 12.500 pesos no dia 1 e acho que está errada", "pt"),
    ("Não reconheço essa cobrança no meu cartão", "pt"),
    ("Sim", "pt"),
    ("quero falar com uma pessoa", "pt"),
    ("hola, obrigado", "mixed"),
    ("no reconozco esta cobrança, obrigado, meu cartão", "pt"),
])
def test_detect(text, expected):
    assert detect_language(text) == expected


@pytest.mark.parametrize("text", ["", "12.500", "OK", "EV-ABCD2345", "👍"])
def test_no_signal_returns_default(text):
    assert detect_language(text) is None
    assert detect_language(text, "pt") == "pt"


def test_mixed_needs_both_close():
    es, pt = marker_counts("hola gracias, obrigado")
    assert es == 2 and pt == 1
    assert detect_language("hola gracias, obrigado") == "es"  # exactly twice -> dominant
    assert detect_language("hola gracias usted, obrigado você") == "mixed"  # 3 vs 2
    assert detect_language("hola gracias usted tarjeta, obrigado") == "es"


def test_shared_words_are_not_markers():
    assert marker_counts("de que dia tarifa pesos") == (0, 0)
