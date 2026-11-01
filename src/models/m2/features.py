"""M2 text features: normalization, hashed character/word n-grams and a concept lexicon.
Standard library only.

The same functions build the training matrix (``models.m2.train``) and the inference vector
(``models.m2.model``), so training and serving features are identical by construction.

Pipeline for one message (already redacted by the orchestrator):

1. ``normalize``: cut to ``MAX_CHARS``; NFKC (fullwidth and compatibility letters become ASCII);
   common Cyrillic/Greek look-alikes become Latin letters; lowercase; accents removed; emoji and
   other symbols become spaces (zero-width characters are dropped); case numbers
   (``EV-7KQ2M9XA``) become the token ``evcaso``; characters spaced out to dodge filters
   ("i g n o r a", "1 g n 0 r 4") are joined; leetspeak words ("1gn0r4") are read as letters;
   every remaining digit becomes ``0`` (amounts and dates carry no intent); runs of 3+ equal
   characters are cut to 2. Obfuscation found on the way (fullwidth, look-alike letters,
   leetspeak, spaced letters) is returned as flags: customers rarely write like that, injections
   often do.
2. ``embed``: three blocks of hashed features, each with sublinear term frequency
   (``1 + ln count``) and L2 norm 1, added into one vector of ``N_BUCKETS`` dimensions:
   - characters: n-grams of 2 to 5 characters inside each token padded with spaces (like
     scikit-learn's ``char_wb``), where a token is a run of letters/digits or a run of punctuation;
   - words: unigrams of every token, bigrams of consecutive word tokens, and the flags;
   - concepts: names of the ``CONCEPTS`` whose patterns match (a hand-written multilingual
     lexicon: fee, unrecognized charge, loss, person, case, balance, injection, markup...).
   The hash is ``zlib.crc32`` of the UTF-8 feature string: low 15 bits = bucket, top bit = sign
   (signed hashing, so colliding features cancel instead of piling up).

``embed`` is the seam where a dense multilingual embedding (Bedrock Titan or Cohere multilingual)
would plug in: the classifier only needs ``embed(text) -> {index: value}`` and a weight row per
index. We had no embedding model available offline, so hashed character n-grams plus the concept
lexicon are the stand-in "embedding": language-agnostic, robust to typos and missing accents,
and computable in pure Python inside the chat Lambda.
"""

from __future__ import annotations

import math
import re
import unicodedata
import zlib

FEATURES_VERSION = "m2-hash-v1"
N_BUCKETS = 1 << 15
CHAR_NGRAM_MIN = 2
CHAR_NGRAM_MAX = 5
MAX_CHARS = 2000  # longer messages are cut before featurizing (bounds latency)

FLAG_NAMES = ("fullwidth", "confusable", "leet", "spaced")

# Cyrillic and Greek letters that look like Latin ones (subset of Unicode TR39 confusables).
_CONFUSABLE_MAP = {
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
    "ԛ": "q", "ԝ": "w", "һ": "h", "ӏ": "l",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
    "υ": "u", "χ": "x", "γ": "y",
    "Α": "A", "Β": "B", "Ε": "E", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P",
    "Τ": "T", "Υ": "Y", "Χ": "X", "Ζ": "Z",
}
_CONFUSABLES = str.maketrans(_CONFUSABLE_MAP)
_LATIN = re.compile(r"[A-Za-z]")

# EV-7KQ2M9XA / ev 7kq2m9xa / EV7KQ2M9XA (the last form only with at least one digit, so that
# "evidencias" is not a case number). Applied to lowercase text.
_CASE_NO = re.compile(r"(?<![a-z0-9])ev(?:\s?-\s?[a-z0-9]{8}|\s?(?=[a-z]{0,7}\d)[a-z0-9]{8})(?![a-z0-9])")
_ALNUM_RUN = re.compile(r"[a-z0-9]+")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g",
                       "@": "a", "$": "s"})
