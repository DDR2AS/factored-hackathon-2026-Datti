"""Deterministic slot and flag extractor, ES and PT (owner: arturo). Fallback of G1.

G1 (the LLM extractor, ``conversation.g1``) is called by the orchestrator through the #8
port: ``complete("g1_extract", variables, output_schema=Extraction.model_json_schema())``.
Its output is validated into the same ``Extraction`` model and guarded with the
``mentioned_*`` helpers at the end of this module; on timeout, an unavailable provider, an
invalid output or no mock fixture, the orchestrator uses ``extract()`` from this module
(``reply_source: "template"``; ``degraded`` visible on timeout/unavailable). Run it on
redacted text (``redact.redact``): redaction keeps amounts, dates and last-4 mentions intact.

What it finds:
- amount: "como 450", "12.500 pesos", "R$ 35,90", "$1,299", "245 mil", "449.90". A dot or
  comma followed by exactly three digits is a thousands separator; by one or two, decimals.
- currency: only when said explicitly (R$/reais -> BRL, USD/dólares, MXN, COP, ARS,
  "pesos mexicanos|colombianos|argentinos"). Plain "$" or "pesos" -> None (ambiguous).
- date: "el 12", "dia 1", "12 de junio", "1 de junho de 2026", "12/06", "2026-06-12",
  "ayer/ontem", "anteayer/anteontem", "hoy/hoje", "hace 3 días/há 3 dias"; resolved against
  ``reference_date`` (the customer's latest data day). A day alone means the latest such day
  on or before the reference date. ``date_is_relative`` is True unless month was given.
- merchant_text: words after "en / en el / en la" (ES) or "na / no / em / do / da" (PT),
  or an UPPERCASE run such as "STREAMING PLUS"; stops at dates, numbers and stopwords.
- card_last4: "terminada en 4821", "final 4821", "•••• 4821", "últimos 4 dígitos 4821".
- flags: asks_for_human, mentions_regulator, asks_compensation, complaint_about_person,
  lost_card (pattern lists below).
- complaint_type (suggested, not final): unrecognized_charge, wrong_fee (cobro indebido:
  fee, duplicate, charged more), app, branch, service.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict

from conversation.textnorm import fold

EXTRACTOR_VERSION = "g1-regex-0"

ComplaintType = Literal["unrecognized_charge", "wrong_fee", "app", "branch", "service"]


class Slots(BaseModel):
    """#4 slots. Everything is what the customer SAID (unverified)."""

    model_config = ConfigDict(extra="forbid")
    amount: str | None = None  # decimal string
    currency: str | None = None  # ISO 4217, only if said explicitly
    date: str | None = None  # ISO date
    date_is_relative: bool | None = None
    merchant_text: str | None = None
    card_last4: str | None = None


class Flags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asks_for_human: bool = False
    mentions_regulator: bool = False
    asks_compensation: bool = False
    complaint_about_person: bool = False
    lost_card: bool = False


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slots: Slots = Slots()
    flags: Flags = Flags()
    complaint_type: ComplaintType | None = None
    extractor_version: str = EXTRACTOR_VERSION


# ---------------------------------------------------------------- patterns (folded text)

