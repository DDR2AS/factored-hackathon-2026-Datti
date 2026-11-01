"""Static checks on the front end (frontend/src), enforced from pytest so CI catches them
without Node.

- No HTML sinks or dynamic code: dangerouslySetInnerHTML, innerHTML/outerHTML,
  insertAdjacentHTML, document.write, eval(, new Function.
- No persistent browser storage (the session token lives in memory + sessionStorage).
- No external http(s) URLs (allow list empty for now; the analyst console will add the
  Cognito endpoint of the region).
- es.core.ts + es.ts and pt.core.ts + pt.ts have exactly the same keys, part by part.
- No UI string promises a refund or compensation, in Spanish or Portuguese.
- index.html carries the agreed CSP.
- Analyst console (29-30 sep): no cookies (the analyst token lives in memory + sessionStorage,
  like the customer's), links that open a new tab carry rel="noopener", the reply approved in
  the console is labeled as approved by a person in ES and PT, and the console's touch targets
  are at least 44 px on mobile.
- RNF-11 (first page < 250 KB): what main.tsx imports statically (the first bundle) never reaches
  the views, their styles, the API client or the dictionaries other than es.core.ts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
I18N = SRC / "i18n"

SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".html"}

# Allowed external URLs (exact prefixes). Empty on purpose until the console needs Cognito.
ALLOWED_URLS: tuple[str, ...] = ()

FORBIDDEN_PATTERNS = {
    "dangerouslySetInnerHTML": re.compile(r"dangerouslySetInnerHTML"),
    "innerHTML": re.compile(r"\binnerHTML\b"),
    "outerHTML": re.compile(r"\bouterHTML\b"),
    "insertAdjacentHTML": re.compile(r"insertAdjacentHTML"),
    "document.write": re.compile(r"document\.write"),
    "eval(": re.compile(r"(?<![\w.])eval\s*\("),
    "new Function": re.compile(r"\bnew\s+Function\b"),
    "localStorage": re.compile(r"localStorage"),
    "document.cookie": re.compile(r"document\.cookie"),
}

URL_RE = re.compile(r"""https?://[^\s'"`<>)\]]+""", re.IGNORECASE)

# Promises of refund or compensation. The system never promises them (only an analyst decides).
REFUND_PROMISES = [
    # Spanish
    r"\bte\s+(reembolsaremos|reembolsamos|devolveremos|compensaremos|abonaremos)\b",
    r"\b(vamos|voy)\s+a\s+(reembolsar|devolver|compensar|abonar)(te)?\b",
    r"\bte\s+vamos\s+a\s+(reembolsar|devolver|compensar|abonar)\b",
    r"\b(reembolso|devoluci[oó]n|compensaci[oó]n)\s+garantizad[oa]\b",
    r"\brecibir[aá]s\s+(un|tu|el|una|la)\s+(reembolso|devoluci[oó]n|compensaci[oó]n)\b",
    r"\bse\s+te\s+(reembolsar[aá]|devolver[aá]|compensar[aá])\b",
    # Portuguese
    r"\b(reembolsaremos|devolveremos|compensaremos|estornaremos|ressarciremos)\b",
    r"\bvamos\s+(te\s+)?(reembolsar|devolver|compensar|estornar|ressarcir)\b",
    r"\b(reembolso|estorno|compensa[cç][aã]o|ressarcimento)\s+garantid[oa]\b",
    r"\bvoc[eê]\s+(ser[aá]\s+reembolsad[oa]|receber[aá]\s+(o|um|seu|a|uma|sua)\s+(reembolso|estorno|compensa[cç][aã]o))\b",
]
REFUND_RES = [re.compile(p, re.IGNORECASE) for p in REFUND_PROMISES]

EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
)


def _allowed(url: str) -> bool:
    return any(url.startswith(prefix) for prefix in ALLOWED_URLS)


def _source_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*") if p.is_file() and p.suffix in SOURCE_SUFFIXES)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# Each language is split in two files (RNF-11): <lang>.core.ts (first screen, in the first bundle
# for es) and <lang>.ts (the views, loaded on demand). Checks read both.
I18N_PARTS = ("core", "views")