_LEET_WORD = re.compile(r"(?<![\w@$])[a-z0-9@$]*[a-z][0-9@$][a-z0-9@$]*(?![\w@$])")
# Four or more single characters separated by 1-3 spaces/dots/dashes: "i g n o r a",
# "i.g.n.o.r.a", "1 g n 0 r 4" (joined only when at least two of them are letters).
_SPACED = re.compile(r"(?<![\w@$])(?:[a-z0-9@$][\s.\-_*·]{1,3}){3,}[a-z0-9@$](?![\w@$])")
_SPACED_SEP = re.compile(r"[\s.\-_*·]+")
_DIGIT = re.compile(r"\d")
_REPEAT = re.compile(r"(.)\1{2,}")
_TOKEN = re.compile(r"\w+|[^\w\s]+")


def _is_leet(word: str) -> bool:
    """Letters and look-alike digits interleaved, like "1gn0r4" or "r3gl4s" (not "4x1000",
    "2do" or "24h"): at least 2 letters, at least 2 digits/symbols, a digit with letters on
    both sides somewhere."""
    letters = sum(c.isalpha() for c in word)
    others = len(word) - letters
    if letters < 2 or others < 2:
        return False
    return any(not word[i].isalpha() and word[i - 1].isalpha() and word[i + 1].isalpha()
               for i in range(1, len(word) - 1))


def normalize(text: str | None) -> tuple[str, tuple[str, ...]]:
    """(normalized text, obfuscation flags). Pure and deterministic; see the module docstring."""
    raw = (text or "")[:MAX_CHARS]
    flags: list[str] = []
    if any(0xFF01 <= ord(c) <= 0xFF5E for c in raw):
        flags.append("fullwidth")
    s = unicodedata.normalize("NFKC", raw)
    mapped = s.translate(_CONFUSABLES)
    if mapped != s:
        # Only a word that mixes look-alike letters with Latin ones is an obfuscation signal.
        for tok_orig, tok_mapped in zip(s.split(), mapped.split()):
            if tok_orig != tok_mapped and _LATIN.search(tok_orig):
                flags.append("confusable")
                break
    s = mapped.lower()
    s = unicodedata.normalize("NFKD", s)
    out = []
    for c in s:
        if c.isascii():
            out.append(c if c.isprintable() else " ")
            continue
        cat = unicodedata.category(c)
        if cat == "Mn" or cat == "Cf":  # accents (combining marks), zero-width characters
            continue
        out.append(c if cat[0] in "LN" else " ")
    s = "".join(out)
    s = _CASE_NO.sub(" evcaso ", s)

    def _join(m: re.Match) -> str:
        run = m.group(0)
        if sum(c.isalpha() for c in run) < 2:
            return run  # "3 1 5 2": spaced digits, not an obfuscated word
        if "spaced" not in flags:
            flags.append("spaced")
        return _SPACED_SEP.sub("", run)

    s = _SPACED.sub(_join, s)

    def _deleet(m: re.Match) -> str:
        w = m.group(0)
        if _is_leet(w):
            if "leet" not in flags:
                flags.append("leet")
            return w.translate(_LEET)
        return w

    s = _LEET_WORD.sub(_deleet, s)
    s = _DIGIT.sub("0", s)
    s = _REPEAT.sub(r"\1\1", s)
    s = " ".join(s.split())
    return s, tuple(flags)


def tokens(normalized: str) -> list[str]:
    """Runs of word characters, or runs of punctuation (``<``, ``{{``, ``]:``)."""
    return _TOKEN.findall(normalized)