MONTHS = {
    "enero": 1, "ene": 1, "janeiro": 1, "jan": 1,
    "febrero": 2, "feb": 2, "fevereiro": 2, "fev": 2,
    "marzo": 3, "marco": 3, "mar": 3,
    "abril": 4, "abr": 4,
    "mayo": 5, "maio": 5, "may": 5, "mai": 5,
    "junio": 6, "junho": 6, "jun": 6,
    "julio": 7, "julho": 7, "jul": 7,
    "agosto": 8, "ago": 8,
    "septiembre": 9, "setiembre": 9, "setembro": 9, "sep": 9, "set": 9,
    "octubre": 10, "outubro": 10, "oct": 10, "out": 10,
    "noviembre": 11, "novembro": 11, "nov": 11,
    "diciembre": 12, "dezembro": 12, "dic": 12, "dez": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

_RE_LAST4 = re.compile(
    r"(?:terminad[ao]s?\s+(?:en|em)|termina\s+(?:en|em)|que\s+termina\s+(?:en|em)|finalizad[ao]\s+(?:en|em)"
    r"|final(?:es)?(?:\s+(?:en|em))?|ultimos\s+(?:4|cuatro|quatro)\s*(?:digitos)?(?:\s+(?:son|sao|es|e))?"
    r"|(?:tarjeta|cartao)(?:\s+(?:de\s+)?(?:credito|debito))?(?:\s+(?:n[or]\.?|numero))?"
    r"|[•*·x]{2,}|\[(?:tarjeta|cartao)\s*[•*]{2,})\s*:?\s*(\d{4})(?!\d)"
)
_RE_ISO = re.compile(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")
_RE_SLASH = re.compile(r"(?<![\d$.,])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")
_RE_DAY_MONTH = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*(?:de\s+)?({_MONTH_RE})\b\.?(?:\s*(?:de|del)?\s*(20\d{{2}}))?"
)
_RE_DAY_ONLY = re.compile(
    r"\b(?:el|del|dia|o\s+dia|no\s+dia|desde\s+el)\s+(?:dia\s+)?(\d{1,2})(?![\d:%]|[.,]\d)(?!\s*(?:mil|pesos|reais|dolares|usd|mxn|cop|ars|%|veces|vezes|dias|meses))"
)
# "junio 12", "junho 12 de 2026": full month names only ("mar 12" could be anything).
_MONTH_FULL_RE = "|".join(sorted((m for m in MONTHS if len(m) > 3), key=len, reverse=True))
_RE_MONTH_DAY = re.compile(
    rf"\b({_MONTH_FULL_RE})\s+(\d{{1,2}})(?![\d.,:])(?!\s*(?:mil|pesos|reais|dolares|usd|mxn|cop|ars|brl|%))"
    rf"(?:\s*(?:,|de|del)?\s*(20\d{{2}}))?"
)
_WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6,
    "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4,
}
# ES weekdays alone; PT ones with "-feira" or after "na/nesta/última" ("segunda vez" is not a day).
_RE_WEEKDAY = re.compile(
    r"\b(?:(lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
    r"|(segunda|terca|quarta|quinta|sexta)-?\s?feira"
    r"|(?:na|nesta|nessa|ultima)\s+(segunda|terca|quarta|quinta|sexta)"
    r"|(?:no|neste|nesse|ultimo)\s+(sabado|domingo))\b"
)
_RE_AGO = re.compile(r"\b(?:hace|ha|faz)\s+(\d{1,2}|un|una|um|uma|dos|tres|duas|tres)\s+(dias?|semanas?)\b")
_REL_WORDS = [
    (re.compile(r"\b(?:anteayer|antier|antes\s+de\s+ayer|anteontem)\b"), 2),
    (re.compile(r"\b(?:ayer|ontem)\b"), 1),
    (re.compile(r"\b(?:hoy|hoje)\b"), 0),
]
_NUMBER_WORDS = {"un": 1, "una": 1, "um": 1, "uma": 1, "dos": 2, "duas": 2, "tres": 3}

_RE_AMOUNT = re.compile(
    r"(?P<cur1>r\$|us\$|u\$s|\b(?:usd|mxn|cop|ars|brl)|\$)?\s*"
    r"(?<![\d:.,/\-])(?P<num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\d])"
    r"(?:\s*(?P<mult>mil(?:hoes|hao|lones|lon)?\b|k\b))?"
    r"(?:\s*(?:de\s+)?(?P<cur2>pesos\s+(?:mexicanos|colombianos|argentinos)|pesos|reais|real|dolares|usd|mxn|cop|ars|brl)\b)?"
)
_AMOUNT_CUES = re.compile(r"(?:como|unos|unas|aprox\w*|cerca de|de|por|cobr\w+|cargo|valor|total|monto|pague|paguei|debitaron|descontaron)\s*$")
_NOT_AMOUNT_AFTER = re.compile(r"^\s*(?:%|:\d|dias?\b|veces\b|vezes\b|meses\b|horas?\b|cuotas\b|parcelas\b|anos?\b|digitos\b)")

_CURRENCY = {
    "r$": "BRL", "reais": "BRL", "real": "BRL", "brl": "BRL",
    "us$": "USD", "u$s": "USD", "usd": "USD", "dolares": "USD",
    "mxn": "MXN", "pesos mexicanos": "MXN",
    "cop": "COP", "pesos colombianos": "COP",
    "ars": "ARS", "pesos argentinos": "ARS",
}

