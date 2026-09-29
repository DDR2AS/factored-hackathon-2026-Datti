import pytest

from conversation.redact import redact


@pytest.mark.parametrize("text, secret, label", [
    ("mi correo es ana.perez@mail.com", "ana.perez@mail.com", "[correo]"),
    ("mi DNI es 30.123.456 y no reconozco el cargo", "30.123.456", "[documento]"),
    ("cédula 1020304050", "1020304050", "[documento]"),
    ("CC 79.456.123", "79.456.123", "[documento]"),
    ("mi CURP es GOMC800101HDFRRR09", "GOMC800101HDFRRR09", "[documento]"),
    ("RFC GOMC800101AB1", "GOMC800101AB1", "[documento]"),
    ("CUIT 20-12345678-3", "20-12345678-3", "[documento]"),
    ("NIT 900.123.456-7", "900.123.456-7", "[documento]"),
    ("pasaporte G12345678", "G12345678", "[documento]"),
    ("llámame al +52 55 1234 5678", "1234 5678", "[teléfono]"),
    ("mi cel es 300 123 4567", "300 123 4567", "[teléfono]"),
    ("cel 3001234567", "3001234567", "[teléfono]"),
    ("whatsapp 11 1234-5678", "1234-5678", "[teléfono]"),
])
def test_masks_spanish(text, secret, label):
    out = redact(text)
    assert secret not in out and label in out


@pytest.mark.parametrize("text, secret, label", [
    ("meu CPF é 123.456.789-09", "123.456.789-09", "[documento]"),
    ("CPF 12345678909", "12345678909", "[documento]"),
    ("RG 12.345.678-9", "12.345.678-9", "[documento]"),
    ("meu telefone é (11) 91234-5678", "91234-5678", "[telefone]"),
    ("liga no +55 11 91234-5678", "91234-5678", "[telefone]"),
    ("meu e-mail joao@exemplo.com.br", "joao@exemplo.com.br", "[e-mail]"),
])
def test_masks_portuguese(text, secret, label):
    out = redact(text, "pt")
    assert secret not in out and label in out


def test_full_card_keeps_only_last4():
    assert redact("tarjeta 4111 1111 1111 1234") == "tarjeta [tarjeta ••••1234]"
    assert redact("4111-1111-1111-1234 fue clonada") == "[tarjeta ••••1234] fue clonada"
    assert redact("5500 0000 0000 00004") == "[tarjeta ••••0004]"  # 17 digits
    assert redact("4111111111111234") == "[tarjeta ••••1234]"
    assert redact("4111111111111234", "pt") == "[cartão ••••1234]"


@pytest.mark.parametrize("text", [
    "me cobraron como 450 en el super el 12",
    "12.500 pesos no dia 1",
    "R$ 35,90",
    "$1,299 de STREAMING PLUS",
    "449.90 MXN",
    "189.900 COP el 14 a las 10:02",
    "1.299,00",
    "245 mil",
    "el 12/06/2026",
    "2026-06-12",
    "12-06-2026",
    "tarjeta •••• 4821",
    "terminada en 4821",
    "caso EV-ABCD2345",
    "no reconozco el documento que me mandaron",
])
def test_keeps_amounts_dates_and_last4(text):
    assert redact(text) == text


def test_idempotent_and_empty():
    text = "DNI 30.123.456, tel +54 9 11 1234-5678, a@b.co, 4111 1111 1111 1111"
    once = redact(text)
    assert redact(once) == once
    assert redact("") == "" and redact(None) is None
