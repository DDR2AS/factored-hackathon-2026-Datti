"""Text folding shared by the keyword rules (extract, classifier)."""

from __future__ import annotations

import unicodedata


def _fold_char(ch: str) -> str:
    base = unicodedata.normalize("NFKD", ch)[:1] or ch
    return base.lower() if len(base.lower()) == 1 else ch.lower()[:1] or ch


def fold(text: str) -> str:
    """NFC, then lowercase without accents, one output char per input char (so match
    positions in the folded text are valid in ``unicodedata.normalize("NFC", text)``).
    "Cartão Não" -> "cartao nao"; "ñ" -> "n"; "¿" stays "¿"."""
    return "".join(_fold_char(ch) for ch in unicodedata.normalize("NFC", text or ""))
