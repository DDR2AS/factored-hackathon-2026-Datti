"""Provisional M2 intent classifier (INTERFACES.md #4, owner Cristhian). Keywords, not a model.

Stand-in with the #4 signature until Cristhian's M2 arrives in ``src/models``. Each class
has weighted ES and PT patterns (matched on lowercase text without accents); the class
scores become a pseudo-probability ``p = score_top / (sum_of_scores + SMOOTHING)``. It is
NOT calibrated; the console must show ``model_version`` so nobody mistakes it for M2.

Manipulation (prompt injection, "ignore your instructions", "system prompt", pasted
markup) wins over everything else. The class never chooses the lane: it is evidence for
``lane_rules`` (D2).

Gate: accept when p >= ACCEPT_THRESHOLD, ask when p >= ASK_THRESHOLD, human below that;
with no keyword at all the gate is "ask" (there is nothing to be unsure about yet, the
orchestrator shows the start buttons). Cristhian's definitive M2 reads these thresholds
from ``config/thresholds.yaml``.
"""

from __future__ import annotations

import re
from typing import Any

from conversation.textnorm import fold

MODEL_VERSION = "m2-keywords-0"

CLASSES = (
    "dispute_charge", "dispute_fee", "complaint_other", "case_status", "account_query",
    "lost_card", "human_request", "out_of_scope", "manipulation",
)

# Gate thresholds (#4). The definitive M2 reads them from config/thresholds.yaml (Cristhian).
ACCEPT_THRESHOLD = 0.70
ASK_THRESHOLD = 0.40
SMOOTHING = 0.3