# Concept lexicon: a small hand-written multilingual (ES-MX/CO/AR + PT) layer that maps many
# surface forms to one concept, so a synonym or a sentence shape never seen in training still
# lights a feature the model knows. It is domain knowledge, not a rule: the logistic regression
# learns how much each concept weighs for each class. Patterns run on the normalized text
# (lowercase, no accents, digits as 0). This is the cheap stand-in for the semantic
# similarity a multilingual embedding model would provide.
_W = r"(?<![a-z0-9_])"
_E = r"(?![a-z0-9_])"
# (name, pattern, use word boundaries). Digits are already "0" and runs of 3+ equal characters
# are cut to 2, so "4x1000" reads "0x00" and "10 días" reads "00 dias".
CONCEPTS: tuple[tuple[str, str, bool], ...] = (
    ("fee", r"comision\w*|comissao|comissoes|cuota de (?:manejo|administracion)|anualidad\w*|anuidade\w*|"
            r"tarifa\w*|taxas?|mantenimiento|mantencion|manutencao|cargo por (?:manejo|servicio|mantenimiento|"
            r"inactividad|sobregiro)|recargo\w*|multa\w*|penalizac\w*|membresia|cuatro por mil|0 por mil|0x00|gmf|"
            r"iva|intereses|juros|costo de (?:la cuenta|reposicion|manejo)|cobro indebido|cobranca indevida|"
            r"reposicion|segunda via", True),
    ("charge", r"cargo\w*|cobr\w*|debit\w*|descont\w*|compra\w*|consumo\w*|transacc\w*|transac\w*|"
               r"me pasaron la tarjeta|passaram (?:meu|o) cartao", True),
    ("unrecognized", r"no (?:lo |la )?reconozco|desconozco|desconoc\w*|no (?:lo |la )?hice|no fui yo|no autorice|"
                     r"nao reconhec\w*|nao (?:o |a )?fiz|nao fui eu|nao autorizei|desconhec\w*|no (?:es|son) mi[oa]s?|"
                     r"nao (?:e|sao) minh?as?|nao e meu|fraude\w*|clonar\w*|clonad\w*|hacke\w*|nunca (?:compre|"
                     r"comprei|hice|fiz|pedi)|no compre|nao comprei|raro|rara|rarisimo|estranh\w*|esquisit\w*|"
                     r"sospechos\w*|suspeit\w*|alguien (?:uso|usa)|alguem usou", True),
    ("duplicate", r"dos veces|0 veces|doble|duplicad\w*|duplicaron|duplicaram|duas vezes|em dobro|repetid\w*|"
                  r"dobrad\w*|las dos|as duas|tres veces|tres vezes|0 vezes", True),
    ("overcharge", r"de mas|a mais|mas de lo que|mais do que|cobraron mas|cobraram mais", True),
    ("refund_missing", r"(?:devolucion|reembolso|estorno|reintegro)\w* .{0,30}(?:nunca|no|nao)|"
                       r"(?:nunca|no|nao) (?:me )?(?:devolvieron|devolveram|reembolsaron|reintegraron|volto)", True),
    ("atm", r"cajero\w*|caixa eletronico|caixa automatico|atm", True),
    ("card", r"tarjeta\w*|cartao|cartoes|plastico", True),
    ("loss", r"perdi (?:mi |la |el |o |meu |minha |a |las |os |as )?(?:tarjeta\w*|cartao|cartoes|cartera|billetera|"
             r"carteira|celular|bolso|bolsa|mochila|documentos)|perdid[oa]s?|se me perdio|perda|extravi\w*|robaron|"
             r"robo|robad[oa]|hurt\w*|roub\w*|furt\w*|asalt\w*|assalt\w*|afanaron|me (?:la |lo )?sacaron|levaram|"
             r"no (?:la |lo )?encuentro|nao (?:o |a )?(?:acho|encontro)|se me cayo|se me quedo|se trago|engoliu|"
             r"tiraram", True),
    ("block", r"bloque\w*|congel\w*|desactivar la tarjeta|cancelar (?:la|mi) tarjeta", True),
    ("forgot", r"olvide|olvido|olvidado|esqueci", True),
    ("human", r"persona\w*|pessoa\w*|humano\w*|asesor\w*|agente\w*|ejecutiv\w*|operador\w*|atendente\w*|alguien|"
              r"alguem|supervisor\w*|gerente|carne y hueso|de verdad|real", True),
    ("talk", r"hablar|habla|comunic\w*|pas(?:ar|ame|eme|en|e) con|transfier\w*|transferi\w*|llamen|llamame|llameme|"
             r"me llame|me llamen|llamada|ligar|liguem|ligue|me liga|falar|fale|me atienda|atiendan|atenda|"
             r"conversar", True),
    ("bot", r"bot|robot|robo|maquina|automatic\w*|chatbot", True),
    ("case", r"caso|reclamo\w*|reclamac\w*|solicitud\w*|solicitac\w*|folio|radicado|protocolo|chamado|ticket|"
             r"disputa|contestac\w*|evcaso|numero de caso", True),
    ("status", r"estado(?! de cuenta)|status|estatus|avance|seguimiento|novedad\w*|novidade\w*|andamento|como va|"
               r"como esta|como ta|que paso|que aconteceu|respuesta|resposta|resolvi\w*|resolver\w*|resolveram|"
               r"revisaron|en que (?:va|quedo|estado)|em que pe|atualizac\w*|noticias|ya hay|ja tem|"
               r"cuando me responden|quando vao responder", True),
    ("waiting", r"dias habiles|dias uteis|plazo|prazo|hace 0+ dias|ha 0+ dias|faz 0+ dias|sigo esperando|"
                r"ainda (?:to|estou) esperando|ya paso|ja passou|ya pasaron|y nada|e nada", True),
    ("balance", r"saldo|cuanto tengo|quanto tenho|cuanta plata|cuanto dinero|quanto dinheiro|disponible|disponivel",
     True),
    ("statement", r"extracto|estado de cuenta|resumen|extrato|fatura|movimiento\w*|movimentac\w*", True),
    ("due", r"fecha de (?:corte|pago|cierre)|vence|vencimiento|vencimento|pago minimo|pagamento minimo|fechamento|"
            r"cierre de (?:la |mi )?tarjeta|cierra (?:mi|la) tarjeta|fecha limite|cuanto (?:debo|tengo que pagar)|"
            r"quanto (?:devo|tenho que pagar|falta)|fecha (?:mi|minha|a) fatura", True),
    ("limit", r"limite|cupo", True),
    ("arrived", r"se acredito|acreditaron|acreditad\w*|se reflejo|reflej\w*|ya entro|ya llego|caiu|ja entrou|"
                r"entrou|nomina|sueldo|salario|deposit\w*", True),
    ("app", r"app|aplicac\w*|aplicativo|web|pagina|sitio|site|token|codigo|clave|contrasena|senha|sesion|sessao|"
            r"login|actualizac\w*|atualizac\w*|se cierra|se cae|no carga|nao carrega|trava\w*|error|erro|tilda|"
            r"lent[oa]|caid[oa]|fecha sozinho|desloga|no me deja|nao (?:me )?deixa|no puedo entrar|"
            r"nao consigo entrar|en blanco|tela branca|pantalla", True),
    ("branch", r"sucursal\w*|agencia\w*|oficina|fila|ventanilla|guardia|seguranca|turno|horario", True),
    ("service", r"atencion|atendimento|servicio|servico|pesim\w*|grosero\w*|grosera|gross[oa]|maltrat\w*|trato|"
                r"call center|central|linea de atencion|linha de atendimento|nadie (?:contesta|atiende|responde)|"
                r"ninguem (?:atende|responde)|colgad[oa]|pendurad[oa]|mal atendid[oa]|horrible|horrivel|queja\w*|"
                r"queixa\w*", True),
    ("loan", r"prestamo\w*|credito (?:personal|hipotecario|de vivienda|de auto|para)|creditos|hipotec\w*|"
             r"emprestimo\w*|financiamento|financi\w*|refinanc\w*|consolidar|renegoci\w*|prestan|emprestam", True),
    ("invest", r"invert\w*|inversion\w*|plazo fijo|cdt|cdb|fondos?|renda fixa|acciones|cripto\w*|bitcoin|"
               r"investi\w*|comprar dolares|dolar blue|cotizacion", True),
    ("product", r"abrir (?:una |uma )?(?:cuenta|conta)|tarjeta nueva|nueva tarjeta|cartao novo|novo cartao|"
                r"aument\w* (?:el |o |mi |meu )?(?:limite|cupo)|sub\w* (?:el |mi )?(?:limite|cupo)|seguro de|"
                r"contratar (?:un |um )?seguro|beneficios|millas|milhas|requisitos", True),
    ("chitchat", r"clima|llov\w*|lluvia\w*|chov\w*|chuva\w*|temperatura|chiste|piada|receta|receita|partido|jogo|"
                 r"futbol|pelicula|peli|filme|poema|cancion|musica|"
                 r"horoscopo|tarea|como te llamas|seu nome|inflacion|quien gano|quem ganhou|"
                 r"(?:va a estar|vai estar) (?:el |o )?(?:tiempo|tempo)", True),
    ("dollar", r"dolar|dolares|usd", True),
    ("inj_override", r"ignor\w* (?:\w+ ){0,3}(?:instruc\w*|regla\w*|regra\w*|anterior\w*|previous|todo|tudo|rules|"
                     r"instructions)|olvid\w* (?:\w+ ){0,3}(?:regla\w*|instruc\w*|todo lo|lo anterior)|"
                     r"esquec\w* (?:\w+ ){0,3}(?:regra\w*|instruc\w*|tudo)|disregard|override|"
                     r"nuevas? (?:regla|reglas|instruccion\w*)|novas? (?:regra|regras|instruc\w*)|"
                     r"sin (?:restricciones|filtros|reglas)|sem (?:restric\w*|filtros|regras)|jailbreak|"
                     r"(?:modo|mode) dan|dan (?:mode|activado|ativado)|(?:eres|sos|seras) dan|"
                     r"modo (?:desarrollador|desenvolvedor|developer|administrador|dios|deus|depuracion)|"
                     r"developer mode|you are now|act as|pretend|fing\w* que|finja|juguemos|hagamos un juego|"
                     r"vamos brincar|role ?play|(?:ahora|agora) (?:eres|sos|vc e|voce e|tu eres)|"
                     r"a partir de (?:ahora|agora|este momento) (?:eres|sos|voce|vc|tu|no tienes|nao tem)", True),
    ("inj_reveal", r"prompt|instrucciones (?:internas|del sistema|que te|anteriores)|instrucoes (?:internas|"
                   r"do sistema|que te)|que instrucciones|quais instrucoes|configuracion interna|variables internas|"
                   r"repit\w* (?:todo|el texto|tus|lo que)|repet\w* (?:todo|o texto|suas)|traduc\w* tus instrucciones|"
                   r"revela\w*|reveal|palabra por palabra|palavra por palavra|reglas (?:internas|del sistema|que te)|"
                   r"regras (?:internas|do sistema)", True),
    ("inj_internal", r"fraud_?score|score de fraude|customer_?id|txn_?id|json (?:interno|con)|campos internos|"
                     r"datos internos|dados internos|base de datos|rules_version|variables internas|system prompt",
     True),
    ("inj_role", r"\[ ?(?:sistema|system|sys|admin|administrador|developer|asistente|assistant) ?\]|"
                 r"(?<![a-z])(?:sistema|system|admin|asistente|assistant) ?:|###|"
                 r"nota (?:para|pro|pra) (?:el |la |o |a )?(?:analista|revisor|modelo|ia|asistente|sistema|agente|bot)|"
                 r"<\|?(?:system|im_start|im_end)|admin override", False),
    ("inj_action", r"(?:aprueb\w*|aprob\w*|aprov\w*|autoriz\w*) (?:\w+ ){0,3}(?:reembolso|devolucion|estorno|todo|"
                   r"tudo|el caso|o caso|refund)|sin (?:verificar|revision|validar|investigar|revisar)|"
                   r"sem (?:verificar|validar|revisao|revisar)|salt\w* (?:la )?verificacion|pul\w* (?:a )?verificacao|"
                   r"omit\w* (?:la )?verificacion|(?:marca|marque|marcalo)\w* (?:\w+ ){0,3}como|"
                   r"(?:clasific|classifi|enrut|mand|envi)\w* (?:\w+ ){0,4}(?:ruta|carril|rota|lane|prioridad|"
                   r"prioridade)|cambia el monto|mude o valor|ya (?:fue |esta )?(?:aprobado|autorizado)|"
                   r"ja (?:foi )?(?:aprovado|autorizado)|como (?:ya )?(?:acordamos|quedamos|combinamos)|"
                   r"autorizo (?:el|o) (?:reembolso|estorno)|soy (?:el gerente|gerente|empleado del banco|"
                   r"funcionario del banco|del area de|auditor|el supervisor|del equipo de)|"
                   r"sou (?:o gerente|funcionario do banco|auditor|da equipe)|prueba autorizada|teste autorizado|"
                   r"approve the refund|a (?:mi|meu) favor", True),
    ("inj_other_customer", r"(?:cuenta|datos|movimientos|conta|dados|movimentac\w*|saldo) (?:\w+ ){0,2}"
                           r"(?:de|do|da) (?:mi |meu |minha |la |el )?(?:esposa|esposo|vecino|vizinho|otro cliente|"
                           r"outro cliente|papa|pai|madre|mae|ex|hermano|irmao|novia|novio|namorad\w*)", True),
    ("markup", r"<\s*/?\s*(?:script|img|iframe|svg|body|style|a |b )|onerror|onload|onmouseover|javascript ?:|"
               r"\{\{|\}\}|\$\{|drop table|union select|or 0+ ?= ?0+|<\|", False),
    ("decode", r"decodific\w*|decode|base00|decodifique", True),
)
_CONCEPTS = [(name, re.compile((_W + "(?:" + pat + ")" + _E) if bounded else pat))
             for name, pat, bounded in CONCEPTS]