def _i18n_file(lang: str, part: str) -> Path:
    return I18N / (f"{lang}.core.ts" if part == "core" else f"{lang}.ts")


def _i18n_text(lang: str) -> str:
    return "\n".join(_read(_i18n_file(lang, part)) for part in I18N_PARTS)


def _i18n_keys(text: str) -> list[str]:
    return re.findall(r"^\s*'([A-Za-z0-9_.]+)'\s*:", text, flags=re.MULTILINE)


def _i18n_values(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r":\s*\n?\s*'((?:[^'\\]|\\.)*)'", text)]


pytestmark = pytest.mark.skipif(not SRC.is_dir(), reason="frontend/src not present")


def test_there_are_sources_to_check():
    files = _source_files()
    assert len(files) >= 10, f"expected the React sources under {SRC}, found {len(files)} files"


@pytest.mark.parametrize("name", sorted(FORBIDDEN_PATTERNS))
def test_forbidden_constructs_absent(name: str):
    pattern = FORBIDDEN_PATTERNS[name]
    hits = []
    for p in _source_files():
        for n, line in enumerate(_read(p).splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, f"{name} is forbidden in frontend/src:\n" + "\n".join(hits)


def test_no_external_urls():
    hits = []
    for p in [*_source_files(), FRONTEND / "index.html"]:
        for n, line in enumerate(_read(p).splitlines(), 1):
            for url in URL_RE.findall(line):
                if not _allowed(url):
                    hits.append(f"{p.relative_to(ROOT)}:{n}: {url}")
    assert not hits, "external URLs are not allowed (only relative /api paths):\n" + "\n".join(hits)


@pytest.mark.parametrize("part", I18N_PARTS)
def test_i18n_same_keys_per_part(part: str):
    es = _i18n_keys(_read(_i18n_file("es", part)))
    pt = _i18n_keys(_read(_i18n_file("pt", part)))
    assert len(es) > 40, f"{part} keys not found in es; did the dictionary format change?"
    assert set(es) == set(pt), {
        "missing_in_pt": sorted(set(es) - set(pt)),
        "missing_in_es": sorted(set(pt) - set(es)),
    }


def test_i18n_same_keys():
    es = _i18n_keys(_i18n_text("es"))
    pt = _i18n_keys(_i18n_text("pt"))
    assert len(es) > 50, "es keys not found; did the dictionary format change?"
    assert len(es) == len(set(es)), "duplicate keys in es.ts"
    assert len(pt) == len(set(pt)), "duplicate keys in pt.ts"
    assert set(es) == set(pt), {
        "missing_in_pt": sorted(set(es) - set(pt)),
        "missing_in_es": sorted(set(pt) - set(es)),
    }


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_no_refund_or_compensation_promises(lang: str):
    values = _i18n_values(_i18n_text(lang))
    assert len(values) > 50
    hits = [v for v in values for rx in REFUND_RES if rx.search(v)]
    assert not hits, f"{lang}.ts promises a refund or compensation: {hits}"


def test_no_refund_promises_anywhere_in_src():
    hits = []
    for p in _source_files():
        text = _read(p)
        for rx in REFUND_RES:
            for m in rx.finditer(text):
                hits.append(f"{p.relative_to(ROOT)}: {m.group(0)}")
    assert not hits, "\n".join(hits)


@pytest.mark.parametrize(
    "phrase",
    [
        "Te reembolsaremos el cargo mañana",
        "Vamos a devolverte el dinero",
        "Reembolso garantizado en 48 horas",
        "Recibirás un reembolso",
        "Vamos reembolsar o valor",
        "Você receberá o reembolso",
        "Estorno garantido",
        "Devolveremos seu dinheiro",
    ],
)
def test_refund_patterns_catch_known_promises(phrase: str):
    assert any(rx.search(phrase) for rx in REFUND_RES), phrase


@pytest.mark.parametrize(
    "phrase",
    [
        "Nada mueve dinero y el sistema no promete devoluciones ni compensaciones.",
        "Pediste una compensación; eso solo lo evalúa una persona.",
        "o sistema não promete devoluções nem compensações",
    ],
)
def test_refund_patterns_allow_disclaimers(phrase: str):
    assert not any(rx.search(phrase) for rx in REFUND_RES), phrase


def test_index_html_has_csp():
    html = _read(FRONTEND / "index.html")
    m = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html)
    assert m, "CSP meta tag missing in frontend/index.html"
    assert m.group(1) == EXPECTED_CSP