# (pattern on folded text, weight)
PATTERNS: dict[str, list[tuple[str, float]]] = {
    "manipulation": [
        (r"\bignor[ae]\w*\s+(?:\w+\s+){0,3}(?:instrucciones|instruccion|reglas|indicaciones|instrucoes|instrucao|regras|orientacoes|instructions|rules|prompt)\b", 5),
        (r"\bolvida\w*\s+(?:\w+\s+){0,3}(?:instrucciones|reglas)\b", 5),
        (r"\besquec\w*\s+(?:\w+\s+){0,3}(?:instrucoes|regras)\b", 5),
        (r"\b(?:system\s+prompt|prompt\s+(?:del|de)\s+sistema|prompt\s+do\s+sistema|developer\s+mode|modo\s+(?:desarrollador|desenvolvedor|dios|deus)|jailbreak|dan\s+mode)\b", 5),
        (r"\b(?:revela|muestra|mostra|muestrame|mostre|dime|diga)\s+(?:\w+\s+){0,2}(?:instrucciones|prompt|reglas\s+internas|instrucoes|regras\s+internas)\b", 5),
        (r"\b(?:ahora\s+eres|a\s+partir\s+de\s+ahora\s+eres|actua\s+como\s+(?:si|un|una)|finge\s+que|agora\s+voce\s+e|finja\s+que|aja\s+como)\b", 4),
        (r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be|disregard\s+(?:all|previous|the))\b", 4),
        (r"<\s*/?\s*(?:script|img|iframe|svg|style)\b|javascript:|onerror\s*=|\{\{.*\}\}", 5),
        (r"\b(?:aprueba|aprobar|autoriza|haz|hace|faz|aprove)\s+(?:\w+\s+){0,2}(?:reembolso|reembolsos|devolucion|estorno)\s+(?:ya|ahora|automatic\w*|agora)\b", 4),
        # Injections without the classic phrases: fake system notes, new rules, orders to the
        # model or the analyst, requests for internal fields.
        (r"[\[<(]\s*(?:sistema|system|sys|admin|administrador|developer|desarrollador|desenvolvedor|assistant|asistente|assistente)\s*[\]>)]", 5),
        (r"(?:^|[.!?\n]\s*)(?:sistema|system|admin|instruccion(?:es)?\s+del\s+sistema|instrucao\s+do\s+sistema)\s*:", 5),
        (r"\b(?:nueva|nuevas|nova|novas)\s+(?:regla|reglas|regra|regras|instruccion|instrucciones|instrucao|instrucoes|politica|politicas)\b", 5),
        (r"\bnota\s+(?:para|pro|pra)\s+(?:el|la|los|o|a|os)?\s*(?:modelo|analista|ia|asistente|assistente|bot|sistema|agente|revisor)\b", 5),
        (r"\b(?:clasifica|clasificalo|clasificar|classifique|classifica|marca|marcalo|enruta|manda|envia)\w*\s+(?:\w+\s+){0,3}(?:como|en|na|para)\s+(?:la\s+|a\s+)?(?:ruta|rota|carril|lane)\s+[abc]\b", 5),
        (r"\b(?:responde|responda|responder|devuelve|devolva|retorna|retorne|muestra|mostre|mostra|revela|dame|me\s+da|me\s+de|imprime|imprima)\s+(?:\w+\s+){0,4}(?:fraud_score|fraud\s+score|score\s+de\s+fraude|customer_id|txn_id|id\s+del\s+cliente|id\s+do\s+cliente|rules_version)\b", 5),
        (r"\b(?:el\s+cliente|o\s+cliente)\s+(?:ya\s+|ja\s+)?(?:fue|foi|esta|está)\s+(?:verificad[oa]|autenticad[oa]|aprobad[oa]|aprovad[oa])\b.{0,80}\b(?:aprueba|aprove|autoriza|clasifica|no\s+pidas|nao\s+peca)\b", 5),
    ],
    "human_request": [
        (r"\b(?:hablar|comunicarme|comunicame|comunicas|comunican|comunicar|pasarme|pasame|pasas|pasan|me\s+pasas|me\s+pasan)\s+con\s+(?:una?\s+|el\s+|la\s+|algun\s+|alguna\s+)?(?:persona|humano|asesor|asesora|agente|ejecutivo|ejecutiva|operador|operadora|alguien)\b", 4),
        # Short answers to "¿revisar los datos o hablar con una persona?": "con una persona",
        # "sim, uma pessoa", "un asesor", "humano por favor", "atendente".
        (r"^\W*(?:(?:si|sim|ok|claro|bueno|vale|dale|mejor|prefiero|quiero|quero|prefiro)\W+)*(?:con\s+|com\s+)?(?:una?\s+|um\s+|uma\s+|el\s+|la\s+|o\s+|a\s+|algun\s+|alguna\s+|algum\s+)?(?:persona|pessoa|humano|humana|asesor|asesora|atendente|agente|ejecutivo|ejecutiva|operador|operadora|alguien|alguem)(?:\s+(?:real|de\s+verdade|humana|humano))?(?:\W+(?:por\s+favor|porfa|pf|pls|please|obrigad[oa]|gracias))?\W*$", 4),
        (r"\b(?:quiero|necesito|prefiero)\s+(?:una?\s+)?(?:persona|humano|asesor|asesora|agente)\b", 4),
        (r"\b(?:persona\s+real|ser\s+humano|pessoa\s+de\s+verdade|atendente\s+humano)\b", 4),
        (r"\b(?:falar|conversar)\s+com\s+(?:uma?\s+|o\s+|a\s+|algum\s+)?(?:pessoa|humano|atendente|alguem|agente|gerente)\b", 4),
        (r"\b(?:quero|preciso\s+de)\s+(?:uma?\s+)?(?:pessoa|atendente|humano)\b", 4),
    ],
    "lost_card": [
        (r"\b(?:perdi|extravie|se\s+me\s+perdio|me\s+robaron|robaron|me\s+hurtaron|roubaram|furtaram)\b.{0,30}\b(?:tarjeta|cartera|billetera|cartao|carteira)\b", 5),
        (r"\b(?:tarjeta|cartao)\s+(?:\w+\s+)?(?:perdida|robada|extraviada|hurtada|perdido|roubado|furtado|extraviado)\b", 5),
        (r"\b(?:robo|hurto|roubo|furto|perdida|perda)\s+de\s+(?:mi\s+|la\s+|meu\s+|do\s+)?(?:tarjeta|cartao)\b", 5),
        (r"\bbloque(?:ar|a|as|en|e|es|ia|iem)\s+(?:mi|la|o|meu|minha)\s+(?:tarjeta|cartao)\b", 4),
    ],
    "dispute_fee": [
        (r"\b(?:comision|comisiones|comissao|comissoes)\b", 2),
        (r"\b(?:cuota\s+de\s+manejo|anualidad|anuidade)\b", 2),
        (r"\b(?:mantenimiento|manutencao)\b", 2),
        (r"\b(?:tarifa|tarifas|taxa|taxas)\b", 2),
        (r"\b(?:cobro\s+indebido|cobranca\s+indevida|cargo\s+por\s+(?:manejo|mantenimiento|servicio))\b", 2),
    ],
    "dispute_charge": [
        (r"\b(?:no\s+(?:lo\s+|la\s+)?reconozco|desconozco|no\s+(?:la\s+|lo\s+)?hice|no\s+fui\s+yo|no\s+autorice|nao\s+reconheco|nao\s+fui\s+eu|nao\s+fiz|nao\s+autorizei|desconheco)\b", 3),
        (r"\b(?:me\s+)?(?:cobraron|cobro|cobraram|cobrou|cargaron|descontaron|debitaron|descontaram|debitaram)\b", 1),
        (r"\b(?:cargo|cobro|compra|transaccion|movimiento|transacao|compra)\s+(?:que\s+)?(?:no|nao|desconocid[oa]|estranh[oa]|rar[oa]|extran[oa])\b", 2),
        (r"\b(?:dos\s+veces|doble\s+cobro|cobro\s+doble|duplicad[oa]|duas\s+vezes|em\s+dobro|fraude|clonaron|clonada)\b", 2),
    ],
    "complaint_other": [
        (r"\b(?:app|aplicacion|aplicativo|no\s+me\s+deja\s+(?:entrar|ingresar)|se\s+cierra|se\s+cae|no\s+carga|nao\s+abre|nao\s+consigo\s+entrar|travou)\b", 2),
        (r"\b(?:sucursal|agencia|oficina|cajero\s+automatico|caixa\s+eletronico)\b", 2),
        (r"\b(?:atencion|mal\s+servicio|me\s+atendieron|atendimento|me\s+atenderam|grosero|grosera|grosseir[oa]|maltrat\w*|queja|reclamo|reclamacao)\b", 2),
    ],
    "case_status": [
        (r"\b(?:estado|status|avance|seguimiento|andamento)\s+(?:de\s+|do\s+)?(?:mi\s+|meu\s+)?(?:caso|reclamo|queja|solicitud|protocolo|pedido)\b", 4),
        (r"\b(?:como\s+va|que\s+paso\s+con|como\s+esta)\s+(?:mi\s+)?(?:caso|reclamo|queja|solicitud)\b", 4),
        (r"\b(?:numero\s+de\s+caso|mi\s+caso|meu\s+caso|ev-[a-z0-9]{4,})\b", 3),
    ],
    "account_query": [
        (r"\b(?:saldo|cuanto\s+tengo|cuanto\s+debo|extracto|estado\s+de\s+cuenta|movimientos|limite|fecha\s+de\s+corte|pago\s+minimo|extrato|fatura|quanto\s+tenho|quanto\s+devo)\b", 3),
    ],
    "out_of_scope": [
        (r"\b(?:prestamo|prestamos|credito\s+(?:hipotecario|personal|de\s+consumo)|hipoteca|invertir|inversion|inversiones|abrir\s+(?:una\s+)?cuenta|seguro\s+de|cdt|plazo\s+fijo|cripto\w*|acciones)\b", 3),
        (r"\b(?:emprestimo|emprestimos|financiamento|investir|investimento|abrir\s+(?:uma\s+)?conta|seguro\s+de|consorcio|cripto\w*)\b", 3),
        (r"\b(?:clima|chiste|receta|futbol|piada|receita|horoscopo|poema|cancion|musica)\b", 3),
    ],
}

