"""Redaction of free text before any model call and before storing customer_statement
(owner: arturo; RNF-06).

Masks, in this order: e-mail addresses; postal addresses (SEC-03: after "mi dirección/
domicilio es", "meu endereço é"; a street word with a number: calle, avenida/av., carrera/cra.,
calzada, boulevard, jirón, pasaje, diagonal, transversal, privada, rua, travessa, alameda,
rodovia, estrada, praça, with an optional "# 45-10" and unit "depto 5"/"ap 12"; a
neighbourhood word before a capitalised name: col./colonia, fracc., barrio, bairro, urb.; a
postal code after CP/C.P./código postal/CEP; neighbouring pieces collapse into one label; a
street word whose "number" is an amount such as "450 pesos" is left alone); bank account numbers (CLABE, CBU/CVU, CCI, IBAN:
by keyword with 10+ digits, or a bare run of 20-22 digits); full card numbers (13-19 digits,
spaces, dashes or dots allowed; with dots only if the Luhn check passes, so amounts are not
touched; the last 4 are kept because the product already shows them; 18 bare digits that
fail Luhn are a CLABE, not a card); identity documents (by keyword before the number: DNI,
CURP, RFC, INE, cédula, CC, CPF, RG, CUIT, CUIL, NIT, pasaporte, documento...; by keyword
after it: "45678912 de DNI"; and by shape: CPF 000.000.000-00, CUIT/CUIL 00-00000000-0,
CURP, RFC); card security codes ("cvv 123"); card expiry dates after "vence/venc/exp/
validade" ("11/29"); and phone numbers (8-13 digits in groups, optional +country and (area)
code; or a bare run of 10-12 digits; also right after "cel." / "tel." / "wa."; a phone may end
the sentence with a comma or a dot). Amounts ("12.500", "$1,299", "R$ 35,90", "449.90"),
dates ("12/06/2026", "2026-06-12"), times ("10:02") and last-4 mentions ("•••• 4821") are
left intact.

This is a pattern list, not a guarantee: tests cover the shapes above in ES and PT.
"""

from __future__ import annotations

import re

LABELS = {
    "es": {"email": "[correo]", "card": "[tarjeta ••••{last4}]", "doc": "[documento]", "phone": "[teléfono]",
           "account": "[cuenta]", "cvv": "[cvv]", "expiry": "[vencimiento]", "address": "[dirección]"},
    "pt": {"email": "[e-mail]", "card": "[cartão ••••{last4}]", "doc": "[documento]", "phone": "[telefone]",
           "account": "[conta]", "cvv": "[cvv]", "expiry": "[validade]", "address": "[endereço]"},
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)

# ---- postal addresses (SEC-03)
_ADDR_ABBREV = r"(?:av|avda|col|dpto|depto|apto|ap|int|cra|kr|jr|psje|blvd|calz|fracc|urb|nro|no|n|of)"
_ADDR_INTRO = re.compile(
    r"\b(?:mi\s+direcci[óo]n|mi\s+domicilio|direcci[óo]n\s*:|domicilio\s*:|meu\s+endere[çc]o|minha\s+resid[êe]ncia|"
    r"endere[çc]o\s*:)(?:\s*(?:es|é|:|=)(?![^\W\d_]))*\s*"
    r"((?:\b" + _ADDR_ABBREV + r"\.|[^;!?\n.])+)",
    re.IGNORECASE | re.UNICODE,
)
_STREET_WORDS = (r"(?:calle|callej[óo]n|avenida|avda\.?|av\.?|carrera|cra\.?|kr\.?|calzada|calz\.?|boulevard|bulevar|"
                 r"blvd\.?|jir[óo]n|jr\.|pasaje|psje\.?|diagonal|transversal|privada|rua|travessa|alameda|rodovia|"
                 r"estrada|pra[çc]a)")