def test_build_marker_present():
    marker = FRONTEND / "public" / "build.txt"
    assert marker.is_file(), "frontend/public/build.txt identifies the published version"
    assert marker.read_text(encoding="utf-8").strip()


def _media_block(css: str, query: str) -> str:
    """Body of the first `@media <query> { ... }` block (brace-matched)."""
    start = css.find(f"@media {query}")
    assert start >= 0, f"@media {query} not found in app.css"
    i = css.index("{", start)
    depth = 0
    for j in range(i, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[i + 1 : j]
    raise AssertionError("unbalanced braces in app.css")


def _rules(block: str) -> dict[str, str]:
    """selector -> declarations, one entry per selector of a comma list."""
    out: dict[str, str] = {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
        for sel in m.group(1).split(","):
            sel = re.sub(r"/\*.*?\*/", "", sel, flags=re.S).strip()
            out[sel] = out.get(sel, "") + m.group(2)
    return out


def _mobile_rules_after_base(css: str, query: str = "(max-width: 1023px)") -> dict[str, list[str]]:
    """selector -> declarations of the `@media <query>` rules that come AFTER the selector's last
    rule outside any @media. A mobile rule placed before the base rule loses the cascade (same
    specificity): found on phones on 30 sep, .tabs__tab and .cite stayed 36 and 28 px tall."""
    text = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), css, flags=re.S)
    spans = []  # (start, end, query) of every top-level @media block
    for m in re.finditer(r"@media\s*([^{]+)\{", text):
        if any(a <= m.start() < b for a, b, _ in spans):
            continue
        depth = 0
        for j in range(m.end() - 1, len(text)):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                spans.append((m.start(), j, m.group(1).strip()))
                break
    base_at: dict[str, int] = {}
    mobile: list[tuple[int, str, str]] = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        inside = next((q for a, b, q in spans if a <= m.start() < b), None)
        for sel in m.group(1).split(","):
            sel = sel.strip()
            if sel.startswith("@media"):
                sel = sel.split("{")[-1].strip()
            if inside is None:
                base_at[sel] = m.start()
            elif inside == query:
                mobile.append((m.start(), sel, m.group(2)))
    out: dict[str, list[str]] = {}
    for at, sel, decls in mobile:
        if at > base_at.get(sel, -1):
            out.setdefault(sel, []).append(decls)
    return out


def _px(decls: str, prop: str) -> int | None:
    m = re.search(rf"(?<![\w-]){prop}\s*:\s*(\d+)px", decls)
    return int(m.group(1)) if m else None


@pytest.mark.parametrize(
    "selector, props",
    [
        (".composer__send", (("width", "min-width"), ("height", "min-height"))),
        (".brand", (("min-width", "width"), ("min-height", "height"))),
        (".btn--small", (("min-width", "width"),)),
        (".btn--icon", (("width", "min-width"), ("min-height", "height"))),
        (".chip", (("min-height", "height"),)),
        (".later__item", (("min-height", "height"),)),
        (".switch__label", (("min-height", "height"),)),
        (".trace__full", (("min-height", "height"),)),
    ],
)
def test_mobile_touch_targets_are_44px(selector: str, props):
    """RNF-08 (FE-08): touch targets of at least 44 px on mobile (max-width: 1023px). The chat's
    controls live in views.css (loaded with the views, RNF-11); page chrome in app.css."""
    # views.css loads after app.css: read them in that order, as the cascade does.
    css = _read(SRC / "styles" / "app.css") + "\n" + _read(SRC / "styles" / "views.css")
    rules = {sel: "".join(d) for sel, d in _mobile_rules_after_base(css).items()}
    assert selector in rules, f"{selector} has no rule in the mobile media query after its base rule"
    for alternatives in props:
        values = [v for v in (_px(rules[selector], p) for p in alternatives) if v is not None]
        assert values and max(values) >= 44, f"{selector}: {alternatives} must be >= 44px on mobile"