_STOP = set("""
    el la los las lo un una unos unas y e o u que por para con com de del do da dos das
    dia hace ha faz ayer hoy ontem hoje anteayer anteontem pero mas porque pq a al ao me mi
    mis meu minha se no na nos nas em en fue foi es era cuando quando como
    tambien tampoco ya ja hoje semana mes pasado passado ultimo ultima
""".split())
_NOT_MERCHANT = set("""
    app aplicacion aplicativo sucursal agencia oficina cuenta conta tarjeta cartao linea
    internet web pagina casa efectivo dinheiro dinero banco cajero mes semana fecha data
    compra cargo cobro cobranca comision comissao tarifa taxa movimientos extracto extrato resumen
    total realidad verdad serio general cuanto pesos reais dolares
    reconozco reconheco hice fiz fui autorice autorizei recuerdo lembro entiendo entendo sei se
    espanol espanhol castellano portugues ingles idioma favor
""".split()) | set(MONTHS)
_CHANNEL_WORDS = {"app", "aplicacion", "aplicativo", "pagina", "web", "sitio", "site"}
_RE_AFTER_AMOUNT_DE = re.compile(r"(?:\d|\bmil|\bpesos|\breais|\bdolares)\s+(?:de|del|do|da)\s+")
_RE_MERCHANT_ES = re.compile(r"\b(?:en\s+(?:el|la|los|las)\s+|en\s+)", re.UNICODE)
_RE_MERCHANT_PT = re.compile(r"\b(?:na|no|nas|nos|em|do|da)\s+", re.UNICODE)
_RE_UPPER_RUN = re.compile(r"(?<![\w*])([A-Z0-9][A-Z0-9&*.'-]*[A-Z](?:[A-Z0-9&*.'-]*)(?:\s+[A-Z0-9&*.'-]{2,}){0,3})(?![\w])")
_UPPER_IGNORE = set("""
    CONDUSEF SFC BCRA PROCON BACEN DNI CPF RFC CURP INE NIT CUIT CUIL USD MXN COP ARS BRL
    OK SI NO EV PT ES SMS PIN CVV ATM
""".split())

FLAG_PATTERNS: dict[str, list[str]] = {
    "asks_for_human": [
        r"\b(?:hablar|comunicarme|comunicame|comunicas|comunican|comunicar|pasarme|pasame|pasas|pasan|contactar|atienda|atiendan)\s+(?:con\s+)?(?:una?\s+|el\s+|la\s+|algun\s+|alguna\s+)?(?:persona|humano|asesor|asesora|agente|ejecutivo|ejecutiva|operador|operadora|alguien)\b",
        # Short answers to "¿revisar los datos o hablar con una persona?" (whole message).
        r"^\W*(?:(?:si|sim|ok|claro|bueno|vale|dale|mejor|prefiero|quiero|quero|prefiro)\W+)*(?:con\s+|com\s+)?(?:una?\s+|um\s+|uma\s+|el\s+|la\s+|o\s+|a\s+|algun\s+|alguna\s+|algum\s+)?(?:persona|pessoa|humano|humana|asesor|asesora|atendente|agente|ejecutivo|ejecutiva|operador|operadora|alguien|alguem)(?:\s+(?:real|de\s+verdade|humana|humano))?(?:\W+(?:por\s+favor|porfa|pf|pls|please|obrigad[oa]|gracias))?\W*$",
        r"\b(?:quiero|necesito|prefiero|pido)\s+(?:una?\s+|a\s+)?(?:persona|humano|asesor|asesora|agente|ejecutivo)\b",
        r"\bpersona\s+real\b", r"\bser\s+humano\b",
        r"\b(?:falar|conversar)\s+com\s+(?:uma?\s+|o\s+|a\s+|algum\s+)?(?:pessoa|humano|atendente|alguem|agente|gerente)\b",
        r"\b(?:quero|preciso\s+de)\s+(?:uma?\s+)?(?:pessoa|atendente|humano)\b",
        r"\bpessoa\s+de\s+verdade\b", r"\batendente\s+humano\b",
    ],
    "mentions_regulator": [
        r"\b(?:condusef|profeco|sfc|superfinanciera|superintendencia|bcra|defensa\s+del\s+consumidor|defensoria\s+del\s+consumidor|defensor\s+del\s+consumidor(?:\s+financiero)?)\b",
        r"\b(?:banco\s+central|procon|bacen|consumidor\.gov|reclame\s+aqui)\b",
        r"\b(?:regulador(?:es|a)?|ente\s+regulador|organo\s+regulador|orgao\s+regulador)\b",
        r"\b(?:abogad[oa]s?|advogad[oa]s?|demanda(?:r|re|ria|remos)?|juicio|tutela|juzgado|tribunal|processar|processo\s+judicial|justica|accion\s+legal|via\s+legal|acao\s+judicial)\b",
    ],
    "asks_compensation": [
        r"\b(?:compensacion|compensen|compensar(?:me)?|indemnizacion|indemnicen|indemnizar(?:me)?|resarcimiento|resarcir|danos\s+y\s+perjuicios)\b",
        r"\b(?:compensacao|compensem|indenizacao|indenizar|indenizem|danos\s+morais)\b",
        r"\b(?:me\s+paguen|me\s+pagan|que\s+me\s+paguem)\s+(?:por|los|el|pelos?|os)\b",
    ],
    "complaint_about_person": [
        r"\b(?:grosero|grosera|groseros|me\s+grito|me\s+gritaron|me\s+insulto|me\s+insultaron|me\s+humillo|maltrat\w*|me\s+trato\s+mal|me\s+trataron\s+mal|mal\s+educad[oa]|maleducad[oa]|irrespetuos[oa]|prepotente|discrimin\w*)\b",
        r"\b(?:grosseir[oa]|me\s+destratou|me\s+tratou\s+mal|me\s+trataram\s+mal|mal[-\s]educad[oa]|gritou\s+comigo|me\s+humilhou|desrespeitos[oa])\b",
    ],
    "lost_card": [
        r"\b(?:perdi|extravie|se\s+me\s+perdio|me\s+robaron|robaron|me\s+hurtaron|roubaram|furtaram|perdi\s+o|perdi\s+meu|perdi\s+minha)\b.{0,30}\b(?:tarjeta|cartera|billetera|cartao|carteira)\b",
        r"\b(?:tarjeta|cartao)\s+(?:\w+\s+)?(?:perdida|robada|extraviada|hurtada|perdido|roubado|furtado|extraviado)\b",
        r"\b(?:robo|hurto|roubo|furto|perdida|perda)\s+de\s+(?:mi\s+|la\s+|meu\s+|do\s+)?(?:tarjeta|cartao)\b",
        r"\bbloque(?:ar|a|as|en|e|es|ia|iem)\s+(?:mi|la|o|meu|minha)\s+(?:tarjeta|cartao)\b",
    ],
}