_STREET = re.compile(
    r"\b" + _STREET_WORDS + r"(?![^\W\d_])\s*"
    r"(?:[^\W\d_][\w'’.-]*[\s,]+){0,4}?"  # the street name: up to 4 words
    r"(?:n[°º.o]?\s*|#\s*|nro\.?\s*|n[úu]m(?:ero|\.)?\s*)?\d{1,5}[a-z]?(?![\d.,]\d|\w)"
    r"(?!\s*(?:pesos|reais|mil|usd|d[óo]lares|\$|%))"  # "calle ... 450 pesos" is an amount
    r"(?:\s*(?:#|-|n[°º])\s*\d{1,5}(?:\s*-\s*\d{1,5})?)?"  # "# 45-10"
    r"(?:\s*,?\s*(?:int(?:erior)?\.?|depto\.?|dpto\.?|departamento|apto\.?|apartamento|ap\.?|piso|casa|bloco|"
    r"torre|of\.?|oficina|sala)\s*\w{1,6})?",
    re.IGNORECASE | re.UNICODE,
)
_CAP = r"(?-i:[A-ZÁÉÍÓÚÑÇÂÊÔÃÕÜ])"
_HOOD = re.compile(
    r"\b(?:col\.|colonia|fracc\.|fraccionamiento|bairro|barrio|urb\.|urbanizaci[óo]n)\s*"
    + _CAP + r"[\w'’-]*(?:\s+(?:" + _CAP + r"[\w'’-]*|(?:de|del|da|do|la|las|los)(?![^\W\d_])))*",
    re.IGNORECASE | re.UNICODE,
)
_POSTAL = re.compile(
    r"\b(?:c\.\s?p\.?|cp|c[óo]digo\s+postal|cep)(?![^\W\d_])\s*[:#.]?\s*\d{5}(?:-?\d{3})?(?!\d)",
    re.IGNORECASE | re.UNICODE,
)

_ACCOUNT_KEYWORD = re.compile(
    r"\b(?:clabe(?:\s+interbancaria)?|cbu|cvu|cci|iban|n[úu]mero\s+de\s+cuenta|n[úu]mero\s+da\s+conta|cuenta|conta)"
    r"(?:\s*(?:es|é|:|#|=|n[°º.]|nro\.?|n[úu]mero|mi|minha)(?![^\W\d_]))*\s*"
    r"((?:\d[\s-]?){9,29}\d)(?!\d)",
    re.IGNORECASE | re.UNICODE,
)
_ACCOUNT_BARE = re.compile(r"(?<![\d.,])\d{20,22}(?!\d)")

_CARD = re.compile(r"(?<![\d.,+])(?:\d[ .-]?){12,18}\d(?![\d])")

_WORD_END = r"(?![^\W\d_])"  # the filler word ends here (not "e" inside "el")
_DOC_KEYWORD = re.compile(
    r"\b(?:dni|curp|rfc|ine|c[ée]dula(?:\s+de\s+(?:ciudadan[íi]a|identidad))?|c\.?\s?c\.?|cpf|rg|cuit|cuil|nit|"
    r"pasaporte|passaporte|documento(?:\s+de\s+identidad)?|identifica[cç][aã]o|n[úu]mero\s+de\s+identidad)"
    r"(?:\s*(?:n[°º.]|n[o]" + _WORD_END + r"|nro\.?|n[úu]mero|num\.?|#|:|=|(?:es|el|la|é|e|o|mi|meu|um)" + _WORD_END + r"))*\s*"
    r"((?:(?-i:[A-Z]{1,3})[\s-]?)?\d(?:[\d.\-/]|\s(?=\d)){3,20}(?-i:[A-Z])?)(?![\w])",
    re.IGNORECASE | re.UNICODE,
)
# The number first, the document word after: "soy 45678912 de DNI", "45.678.912 es mi DNI".
_DOC_AFTER = re.compile(
    r"(?<![\w.,/-])(\d(?:[\d.\-]){5,14}\d)(?=\s+(?:(?:es|é)\s+)?(?:(?:de|del|do|da)\s+)?(?:(?:mi|meu|minha)\s+)?"
    r"(?:dni|cpf|rg|c[ée]dula|curp|ine|cuit|cuil|nit|pasaporte|passaporte|documento)\b)",
    re.IGNORECASE | re.UNICODE,
)
_DOC_SHAPES = [
    re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)"),  # CPF
    re.compile(r"(?<!\d)\d{2}-\d{8}-\d(?!\d)"),  # CUIT / CUIL
    re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b"),  # CURP
    re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b"),  # RFC
]

_CVV = re.compile(
    r"\b(?:cvv2?|cvc2?|cv2|c[óo]digo\s+(?:de\s+)?(?:seguridad|seguran[çc]a|cvv))\b"
    r"(?:\s*(?:es|é|:|#|=)" + _WORD_END + r")*\s*(\d{3,4})(?!\d)",
    re.IGNORECASE | re.UNICODE,
)
_EXPIRY = re.compile(
    r"\b(?:vence|vencimiento|vencimento|venc\.?|vto\.?|expira|expiraci[óo]n|exp\.?|validade|"
    r"v[áa]lid[ao]\s+(?:hasta|at[ée]))"
    r"(?:\s*(?:el|en|em|de|:)" + _WORD_END + r")*\s*((?:0?[1-9]|1[0-2])\s*[/-]\s*(?:20\d{2}|\d{2}))(?![\d/])",
    re.IGNORECASE | re.UNICODE,
)
EXPIRY_YEARS = (24, 45)  # two-digit years a card in use can expire in; "12/06" is a date, not an expiry