def test_new_tab_links_use_noopener():
    """A link that opens another tab (judge -> console) never hands it window.opener."""
    hits = []
    for p in _source_files():
        if p.suffix not in {".tsx", ".jsx"}:
            continue
        text = _read(p)
        for m in re.finditer(r"<a\s[^>]*?target=\"_blank\"[^>]*?>", text, flags=re.S):
            if "noopener" not in m.group(0):
                hits.append(f"{p.relative_to(ROOT)}: {m.group(0)[:80]}")
    assert not hits, "target=_blank without rel=noopener: " + "; ".join(hits)


def test_analyst_token_lives_in_session_storage_only():
    auth = _read(SRC / "console" / "auth.ts")
    assert "sessionStorage" in auth
    # The token never travels in a URL (only case_id or trace_id may appear there).
    for p in _source_files():
        text = _read(p)
        assert not re.search(r"[?&](token|access_token|id_token)=", text), p


@pytest.mark.parametrize(
    "lang, needle",
    [("es", "aprobado por un analista"), ("pt", "aprovado por um analista")],
)
def test_resolution_badge_names_the_human(lang: str, needle: str):
    text = _i18n_text(lang)
    m = re.search(r"'chat\.resolutionBadge':\s*'([^']*)'", text)
    assert m and needle in m.group(1), f"{lang}.ts chat.resolutionBadge must say {needle!r}"


@pytest.mark.parametrize("selector", [".rail__item", ".tabs__tab", ".cite", ".qitem", ".convo__summary"])
def test_console_touch_targets_are_44px(selector: str):
    """RNF-08 for the console: touch targets of at least 44 px on mobile (max-width: 1023px),
    in a mobile rule that comes after the selector's base rule (or the base rule would win)."""
    css = _read(SRC / "styles" / "console.css")
    rules = {sel: "".join(d) for sel, d in _mobile_rules_after_base(css).items()}
    assert selector in rules, f"{selector} has no rule in the console's mobile media query after its base rule"
    values = [v for v in (_px(rules[selector], p) for p in ("min-height", "height")) if v is not None]
    assert values and max(values) >= 44, f"{selector} must be >= 44px tall on mobile"