_COMPILED = {k: [(re.compile(p), w) for p, w in v] for k, v in PATTERNS.items()}


def scores(text: str) -> dict[str, float]:
    """Raw keyword score per class (0 if nothing matched). Pure; for tests and traces."""
    f = fold(text)
    return {cls: sum(w for rx, w in _COMPILED.get(cls, []) if rx.search(f)) for cls in CLASSES}


def gate_for(p: float, matched: bool = True) -> str:
    """accept | ask | human for a top probability (no keyword matched -> ask)."""
    if not matched:
        return "ask"
    if p >= ACCEPT_THRESHOLD:
        return "accept"
    if p >= ASK_THRESHOLD:
        return "ask"
    return "human"


def classify(text: str, language: str | None = None) -> dict[str, Any]:
    """Intent of one customer message.

    text: the message (redacted or not; the patterns ignore documents and phones).
    language: "es", "pt" or "mixed"; accepted for the #4 signature. Both pattern sets are
        always applied, because customers mix languages.
    Returns ``{"intent_class", "p", "gate_action", "runner_up", "model_version"}`` where
    ``runner_up`` is the second class name or None, and p is rounded to 3 decimals.
    Manipulation wins over any other class with p >= 0.9. With no keyword at all:
    intent_class "complaint_other", p = 1/9, gate "ask". Pure, never raises.
    """
    s = scores(text)
    ranked = sorted(((v, k) for k, v in s.items() if v > 0), key=lambda x: (-x[0], CLASSES.index(x[1])))
    if not ranked:
        return {"intent_class": "complaint_other", "p": round(1 / len(CLASSES), 3), "gate_action": "ask",
                "runner_up": None, "model_version": MODEL_VERSION}
    total = sum(v for v, _ in ranked)
    if s["manipulation"] > 0:
        runner = next((k for _, k in ranked if k != "manipulation"), None)
        p = max(0.9, s["manipulation"] / (total + SMOOTHING))
        return {"intent_class": "manipulation", "p": round(p, 3), "gate_action": "accept",
                "runner_up": runner, "model_version": MODEL_VERSION}
    top_score, top = ranked[0]
    p = top_score / (total + SMOOTHING)
    return {
        "intent_class": top,
        "p": round(p, 3),
        "gate_action": gate_for(p),
        "runner_up": ranked[1][1] if len(ranked) > 1 else None,
        "model_version": MODEL_VERSION,
    }