# Before a phone: not a word character, currency or separator... except "cel." / "tel." / "wa.".
_PHONE_START = (r"(?:(?<=\bcel\.)|(?<=\btel\.)|(?<=\btlf\.)|(?<=\bwa\.)|(?<=\bfone\.)|(?<=\bcelular\.)"
                r"|(?<![\w$.,/:-]))")
# After a phone: a comma or a dot may end the sentence; followed by a digit it is a number.
_PHONE_END = r"(?![\w/:]|[.,]\d|-\d)"
_PHONE_GROUPED = re.compile(
    _PHONE_START + r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{1,4}\)[\s-]?)?\d{1,5}(?:[\s-]\d{2,5}){1,4}" + _PHONE_END,
    re.IGNORECASE,
)
_PHONE_BARE = re.compile(_PHONE_START + r"\+?\d{10,12}" + _PHONE_END, re.IGNORECASE)
_DATE_LIKE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")


def _digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


def luhn_ok(digits: str) -> bool:
    """Luhn checksum of a digit string (card numbers pass it; most amounts and dates do not)."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return bool(digits) and total % 10 == 0


def redact(text: str, language: str = "es") -> str:
    """Return ``text`` with e-mails, postal addresses, accounts, full card numbers, CVV,
    expiry dates, documents and phones masked.

    Contract: pure, idempotent (redacting twice gives the same text), never raises, keeps
    everything else byte for byte. ``language`` ("es"/"pt", anything else -> "es") only
    chooses the placeholder words.
    """
    if not text:
        return text
    lab = LABELS.get(language, LABELS["es"])
    out = _EMAIL.sub(lab["email"], text)
    addr = lab["address"]
    out = _ADDR_INTRO.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + addr, out)
    out = _STREET.sub(addr, out)
    out = _HOOD.sub(addr, out)
    out = _POSTAL.sub(addr, out)
    out = re.sub(re.escape(addr) + r"(?:\s*,\s*" + re.escape(addr) + r")+", addr, out)

    def keep_prefix(m: re.Match, label: str, min_digits: int = 1) -> str:
        if _digits(m.group(1)) < min_digits:
            return m.group(0)
        return m.group(0)[: m.start(1) - m.start(0)] + label

    out = _ACCOUNT_KEYWORD.sub(lambda m: keep_prefix(m, lab["account"], 10), out)
    out = _ACCOUNT_BARE.sub(lab["account"], out)

    def card(m: re.Match) -> str:
        s = m.group(0)
        digits = re.sub(r"\D", "", s)
        if not 13 <= len(digits) <= 19:
            return s
        if "." in s and not luhn_ok(digits):
            return s  # "12.500.000..." style numbers: only a real card number is masked
        if len(digits) == 18 and s.isdigit() and not luhn_ok(digits):
            return lab["account"]  # CLABE (18 digits)
        return lab["card"].format(last4=digits[-4:])

    out = _CARD.sub(card, out)
    out = _DOC_KEYWORD.sub(lambda m: keep_prefix(m, lab["doc"], 4), out)
    out = _DOC_AFTER.sub(lambda m: lab["doc"] if _digits(m.group(1)) >= 6 else m.group(0), out)
    for shape in _DOC_SHAPES:
        out = shape.sub(lab["doc"], out)
    out = _CVV.sub(lambda m: keep_prefix(m, lab["cvv"]), out)

    def expiry(m: re.Match) -> str:
        year = int(re.split(r"\s*[/-]\s*", m.group(1))[-1]) % 100
        if not EXPIRY_YEARS[0] <= year <= EXPIRY_YEARS[1]:
            return m.group(0)  # "vence el 12/06": a due date, not a card expiry
        return keep_prefix(m, lab["expiry"])

    out = _EXPIRY.sub(expiry, out)

    def phone(m: re.Match) -> str:
        s = m.group(0)
        if _DATE_LIKE.match(s.strip()) or not 8 <= _digits(s) <= 13:
            return s
        return lab["phone"]

    out = _PHONE_GROUPED.sub(phone, out)
    out = _PHONE_BARE.sub(phone, out)
    return out