def test_every_queue_id_the_backend_emits_has_a_label_in_es_and_pt():
    # Integration 29 sep: the console showed the raw id "complaints_es" in a lane C handoff.
    import ast

    ids: set[str] = set()
    orch = (ROOT / "src" / "conversation" / "orchestrator.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(orch)) if isinstance(n, ast.FunctionDef) and n.name == "_queue_for")
    ids |= {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.fullmatch(r"[a-z_]+", n.value) and n.value not in ("fraud_score",)}
    ids -= {"high_fraud_score", "regulator_or_legal", "lost_card", "no_progress", "pt"}  # rule ids, language
    analyst = (ROOT / "src" / "conversation" / "analyst.py").read_text(encoding="utf-8")
    ids |= set(re.findall(r'"(complaints_[a-z]+)"', analyst))
    ids |= set(re.search(r"_B_QUEUE = \{([^}]*)\}", analyst).group(1).replace('"', "").replace(" ", "")
               .replace(",", ":").split(":")[1::2])
    lifecycle = (ROOT / "src" / "conversation" / "lifecycle.py").read_text(encoding="utf-8")
    ids.add(re.search(r'SENIOR_QUEUE = "([a-z_]+)"', lifecycle).group(1))
    assert {"fraud", "regulator", "cards", "general", "complaints_es", "complaints_pt", "disputes", "fees",
            "senior"} <= ids
    for lang in ("es", "pt"):
        text = _i18n_text(lang)
        missing = sorted(q for q in ids if f"'queue.{q}':" not in text)
        assert not missing, f"{lang}.ts lacks queue labels: {missing}"


# ---------------------------------------------------------------- review round 30 sep (front)

def test_console_copy_of_the_customer_texts_matches_the_templates():
    # CX-02: the console shows the analyst the exact text before "Confirmar y enviar". Its copy
    # of request_information_default (per register) and resolution_stamp must not drift.
    from conversation.templates import render

    ts = _read(SRC / "console" / "customerTexts.ts")
    for lang in ("es", "pt"):
        assert f"'{render('resolution_stamp', lang)}'" in ts, f"resolution_stamp ({lang}) drifted"
    for country in ("MX", "CO", "AR"):
        text = render("request_information_default", "es", country, case_id="{case_id}")
        assert f"'{text}'" in ts, f"request_information_default (es, {country}) drifted"
    assert f"'{render('request_information_default', 'pt', 'BR', case_id='{case_id}')}'" in ts
    assert "{MX: tu, CO: usted, AR: vos}" in (ROOT / "src" / "conversation" / "templates" / "es.yaml").read_text(
        encoding="utf-8"
    ), "the register table changed: update REGISTER_BY_COUNTRY in customerTexts.ts"
    assert "{ MX: 'tu', CO: 'usted', AR: 'vos' }" in ts


def _token(css: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}:\s*([^;]+);", css)
    assert m, f"{name} not found"
    return m.group(1).strip().lower()


def test_approved_by_a_person_has_its_own_color():
    # CX-13: "Respuesta enviada" and the human bubble used the lane C pink, close to danger.
    css = _read(SRC / "styles" / "tokens.css")
    dark_at = css.index("prefers-color-scheme: dark")
    for block in (css[:dark_at], css[dark_at:]):
        human = {_token(block, "--human-fg"), _token(block, "--human-bg")}
        for other in ("--lane-c-fg", "--lane-c-bg", "--danger-fg", "--danger-bg"):
            assert _token(block, other) not in human, f"--human-* reuses {other}"


def test_case_header_is_sticky_and_long_words_break_only_in_codes():
    # CX-14: the case header (name, lane, SLA, jump to the decision) stays on screen.
    # CX-17 (7): merchant names break at word boundaries; only .mono codes break anywhere.
    console = re.sub(r"/\*.*?\*/", "", _read(SRC / "styles" / "console.css"), flags=re.S)
    head = _rules(console).get(".case-head", "")
    assert re.search(r"position:\s*sticky", head), ".case-head must be sticky"
    views = re.sub(r"/\*.*?\*/", "", _read(SRC / "styles" / "views.css"), flags=re.S)
    facts_dd = _rules(views).get(".facts dd", "")
    assert facts_dd, ".facts dd not found in views.css"
    assert "overflow-wrap: anywhere" not in facts_dd, ".facts dd must not cut words anywhere"


# ---------------------------------------------------------------- RNF-11 first bundle (30 sep)

_IMPORT_RE = re.compile(
    r"^[ \t]*(?:import|export)[ \t]+(?!type\b)([^;'\n]*?(?:\n[^;'\n]*?)*?)[ \t]+from[ \t]+'([^']+)'"
    r"|^[ \t]*import[ \t]+'([^']+)'",
    flags=re.MULTILINE,
)


def _static_imports(p: Path) -> list[Path]:
    """Relative modules `p` imports at runtime (not `import type`, not dynamic import())."""
    out = []
    for m in _IMPORT_RE.finditer(_read(p)):
        clause, spec = m.group(1), m.group(2) or m.group(3)
        if clause is not None and not re.search(r"[A-Za-z_$*]", re.sub(r"\btype\s+\w+", "", clause.strip("{} \n"))):
            continue  # import { type A, type B } from '...': types only
        if not spec.startswith("."):
            continue
        base = (p.parent / spec).resolve()
        cands = [base, *(base.with_name(base.name + ext) for ext in (".ts", ".tsx")), base / "index.ts"]
        found = next((c for c in cands if c.is_file()), None)
        assert found, f"{p.relative_to(ROOT)} imports {spec!r}, not found"
        out.append(found)
    return out


def _first_bundle() -> set[Path]:
    seen: set[Path] = set()
    todo = [SRC / "main.tsx"]
    while todo:
        p = todo.pop().resolve()
        if p in seen:
            continue
        seen.add(p)
        if p.suffix in {".ts", ".tsx"}:
            todo.extend(_static_imports(p))
    return seen


def test_first_bundle_leaves_views_client_and_other_dictionaries_out():
    bundle = {p.relative_to(SRC.resolve()).as_posix() for p in _first_bundle()}
    assert {"App.tsx", "views/JudgeLanding.tsx", "i18n/es.core.ts", "styles/app.css"} <= bundle, sorted(bundle)
    must_be_lazy = {
        "i18n/es.ts", "i18n/pt.ts", "i18n/pt.core.ts",
        "api/client.ts",
        "components/CaseCard.tsx", "components/TraceStrip.tsx",
        "views/ChatView.tsx", "views/ConsoleView.tsx", "views/TraceView.tsx",
        "styles/views.css", "styles/console.css",
    }
    leaked = sorted(bundle & must_be_lazy)
    assert not leaked, f"the first bundle (RNF-11) statically imports {leaked}"


# ---------------------------------------------------------------- SLA timers (front, 1 oct)

@pytest.mark.parametrize(
    "lang, badge, ratio",
    [("es", "Regla · aviso automático", "1 día = {t}"), ("pt", "Regra · aviso automático", "1 dia = {t}")],
)
def test_sla_notice_badge_and_fast_clock_label(lang: str, badge: str, ratio: str):
    # The clock's proactive notices are sent by a rule (not a person, not a model), and the
    # judge's switch says the demo scale in its name: the one the server reports (GET /health
    # demo_clock_scale, 29 sep), never a fixed "1 minuto"; without it, no figure at all.
    text = _i18n_text(lang)
    m = re.search(r"'chat\.noticeBadge':\s*'([^']*)'", text)
    assert m and m.group(1) == badge, f"{lang}: chat.noticeBadge must be {badge!r}"
    m = re.search(r"'judge\.switch\.fast_clock':\s*'([^']*)'", text)
    assert m and "{ratio}" in m.group(1) and "minuto" not in m.group(1), f"{lang}: judge.switch.fast_clock"
    m = re.search(r"'judge\.clock\.ratio':\s*'([^']*)'", text)
    assert m and m.group(1) == ratio, f"{lang}: judge.clock.ratio must be {ratio!r}"
    m = re.search(r"'judge\.switch\.fast_clockNeutral':\s*'([^']*)'", text)
    assert m and not re.search(r"\d", m.group(1)), f"{lang}: the neutral label says no figure"


@pytest.mark.parametrize("lang, local", [("es", "simulación local"), ("pt", "simulação local")])
def test_sla_timers_are_labeled_a_local_simulation(lang: str, local: str):
    # The timers are simulated locally behind the lifecycle protocol; in the cloud they will be
    # Andrés's EventBridge Scheduler schedules. The judge and the analyst are told so.
    text = _i18n_text(lang)
    for key in ("judge.clock.note", "console.clock.note"):
        m = re.search(rf"'{re.escape(key)}':\s*\n?\s*'([^']*)'", text)
        assert m, f"{lang}: {key} missing"
        assert "EventBridge Scheduler" in m.group(1), f"{lang}: {key} must name EventBridge Scheduler"
        assert local in m.group(1).lower(), f"{lang}: {key} must say it is a {local}"


def test_sla_alert_chips_carry_text_and_icon():
    # Never color alone (WCAG 1.4.1): every alert chip renders an icon and its translated text.
    tsx = _read(SRC / "console" / "SlaClock.tsx")
    assert "t(`console.alert.${k}`)" in tsx and "<Icon size={12} />" in tsx
    for lang in ("es", "pt"):
        text = _i18n_text(lang)
        for kind in ("unassigned", "sla_80", "breached"):
            assert f"'console.alert.{kind}':" in text, f"{lang}: console.alert.{kind} missing"
