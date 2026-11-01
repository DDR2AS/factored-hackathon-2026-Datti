"""Per-turn language detection by marker words (owner: arturo; RF-27).

A rule, not a model: counts words that are distinctive of Spanish or of Portuguese (plus a
few characters: ñ ¿ ¡ for Spanish, ã õ ç for Portuguese). Words shared by both languages
("de", "que", "dia", "tarifa", "pesos") are not markers. The session language is only the
default; the reply language follows the message, and "mixed" means the orchestrator asks.
"""

from __future__ import annotations

import re

ES_MARKERS = frozenset("""
    hola gracias usted ustedes tarjeta cuenta cobraron cobró cobro reconozco reconocés
    reconoce hice yo mi mis una unos unas ayer anteayer hoy quiero quisiera necesito
    pero el los las del con es y qué cómo dónde cuándo porqué
    sí si hablar persona asesor comisión mantenimiento cuota manejo préstamo prestamo
    ahora bien vale bueno tengo puedo mío mía están estoy señor señora
    vos sos tenés querés podés mañana siempre dinero plata
""".split())

PT_MARKERS = frozenset("""
    olá oi obrigado obrigada você voce vocês não nao sim cartão cartao conta cobraram
    cobrou cobrança cobranca reconheço reconheco fiz eu meu minha meus minhas um uma
    ontem anteontem hoje quero queria preciso ao com é são foi
    pessoa atendente manutenção manutencao taxa empréstimo emprestimo agora bem tenho
    posso estou senhor senhora amanhã sempre dinheiro isso
    esse essa então muito pela nem até já errada errado
""".split()) - ES_MARKERS

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def marker_counts(text: str) -> tuple[int, int]:
    """(spanish_score, portuguese_score) for ``text``. Pure; used by tests and traces."""
    es = pt = 0
    for raw in _WORD.findall(text or ""):
        w = raw.lower()
        if w in ES_MARKERS:
            es += 1
        elif w in PT_MARKERS:
            pt += 1
        if "ñ" in w:
            es += 1
        if any(ch in w for ch in "ãõç"):
            pt += 1
    es += (text or "").count("¿") + (text or "").count("¡")
    return es, pt


def detect_language(text: str, default: str | None = None) -> str | None:
    """Language of one message: "es", "pt", "mixed", or ``default`` when there is no signal.

    Contract: no marker at all -> ``default`` (may be None). Both languages present and
    neither has at least twice the other's score -> "mixed". Otherwise the language with
    the higher score. Never raises.
    """
    es, pt = marker_counts(text)
    if es == 0 and pt == 0:
        return default
    if es > 0 and pt > 0 and max(es, pt) < 2 * min(es, pt):
        return "mixed"
    return "es" if es > pt else "pt"