TYPE_PATTERNS: list[tuple[str, str]] = [
    ("unrecognized_charge", r"\b(?:no\s+(?:lo\s+|la\s+)?reconozco|desconozco|no\s+(?:la\s+|lo\s+)?hice|no\s+fui\s+yo|no\s+autorice|nao\s+reconheco|nao\s+fui\s+eu|nao\s+fiz|nao\s+autorizei|desconheco|fraude|clonaron|clonada|clonado|clonaram)\b"),
    ("wrong_fee", r"\b(?:comision|comisiones|comissao|comissoes|tarifa|cuota\s+de\s+manejo|mantenimiento|anualidad|cobro\s+indebido|de\s+mas|dos\s+veces|2\s+veces|doble\s+cobro|cobro\s+doble|duplicad[oa]s?|repetid[oa]s?|taxa|manutencao|anuidade|a\s+mais|duas\s+vezes|em\s+dobro|cobranca\s+indevida)\b"),
    ("app", r"\b(?:app|aplicacion|aplicativo|no\s+me\s+deja\s+(?:entrar|ingresar)|se\s+cierra|se\s+cae|no\s+carga|banca\s+(?:en\s+linea|movil)|nao\s+abre|nao\s+consigo\s+entrar|travou|travando)\b"),
    ("branch", r"\b(?:sucursal|agencia|oficina)\b"),
    ("service", r"\b(?:atencion|servicio\s+al\s+cliente|call\s+center|me\s+atendieron|atendimento|me\s+atenderam|mal\s+servicio)\b"),
]
_CHARGE_WORDS = re.compile(r"\b(?:cobr\w+|cargo|cargaron|compra|descont\w+|debit\w+)\b")
_CHARGE_VERBS = re.compile(r"\b(?:cobr(?:aron|o|ou|aram|an|am)|cargaron|descont\w+|debit(?:aron|ou|aram)|me\s+sacaron)\b")

_FLAG_RES = {k: [re.compile(p) for p in v] for k, v in FLAG_PATTERNS.items()}
_TYPE_RES = [(k, re.compile(p)) for k, p in TYPE_PATTERNS]


# ---------------------------------------------------------------- helpers