def concepts(normalized: str) -> list[str]:
    """Names of the concepts whose patterns match the normalized text, in CONCEPTS order."""
    return [name for name, rx in _CONCEPTS if rx.search(normalized)]


def feature_strings(text: str | None) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """(character n-gram counts, word feature counts, concept counts) as {feature string:
    count}, in the order the features first appear (dict insertion order, so iteration is
    deterministic)."""
    norm, flags = normalize(text)
    toks = tokens(norm)
    chars: dict[str, int] = {}
    for tok in toks:
        padded = " " + tok + " "
        size = len(padded)
        for n in range(CHAR_NGRAM_MIN, CHAR_NGRAM_MAX + 1):
            for i in range(size - n + 1):
                key = "c" + padded[i:i + n]
                chars[key] = chars.get(key, 0) + 1
    words: dict[str, int] = {}
    prev = None
    for tok in toks:
        key = "w" + tok
        words[key] = words.get(key, 0) + 1
        if tok[0].isalnum() or tok[0] == "_":
            if prev is not None:
                key = "b" + prev + " " + tok
                words[key] = words.get(key, 0) + 1
            prev = tok
    for f in flags:
        words["f" + f] = 1
    kons = {"k" + name: 1 for name in concepts(norm)} if toks else {}
    return chars, words, kons


