"""System texts by key, language and country register (owner: arturo).

Texts live in ``templates/es.yaml`` and ``templates/pt.yaml`` (inside src/ so the Lambda has
them; not the repo-root templates/ folder). Spanish has three registers: tú (MX), usted
(CO), vos (AR); Portuguese has one (você, pt-BR, generated without native review). No
template promises a refund or compensation (``FORBIDDEN_PATTERNS``, enforced by tests).

Also the small formatters the replies need: money, dates and a local time with its zone.
The front end formats with ``Intl``; these are only for text inside ``reply_text``.
"""

from __future__ import annotations

import re
import string
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

TEMPLATES_DIR = Path(__file__).parent / "templates"
LANGUAGES = ("es", "pt")

# Phrases no system text may contain (ES and PT): promises of refund, return of money or
# compensation. A fact from the record ("reversión aplicada", "estorno aplicado") is allowed.
FORBIDDEN_PATTERNS = [
    r"reembols", r"devolv", r"devoluc", r"devoluç", r"reintegr", r"compens", r"indemniz",
    r"indeniz", r"resarc", r"ressarc", r"dinero de vuelta", r"dinheiro de volta",
    r"te pagaremos", r"le pagaremos", r"vamos a pagar", r"vamos pagar", r"estornar",
    r"estornaremos", r"será estornad", r"garantiz", r"garant",
]

# Fixed UTC offsets: none of these countries has daylight saving time today (Mexico since
# 2022 outside the border strip, Colombia never, Argentina since 2009). Avoids needing tzdata.
UTC_OFFSETS = {"MX": -6, "CO": -5, "AR": -3}

MONTHS = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"],
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
           "setembro", "outubro", "novembro", "dezembro"],
}


@lru_cache(maxsize=None)
def _load(language: str) -> dict:
    with open(TEMPLATES_DIR / f"{language}.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data.get("language") != language:
        raise ValueError(f"{language}.yaml declares language {data.get('language')!r}")
    return data


def _lang(language: str | None) -> str:
    return language if language in LANGUAGES else "es"


def register_for(language: str | None, country: str | None) -> str:
    """"tu" | "usted" | "vos" for Spanish by country (default "tu"); "voce" for Portuguese."""
    data = _load(_lang(language))
    return (data.get("registers") or {}).get(country or "", data["default_register"])


def template_keys(language: str) -> set[str]:
    """All keys of one language file."""
    return set(_load(_lang(language))["texts"])


def raw_template(key: str, language: str | None, country: str | None = None) -> str:
    """The unformatted text for key/language/register. Raises KeyError for an unknown key."""
    lang = _lang(language)
    entry = _load(lang)["texts"][key]
    if isinstance(entry, dict):
        reg = register_for(lang, country)
        return entry.get(reg) or entry[_load(lang)["default_register"]]
    return entry


def variables_of(key: str, language: str | None, country: str | None = None) -> set[str]:
    """Names of the {variables} a template needs."""
    return {f for _, f, _, _ in string.Formatter().parse(raw_template(key, language, country)) if f}


def render(key: str, language: str | None, country: str | None = None, **variables: Any) -> str:
    """Text for ``key`` in ``language`` ("es"|"pt"; anything else -> "es") with the register
    of ``country`` ("MX" tú, "CO" usted, "AR" vos; Portuguese always você).

    Contract: raises KeyError for an unknown key or a missing variable; extra variables are
    ignored. Values are inserted as given (format money and dates before, with the helpers
    below). Button labels are keys starting with "btn.", queue names "queue.".
    """
    template = raw_template(key, language, country)
    needed = {f for _, f, _, _ in string.Formatter().parse(template) if f}
    missing = needed - set(variables)
    if missing:
        raise KeyError(f"template {key!r} needs {sorted(missing)}")
    return template.format(**{k: variables[k] for k in needed})


def all_texts(language: str) -> list[tuple[str, str]]:
    """Every (key, text) of a language, every register. For tests and reviews."""
    out = []
    for key, entry in _load(_lang(language))["texts"].items():
        if isinstance(entry, dict):
            out.extend((f"{key}[{reg}]", text) for reg, text in entry.items())
        else:
            out.append((key, entry))
    return out


def forbidden_hits(text: str) -> list[str]:
    """Forbidden promise patterns found in ``text`` (case-insensitive). Empty = clean.
    The orchestrator can run model drafts through it too."""
    low = text.lower()
    return [p for p in FORBIDDEN_PATTERNS if re.search(p, low)]


# ---------------------------------------------------------------- formatters

def format_money(amount: Any, currency: str, locale: str | None = None) -> str:
    """"449.90 MXN" (es-MX: comma thousands, dot decimals), "189.900 COP", "12.500,00 ARS"
    (es-CO, es-AR, pt-BR: dot thousands, comma decimals). Integers keep no decimals unless
    the amount had cents. Raises ValueError for a non-number."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"not an amount: {amount!r}") from e
    decimals = 0 if value == value.to_integral() and "." not in str(amount) else 2
    text = f"{abs(value):,.{decimals}f}"  # 12,500.00
    if (locale or "es-MX") != "es-MX":
        text = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{'-' if value < 0 else ''}{text} {currency}"


def format_date(d: date | str, language: str | None) -> str:
    """"12 de junio de 2026" / "12 de junho de 2026"; pt-BR writes the first day "1º de junho"."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    lang = _lang(language)
    months = MONTHS[lang]
    day = "1º" if lang == "pt" and d.day == 1 else str(d.day)
    return f"{day} de {months[d.month - 1]} de {d.year}"


def local_datetime(dt: datetime, country: str | None) -> datetime:
    """``dt`` (aware; naive is taken as UTC) in the country's fixed offset (UTC if unknown)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    hours = UTC_OFFSETS.get(country or "")
    return dt.astimezone(timezone(timedelta(hours=hours)) if hours is not None else timezone.utc)


def format_datetime(dt: datetime, language: str | None, country: str | None) -> str:
    """"29 de septiembre de 2026, 15:40 (hora de Colombia)" in the customer's zone."""
    local = local_datetime(dt, country)
    zone = render(f"tz.{country}" if country in UTC_OFFSETS else "tz.UTC", language)
    return f"{format_date(local.date(), language)}, {local:%H:%M} ({zone})"


_DURATION_UNITS = (  # (seconds, singular, plural); the words are the same in es and pt
    (3600.0, "hora", "horas"),
    (60.0, "minuto", "minutos"),
    (1.0, "segundo", "segundos"),
)


def format_duration(seconds: float, language: str | None) -> str:
    """A simulated span in the largest unit that fits, at most one decimal, decimal comma:
    "1 minuto", "12 minutos", "0,9 segundos" (pt: "0,9 segundo", singular below 2, as
    Intl.NumberFormat writes it in the console). For the judge's demo clock text."""
    lang = _lang(language)
    size, one, many = next((u for u in _DURATION_UNITS if seconds >= u[0]), _DURATION_UNITS[-1])
    value = round(seconds / size, 1)
    text = f"{value:g}".replace(".", ",")
    singular = value < 2 if lang == "pt" else value == 1
    return f"{text} {one if singular else many}"