def parse_amount(num: str) -> Decimal | None:
    """"12.500" -> 12500; "1,299" -> 1299; "35,90" -> 35.90; "1.299,00" -> 1299.00."""
    s = num.strip()
    try:
        if "," in s and "." in s:
            dec = "," if s.rfind(",") > s.rfind(".") else "."
            thou = "." if dec == "," else ","
            return Decimal(s.replace(thou, "").replace(dec, "."))
        for sep in (",", "."):
            if sep in s:
                parts = s.split(sep)
                if len(parts) > 2 or len(parts[-1]) == 3:
                    return Decimal(s.replace(sep, ""))
                return Decimal(s.replace(sep, "."))
        return Decimal(s)
    except InvalidOperation:
        return None


def _last_on_or_before(day: int, ref: date) -> date | None:
    y, m = ref.year, ref.month
    for _ in range(3):
        try:
            d = date(y, m, day)
            if d <= ref:
                return d
        except ValueError:
            pass
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return None


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in spans)


def _find_last4(f: str, used: list) -> str | None:
    m = _RE_LAST4.search(f)
    if m:
        used.append(m.span())
        return m.group(1)
    return None


def _find_date(f: str, ref: date | None, used: list) -> tuple[str | None, bool | None]:
    m = _RE_ISO.search(f)
    if m:
        used.append(m.span())
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat(), False
        except ValueError:
            return None, None
    m = _RE_DAY_MONTH.search(f)
    if m:
        used.append(m.span())
        day, month = int(m[1]), MONTHS[m[2]]
        year = int(m[3]) if m[3] else (ref.year if ref else None)
        if year is None:
            return None, False
        try:
            d = date(year, month, day)
        except ValueError:
            return None, None
        if not m[3] and ref and d > ref:
            try:
                d = date(year - 1, month, day)
            except ValueError:
                return None, None
        return d.isoformat(), False
    m = _RE_MONTH_DAY.search(f)
    if m and 1 <= int(m[2]) <= 31:
        used.append(m.span())
        month, day = MONTHS[m[1]], int(m[2])
        year = int(m[3]) if m[3] else (ref.year if ref else None)
        if year is None:
            return None, False
        try:
            d = date(year, month, day)
            if not m[3] and ref and d > ref:
                d = date(year - 1, month, day)
        except ValueError:
            return None, None
        return d.isoformat(), False
    m = _RE_SLASH.search(f)
    if m and 1 <= int(m[2]) <= 12:
        used.append(m.span())
        year = m[3]
        y = (int(year) + 2000 if len(year) == 2 else int(year)) if year else (ref.year if ref else None)
        if y is None:
            return None, False
        try:
            d = date(y, int(m[2]), int(m[1]))
        except ValueError:
            return None, None
        if not year and ref and d > ref:
            d = d.replace(year=d.year - 1)
        return d.isoformat(), False
    m = _RE_DAY_ONLY.search(f)
    if m and 1 <= int(m[1]) <= 31:
        used.append(m.span())
        d = _last_on_or_before(int(m[1]), ref) if ref else None
        return (d.isoformat() if d else None), True
    m = _RE_AGO.search(f)
    if m:
        used.append(m.span())
        n = int(m[1]) if m[1].isdigit() else _NUMBER_WORDS.get(m[1], 1)
        days = n * (7 if m[2].startswith("semana") else 1)
        return ((ref - timedelta(days=days)).isoformat() if ref else None), True
    for rx, back in _REL_WORDS:
        m = rx.search(f)
        if m:
            used.append(m.span())
            return ((ref - timedelta(days=back)).isoformat() if ref else None), True
    m = _RE_WEEKDAY.search(f)
    if m:
        used.append(m.span())
        wd = _WEEKDAYS[next(g for g in m.groups() if g)]
        if ref is None:
            return None, True
        return (ref - timedelta(days=(ref.weekday() - wd) % 7)).isoformat(), True  # latest on or before
    return None, None


# ---------------------------------------------------------------- amounts in words (ES/PT, basic)