def bucket(feature: str) -> tuple[int, float]:
    """(bucket index, sign) of one feature string: crc32 of its UTF-8 bytes."""
    h = zlib.crc32(feature.encode("utf-8"))
    return h & (N_BUCKETS - 1), (1.0 if h & 0x80000000 else -1.0)


def embed(text: str | None) -> dict[int, float]:
    """Sparse feature vector {bucket: value} of one message (the "embedding" seam).

    Each block (characters, words, concepts) gets sublinear TF and its own L2 norm, then the
    blocks are added. An empty or symbol-only message gives an empty dict."""
    vec: dict[int, float] = {}
    for block in feature_strings(text):
        acc: dict[int, float] = {}
        for feat, count in block.items():
            b, sign = bucket(feat)
            acc[b] = acc.get(b, 0.0) + sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in acc.values()))
        if norm > 0.0:
            for b, v in acc.items():
                if v != 0.0:
                    vec[b] = vec.get(b, 0.0) + v / norm
    return vec


def readable(text: str | None) -> bool:
    """True if the normalized text has at least one letter or digit (an empty message, emoji
    or punctuation alone has nothing to classify)."""
    return bool(_ALNUM_RUN.search(normalize(text)[0]))


def dedup_key(text: str | None) -> str:
    """Key used to find duplicates and near-copies: the normalized text without punctuation."""
    norm, _ = normalize(text)
    return " ".join(_ALNUM_RUN.findall(norm))