_NUM_UNITS = {
    "uno": 1, "un": 1, "una": 1, "um": 1, "uma": 1, "dos": 2, "dois": 2, "duas": 2, "tres": 3, "cuatro": 4,
    "quatro": 4, "cinco": 5, "seis": 6, "siete": 7, "sete": 7, "ocho": 8, "oito": 8, "nueve": 9, "nove": 9,
    "diez": 10, "dez": 10, "once": 11, "onze": 11, "doce": 12, "doze": 12, "trece": 13, "treze": 13,
    "catorce": 14, "catorze": 14, "quatorze": 14, "quince": 15, "quinze": 15, "dieciseis": 16, "dezesseis": 16,
    "diecisiete": 17, "dezessete": 17, "dieciocho": 18, "dezoito": 18, "diecinueve": 19, "dezenove": 19,
    "veinte": 20, "vinte": 20, "veintiuno": 21, "veintiun": 21, "veintiuna": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29, "treinta": 30, "trinta": 30, "cuarenta": 40, "quarenta": 40,
    "cincuenta": 50, "cinquenta": 50, "sesenta": 60, "sessenta": 60, "setenta": 70, "ochenta": 80,
    "oitenta": 80, "noventa": 90, "cien": 100, "ciento": 100, "cem": 100, "cento": 100,
    "doscientos": 200, "doscientas": 200, "duzentos": 200, "duzentas": 200, "trescientos": 300,
    "trescientas": 300, "trezentos": 300, "trezentas": 300, "cuatrocientos": 400, "cuatrocientas": 400,
    "quatrocentos": 400, "quatrocentas": 400, "quinientos": 500, "quinientas": 500, "quinhentos": 500,
    "quinhentas": 500, "seiscientos": 600, "seiscientas": 600, "seiscentos": 600, "seiscentas": 600,
    "setecientos": 700, "setecientas": 700, "setecentos": 700, "setecentas": 700, "ochocientos": 800,
    "ochocientas": 800, "oitocentos": 800, "oitocentas": 800, "novecientos": 900, "novecientas": 900,
    "novecentos": 900, "novecentas": 900,
}
_RE_WORD = re.compile(r"[a-z]+")
_WORD_CUES = re.compile(r"(?:como|unos|unas|aprox\w*|cerca de|de|por|cobr\w+|cargo|valor|total|monto|"
                        r"pague|paguei|debitaron|descontaron|fueron|foram|eran|era|son)\s*$")


def _find_amount_words(f: str, used: list) -> str | None:
    """"cuatrocientos cincuenta" -> "450". Only after a money cue ("cobraron", "de", "como"...)
    or before a currency word, and only for values from 10 up ("un cargo", "dos veces" stay out)."""
    words = [(m.group(0), m.start(), m.end()) for m in _RE_WORD.finditer(f)]
    i = 0
    while i < len(words):
        w, start, _ = words[i]
        if w not in _NUM_UNITS and w != "mil":
            i += 1
            continue
        total, current, j, end, n = 0, 0, i, words[i][2], 0
        while j < len(words):
            wj = words[j][0]
            if wj in _NUM_UNITS:
                current += _NUM_UNITS[wj]
            elif wj == "mil":
                total += (current or 1) * 1000
                current = 0
            elif wj in ("y", "e") and j + 1 < len(words) and words[j + 1][0] in _NUM_UNITS and n:
                j += 1
                continue
            else:
                break
            end, n, j = words[j][2], n + 1, j + 1
        value = total + current
        span = (start, end)
        after = f[end:]
        cue = _WORD_CUES.search(f[max(0, start - 16):start]) or re.match(
            r"\s*(?:de\s+)?(?:pesos|reais|real|dolares)\b", after)
        if value >= 10 and cue and not _overlaps(span, used) and not _NOT_AMOUNT_AFTER.match(after):
            used.append(span)
            return str(value)
        i = max(j, i + 1)
    return None


def _find_amount(f: str, used: list) -> tuple[str | None, str | None]:
    best = None
    for m in _RE_AMOUNT.finditer(f):
        span = m.span("num")
        if _overlaps(span, used):
            continue
        if _NOT_AMOUNT_AFTER.match(f[m.end("num"):]) and not (m["cur1"] or m["cur2"]):
            continue
        num = m["num"]
        value = parse_amount(num)
        if value is None or value == 0:
            continue
        cur_token = m["cur1"] or m["cur2"]
        if not cur_token and not m["mult"] and 2000 <= value <= 2100 and "." not in num and "," not in num:
            continue  # a year
        score = 0
        if cur_token:
            score += 3
        if m["mult"]:
            score += 2
            value *= 1_000_000 if m["mult"].startswith("mil") and len(m["mult"]) > 3 else 1000
        if _AMOUNT_CUES.search(f[max(0, m.start() - 16):m.start()]):
            score += 1
        if "." in num or "," in num:
            score += 1
        cand = (score, -m.start(), value, _CURRENCY.get(re.sub(r"\s+", " ", cur_token or "")), span)
        if best is None or cand[:2] > best[:2]:
            best = cand
    if best is None:
        return None, None
    used.append(best[4])
    value = best[2]
    text = format(value.normalize(), "f") if value == value.to_integral() else str(value)
    return text, best[3]


def _clean_merchant(words: list[str]) -> str | None:
    kept: list[str] = []
    for w in words:
        fw = fold(w).strip(".,;:!?¿¡\"'()")
        if not fw or fw in _STOP or fw.isdigit() or re.fullmatch(r"\d+[.,]\d+", fw):
            break
        kept.append(w.strip(".,;:!?¿¡\"'()"))
    if not kept or fold(kept[0]) in _NOT_MERCHANT:
        return None
    return " ".join(kept)


def _find_merchant(text: str, f: str, language: str | None) -> str | None:
    regexes = [_RE_MERCHANT_ES] + ([_RE_MERCHANT_PT] if language in ("pt", "mixed") else [])
    for rx in regexes:
        for m in rx.finditer(f):
            words = text[m.end():].split()[:6]
            # "en la app de rappi" -> "rappi": the channel is not the merchant
            if len(words) > 2 and fold(words[0]) in _CHANNEL_WORDS and fold(words[1]) in ("de", "del", "do", "da"):
                words = words[2:]
            found = _clean_merchant(words[:4])
            if found:
                return found
    if text.upper() != text:
        for m in _RE_UPPER_RUN.finditer(text):
            tokens = [t for t in m.group(1).split()
                      if t.strip(".,") not in _UPPER_IGNORE and not t.startswith("EV-")]
            if tokens and any(re.search(r"[A-Z]{2,}", t) for t in tokens):
                return " ".join(tokens)
    # "16.900 de musica stream": "de <name>" right after an amount
    for m in _RE_AFTER_AMOUNT_DE.finditer(f):
        found = _clean_merchant(text[m.end():].split()[:4])
        if found:
            return found
    return None


# ---------------------------------------------------------------- public

def extract(text: str, language: str | None = None, reference_date: date | None = None) -> Extraction:
    """Slots, flags and suggested complaint type from one customer message.

    text: the message (preferably already redacted). language: "es", "pt", "mixed" or None;
    Portuguese prepositions for the merchant are only used for "pt"/"mixed" because "no" is
    a negation in Spanish. reference_date: the customer's reference date for relative
    dates; without it, relative dates give ``date=None``.
    Contract: pure, deterministic, never raises; unknown slots are None; nothing is
    verified here (the gateway verifies).
    """
    text = unicodedata.normalize("NFC", text or "")
    f = fold(text)
    used: list[tuple[int, int]] = []
    last4 = _find_last4(f, used)
    iso, rel = _find_date(f, reference_date, used)
    amount, currency = _find_amount(f, used)
    if amount is None:
        amount = _find_amount_words(f, used)
    merchant = _find_merchant(text, f, language)
    flags = Flags(**{k: any(rx.search(f) for rx in rxs) for k, rxs in _FLAG_RES.items()})
    ctype = next((k for k, rx in _TYPE_RES if rx.search(f)), None)
    if ctype is None and flags.complaint_about_person:
        ctype = "service"
    if ctype is None and _CHARGE_WORDS.search(f) and (amount or merchant or iso):
        ctype = "unrecognized_charge"
    # "me cobraron 52 mil en la app de rappi": money was taken, the app is only where. A charge
    # verb (or an amount next to a charge word) outranks app/branch/service.
    if ctype in ("app", "branch", "service") and _CHARGE_WORDS.search(f) and (_CHARGE_VERBS.search(f) or amount):
        ctype = "unrecognized_charge"
    return Extraction(
        slots=Slots(amount=amount, currency=currency, date=iso, date_is_relative=rel,
                    merchant_text=merchant, card_last4=last4),
        flags=flags,
        complaint_type=ctype,
    )


# ---------------------------------------------------------------- what the text mentions (G1 guards)
# The anti-hallucination guards of G1 (conversation.g1) keep a model amount or last-4 only if it
# is mentioned in the redacted text, after normalizing the ways people say numbers. These
# helpers are generous on purpose (they list everything that could be read as a number); the
# guard only asks "was this said at all?".

_DIGIT_WORDS = {
    "cero": "0", "zero": "0", "uno": "1", "una": "1", "um": "1", "uma": "1", "dos": "2", "dois": "2",
    "duas": "2", "tres": "3", "cuatro": "4", "quatro": "4", "cinco": "5", "seis": "6", "siete": "7",
    "sete": "7", "ocho": "8", "oito": "8", "nueve": "9", "nove": "9",
}
_THOUSANDS_SLANG = re.compile(r"^\s*(?:lucas?|k)\b")  # "52 lucas" (CO/AR) = 52 000
_RE_CON_CENTS = re.compile(r"(?<![\d.,])(\d{1,7})\s+con\s+(\d{2})(?!\d)")  # "449 con 90"
_RE_MIL_REST = re.compile(r"(?<![\d.,])(\d{1,3})\s*mil\s+(\d{1,3})(?![\d.,])")  # "51 mil 500"
_RE_CURRENCY_SAID = re.compile(
    r"r\$|us\$|u\$s|\b(?:pesos\s+(?:mexicanos|colombianos|argentinos)|reais|real|dolares|usd|mxn|cop|ars|brl)\b")


def mentioned_amounts(text: str) -> set[Decimal]:
    """Every number the (redacted) text could be saying as an amount, normalized: digits with
    thousands/decimal separators, "mil"/"k"/"lucas" multipliers, "449 con 90", "51 mil 500",
    and number words ("cuatrocientos cincuenta", "doze mil e quinhentos") with every prefix of
    a run ("ciento ochenta y nueve mil novecientos dos veces" also gives 189900)."""
    f = fold(unicodedata.normalize("NFC", text or ""))
    out: set[Decimal] = set()
    for m in _RE_AMOUNT.finditer(f):
        value = parse_amount(m["num"])
        if value is None:
            continue
        out.add(value)
        if m["mult"]:
            out.add(value * (1_000_000 if m["mult"].startswith("mil") and len(m["mult"]) > 3 else 1000))
        if _THOUSANDS_SLANG.match(f[m.end("num"):]):
            out.add(value * 1000)
    for m in _RE_CON_CENTS.finditer(f):
        out.add(Decimal(f"{m[1]}.{m[2]}"))
    for m in _RE_MIL_REST.finditer(f):
        out.add(Decimal(m[1]) * 1000 + Decimal(m[2]))
    words = [(m.group(0), m.end()) for m in _RE_WORD.finditer(f)]
    for i in range(len(words)):
        total, current, n = 0, 0, 0
        for j in range(i, len(words)):
            w = words[j][0]
            if w in _NUM_UNITS:
                current += _NUM_UNITS[w]
            elif w == "mil":
                total += (current or 1) * 1000
                current = 0
            elif w in ("y", "e") and n:
                continue
            else:
                break
            n += 1
            value = Decimal(total + current)
            out.add(value)
            if _THOUSANDS_SLANG.match(f[words[j][1]:]):
                out.add(value * 1000)
    return {v.normalize() for v in out if v > 0}


def amount_is_mentioned(amount: str, text: str) -> bool:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return False
    return value > 0 and value.normalize() in mentioned_amounts(text)


def mentioned_last4(text: str) -> set[str]:
    """Four-digit groups the text says: any run of 4+ digits (its last four and every window)
    and runs of digit words ("cuatro ocho dos uno" -> "4821")."""
    f = fold(unicodedata.normalize("NFC", text or ""))
    out: set[str] = set()
    for m in re.finditer(r"\d{4,}", f):
        run = m.group(0)
        out.update(run[i:i + 4] for i in range(len(run) - 3))
    run = ""
    for w in _RE_WORD.findall(f) + [""]:
        if w in _DIGIT_WORDS:
            run += _DIGIT_WORDS[w]
            continue
        out.update(run[i:i + 4] for i in range(len(run) - 3))
        run = ""
    return out


def mentioned_currencies(text: str) -> set[str]:
    """ISO codes said explicitly (the same rule as the regex extractor: "$" or "pesos" alone
    say nothing)."""
    f = fold(unicodedata.normalize("NFC", text or ""))
    return {_CURRENCY[re.sub(r"\s+", " ", m.group(0))] for m in _RE_CURRENCY_SAID.finditer(f)
            if re.sub(r"\s+", " ", m.group(0)) in _CURRENCY}


def merchant_is_mentioned(merchant: str, text: str) -> bool:
    """Every word of the merchant appears in the text (accents and case aside)."""
    said = set(re.findall(r"[a-z0-9]+", fold(unicodedata.normalize("NFC", text or ""))))
    words = re.findall(r"[a-z0-9]+", fold(merchant or ""))
    return bool(words) and all(w in said for w in words)
