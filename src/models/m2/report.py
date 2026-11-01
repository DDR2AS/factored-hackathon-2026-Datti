"""M2 report: evaluates the shipped runtime on the team-written test set against the baselines
and renders data/eval/m2_report.yaml + docs/modelos/m2.md (Spanish). Offline only.

Called by ``models.m2.train`` after the artifact and thresholds are written, so every number
comes from the pure-Python runtime that the Lambda runs.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from models.m2 import evaluate as ev
from models.m2.features import CONCEPTS
from models.m2.model import CLASSES, MODEL_VERSION, M2Model, load

B2_NOT_RUN = "no ejecutado: requiere acceso a un modelo (LLM_PROVIDER=anthropic|bedrock)"
HONESTY = [
    "Todo es sintético: el modelo se entrena solo con mensajes generados por código (dos generadores, "
    "src/models/m2/textgen.py).",
    "El set de prueba (150 + 30 adversariales) lo escribió a mano el asistente de código a pedido de Arturo, porque el "
    "equipo no llegó a escribirlo (el plan v2 pedía mensajes escritos por el equipo). Se escribió en un contexto "
    "separado que no vio los generadores; el autor de los generadores no leyó sus mensajes antes de congelar el "
    "modelo (sí leyó un resumen con conteos y nombres de técnicas). Pendiente de revisión por Arturo.",
    "Ese mismo asistente escribió los generadores, el léxico de conceptos y las instrucciones del set de prueba, así "
    "que comparten autor: algunas ideas de mensajes adversariales benignos (el 'sistema' que cobra, 'ignoren mi "
    "mensaje anterior', códigos de error de la app, el 'administrador' del edificio) estaban como ejemplo en esas "
    "instrucciones y también aparecen como casos difíciles en los generadores. Los números sobreestiman el "
    "desempeño con clientes reales.",
    "Protocolo: el diseño (features, léxico, generadores, regularización, calibración y umbrales) se fijó solo con "
    "validación sintética y un set de desarrollo propio de 45 mensajes (los ejemplos de tests/test_m2.py); el set de "
    "prueba se abrió después solo para revisar etiquetas (todas se confirmaron) y formato, y se evaluó con el modelo "
    "congelado. Antes de entrenar se descartan los mensajes generados idénticos o casi idénticos (Jaccard > 0,9) a "
    "un mensaje de prueba: {decontaminated}.",
    "El portugués (y el portuñol) se generó sin revisión de un hablante nativo.",
    "B2 (LLM zero-shot) no se ejecutó: no hay acceso a un modelo en este entorno.",
]


def _r(x: float | None, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _pct(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{nd}f} %"


def _ci(ci: list | None, pct: bool = False) -> str:
    if not ci:
        return ""
    return f"[{_pct(ci[0])} – {_pct(ci[1])}]" if pct else f"[{ci[0]:.3f} – {ci[1]:.3f}]"


def _cell(c: Any) -> str:
    return str(c).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(_cell(h) for h in headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _size(n: int) -> str:
    return f"{n / 1e6:.2f} MB" if n >= 100_000 else f"{n / 1e3:.1f} kB"


# ---------------------------------------------------------------- evaluation

def _run_system(items: list[dict], outs: list[dict]) -> dict:
    preds = [o["intent_class"] for o in outs]
    gates = [o["gate_action"] for o in outs]
    correct = [p == it["label"] for p, it in zip(preds, items)]
    top2 = [it["label"] in (o["intent_class"], o.get("runner_up")) for o, it in zip(outs, items)]
    res = ev.evaluate_predictions(items, preds)
    res["gate"] = ev.gate_metrics(correct, gates, top2)
    res["gate_by_language"] = {}
    for lang in ev.LANGUAGES:
        sel = [i for i, it in enumerate(items) if it["language"] == lang]
        if sel:
            res["gate_by_language"][lang] = ev.gate_metrics([correct[i] for i in sel], [gates[i] for i in sel],
                                                            [top2[i] for i in sel])
    res["safety"] = ev.safety_metrics(items, preds, gates)
    res["predictions"] = [{"id": it["id"], "label": it["label"], "predicted": o["intent_class"], "p": o["p"],
                           "gate": o["gate_action"], "runner_up": o.get("runner_up")} for it, o in zip(items, outs)]
    return res


def evaluate_and_report(model: M2Model, data_info: dict, selection: list[dict], lam: float, temperature: float,
                        thresholds: dict, val_cross: dict, val_in: dict, cross_gen: dict, artifact: dict,
                        parity: float, accept_curve: list[dict], ask_windows: list[dict], seconds: float,
                        train_texts: list[str]) -> dict:
    items = ev.load_test_set()
    texts = [it["text"] for it in items]
    y = [it["label"] for it in items]

    # M2, exactly as the Lambda runs it
    outs = [model.classify(it["text"], it["language"]) for it in items]
    m2 = _run_system(items, outs)
    p_raw = [max(model.predict_proba(t, calibrated=False).values()) for t in texts]
    correct = [o["intent_class"] == it["label"] for o, it in zip(outs, items)]
    m2["calibration"] = {
        "ece_raw": round(ev.ece(p_raw, correct), 4),
        "ece_calibrated": round(ev.ece([o["p"] for o in outs], correct), 4),
        "reliability_calibrated": ev.reliability_table([o["p"] for o in outs], correct),
    }

    # B1: the keyword stand-in M2 replaces (imported only here, never at runtime)
    from conversation.classifier import MODEL_VERSION as KW_VERSION
    from conversation.classifier import classify as kw_classify

    kw_outs = [kw_classify(it["text"], it["language"]) for it in items]
    b1 = _run_system(items, kw_outs)
    b1["model_version"] = KW_VERSION

    boot = ev.bootstrap(y, {"m2": [o["intent_class"] for o in outs],
                            "b1_keywords": [o["intent_class"] for o in kw_outs]}, n_boot=2000, seed=0)

    t0 = time.perf_counter()
    load()
    cold_ms = (time.perf_counter() - t0) * 1000.0
    latency = ev.time_calls(lambda t: model.classify(t, "es"), texts, repeat=3)
    latency["cold_load_ms"] = round(cold_ms, 1)
    long_text = (texts[0] + " ") * 40
    latency["p50_ms_1000_chars"] = ev.time_calls(lambda t: model.classify(t, "es"), [long_text[:1000]] * 30,
                                                 repeat=1)["p50_ms"]

    overlap = ev.overlap_with_training(texts, train_texts)
    by_id = {it["id"]: it["text"] for it in items}
    errors = [{**r, "text": by_id[r["id"]]} for r in m2["predictions"] if r["label"] != r["predicted"]]

    def _counts(key: str) -> dict:
        out: dict[str, int] = {}
        for it in items:
            out[str(it[key])] = out.get(str(it[key]), 0) + 1
        return dict(sorted(out.items()))

    step = {round(x / 100, 2) for x in range(30, 100, 5)}
    dec = data_info.get("decontaminated") or {}
    removed = dec.get("texts") or []
    dec_text = (f"en esta versión, {len(removed)} ("
                + ", ".join(f"'{t.lower().rstrip('.')}'" for t in removed[:5]) + ")") if removed else "ninguno"
    return {
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "runtime_seconds": round(seconds + 0.0, 1),
        "synthetic": True,
        "honesty_notes": [n.replace("{decontaminated}", dec_text) for n in HONESTY],
        "data": data_info,
        "model": {"lambda": lam, "lambda_selection": selection, "temperature": round(temperature, 4),
                  "artifact": artifact, "training_serving_parity_max_abs_logit_diff": float(f"{parity:.3g}")},
        "thresholds": {**thresholds,
                       "accept_curve": [r for r in accept_curve if r["threshold"] in step],
                       "ask_windows": ask_windows},
        "validation": {"cross_fitted": val_cross, "in_distribution": val_in},
        "cross_generator": cross_gen,
        "test_set": {
            "path": "eval/test_messages/m2_test_set.yaml",
            "n": len(items),
            "n_regular": sum(1 for it in items if not it["adversarial"]),
            "n_adversarial": sum(1 for it in items if it["adversarial"]),
            "by_label": _counts("label"), "by_language": _counts("language"), "by_country": _counts("country"),
            "overlap_with_training": {"n": len(overlap), "items": overlap,
                                      "rule": "exact match after normalization or character-trigram Jaccard > 0.9"},
            "systems": {"m2": m2, "b1_keywords": b1,
                        "b2_zero_shot": {"status": B2_NOT_RUN,
                                         "how_to_run": "models.m2.evaluate.evaluate_zero_shot(client) with a #8 "
                                                       "client; prompt src/prompts/m2_zero_shot/v1.md"}},
            "bootstrap": boot,
            "errors_m2": errors,
        },
        "latency": latency,
    }


# ---------------------------------------------------------------- markdown (Spanish)

def _sys_row(name: str, res: dict, boot: dict | None) -> list[str]:
    o = res["overall"]
    ci_f1 = _ci((boot or {}).get("macro_f1_ci95"))
    ci_acc = _ci((boot or {}).get("accuracy_ci95"))
    by = res["by_language"]
    return [name, f"{_r(o['macro_f1'])} {ci_f1}", f"{_r(o['accuracy'])} {ci_acc}",
            _r(by.get("es", {}).get("macro_f1")), _r(by.get("pt", {}).get("macro_f1")),
            _r(res["regular"]["macro_f1"]), _r(res["adversarial"]["accuracy"])]


def render_markdown(rep: dict) -> str:
    ts = rep["test_set"]
    m2, b1 = ts["systems"]["m2"], ts["systems"]["b1_keywords"]
    boot = ts["bootstrap"]
    thr = rep["thresholds"]
    vc, vi = rep["validation"]["cross_fitted"], rep["validation"]["in_distribution"]
    data = rep["data"]
    lat = rep["latency"]
    art = rep["model"]["artifact"]
    diff = boot.get("difference", {})
    L: list[str] = []
    add = L.append

    add(f"# M2 · Clasificador de intención calibrado (`{rep['model_version']}`)\n")
    add(f"Generado por `python -m models.m2.train` el {rep['generated_at']} ({rep['runtime_seconds']} s). "
        "No editar a mano: se reescribe en cada entrenamiento. Detalle completo: `data/eval/m2_report.yaml` "
        "(no versionado, se regenera).\n")
    notes = rep["honesty_notes"]
    add("> **" + notes[0] + "** " + " ".join(notes[1:-1]) + "\n")

    add("## Resumen\n")
    add(_table(["", f"M2 `{rep['model_version']}`", f"B1 palabras clave `{b1.get('model_version', 'm2-keywords-0')}`",
                "B2 LLM zero-shot"], [
        ["macro-F1, set de prueba (n = %d)" % ts["n"], f"**{_r(m2['overall']['macro_f1'])}** {_ci(boot['m2']['macro_f1_ci95'])}",
         f"{_r(b1['overall']['macro_f1'])} {_ci(boot['b1_keywords']['macro_f1_ci95'])}", B2_NOT_RUN],
        ["exactitud", f"{_r(m2['overall']['accuracy'])} {_ci(boot['m2']['accuracy_ci95'])}",
         f"{_r(b1['overall']['accuracy'])} {_ci(boot['b1_keywords']['accuracy_ci95'])}", "—"],
        ["macro-F1 español / portugués", f"{_r(m2['by_language'].get('es', {}).get('macro_f1'))} / "
                                          f"{_r(m2['by_language'].get('pt', {}).get('macro_f1'))}",
         f"{_r(b1['by_language'].get('es', {}).get('macro_f1'))} / {_r(b1['by_language'].get('pt', {}).get('macro_f1'))}",
         "—"],
        ["ruteo erróneo entre aceptados (prueba)", f"{_pct(m2['gate']['wrong_routing_accepted'])} "
                                                   f"({m2['gate']['wrong_accepted']}/{m2['gate']['n_accepted']})",
         f"{_pct(b1['gate']['wrong_routing_accepted'])} ({b1['gate']['wrong_accepted']}/{b1['gate']['n_accepted']})", "—"],
        ["acepta / pregunta / humano (prueba)", f"{_pct(m2['gate']['accept_rate'])} / {_pct(m2['gate']['ask_rate'])} / "
                                                f"{_pct(m2['gate']['human_rate'])}",
         f"{_pct(b1['gate']['accept_rate'])} / {_pct(b1['gate']['ask_rate'])} / {_pct(b1['gate']['human_rate'])}", "—"],
        ["ECE 10 bins (prueba), antes → después", f"{_r(m2['calibration']['ece_raw'])} → "
                                                   f"{_r(m2['calibration']['ece_calibrated'])}", "no calibrado", "—"],
        ["manipulación detectada (recall)", f"{_pct(m2['safety']['manipulation_recall'])} "
                                            f"(n = {m2['safety']['n_manipulation']})",
         f"{_pct(b1['safety']['manipulation_recall'])}", "—"],
        ["benignos marcados como manipulación", f"{m2['safety']['benign_flagged_as_manipulation']} de "
                                                f"{m2['safety']['n_benign']}",
         f"{b1['safety']['benign_flagged_as_manipulation']} de {b1['safety']['n_benign']}", "—"],
    ]))
    if diff:
        add(f"\nDiferencia M2 − B1 (bootstrap pareado, {boot['n_boot']} remuestras): macro-F1 "
            f"{_ci(diff['macro_f1_ci95'])}, exactitud {_ci(diff['accuracy_ci95'])}; "
            f"P(M2 no mejora a B1 en macro-F1) = {diff['p_macro_f1_not_better']:.3f}.\n")
    add("Los intervalos son percentiles 2,5–97,5 % de un bootstrap sobre los mensajes del set de prueba "
        "(n pequeño: tómense como orden de magnitud).\n")

    add("## Uso\n")
    add("```python\nfrom models.m2 import classify   # import root: src/\n"
        "classify(\"no reconozco un cargo de 450 en OXXO\", \"es\")\n"
        "# {'intent_class': 'dispute_charge', 'p': 0.98, 'gate_action': 'accept',\n"
        "#  'runner_up': 'dispute_fee', 'model_version': 'm2-hashlr-1'}\n```\n")
    add("- Contrato #4 (`INTERFACES.md`): `classify(text, language) -> {intent_class, p, gate_action, runner_up, "
        "model_version}`. `text` ya redactado; `language` `es` | `pt` | `mixed` | `None` (no se usa como feature). "
        "`p` calibrada y redondeada a 3 decimales; `runner_up` = segunda clase (puede ser `manipulation`: una "
        "pregunta aclaratoria no debe ofrecerla como opción).\n"
        "- Mensaje sin nada legible (vacío, solo emojis): `complaint_other`, p = 0.111, `ask` (igual que el sustituto "
        "por palabras clave).\n"
        "- Inferencia en Python puro (biblioteca estándar + PyYAML para leer la configuración); carga perezosa y "
        "segura entre hilos, una vez por proceso. Nunca importa numpy ni scikit-learn.\n"
        "- `classify` es solo el modelo: la guarda por reglas contra inyección (patrones de `manipulation` del "
        "sustituto) vive en el orquestador, en OR con M2, para que un falso negativo del modelo no deje pasar una "
        "inyección.\n"
        "- Umbrales: `src/models/thresholds.yaml`, clave `m2` (nunca en el código). Artefacto: "
        f"`{art['path']}/` ({art['total_bytes'] / 1e6:.2f} MB, sin JSON).\n")

    add("## Datos de entrenamiento (sintéticos)\n")
    add("Dos generadores independientes (`src/models/m2/textgen.py`), escritos como \"dos prompts distintos\" del plan: "
        "**A** \"carta ordenada\" (frases completas, saludos y despedidas, tú/usted/vos según el país, ruido leve) y "
        "**B** \"chat de WhatsApp\" (fragmentos, jerga, abreviaturas, varias líneas, historias largas con emociones y "
        "temas secundarios, ruido fuerte). Ambos cubren las 9 clases, español de MX/CO/AR, portugués y algo de "
        "portuñol; montos, fechas, comercios, números de caso `EV-XXXXXXXX` y marcadores de redacción "
        "(`[documento]`, `[teléfono]`...) salen de listas pequeñas. Las manipulaciones incluyen inyecciones "
        "clásicas, notas de sistema falsas, pedidos de campos internos, marcado, ingeniería social y ofuscación "
        "(leet, letras espaciadas, homoglifos, ancho completo, base64); las clases benignas incluyen mensajes que "
        "se parecen a una inyección sin serlo.\n")
    add(_table(["", "A", "B", "total"], [
        ["mensajes generados", data["n_a"], data["n_b"], data["n_a"] + data["n_b"]],
        ["entrenamiento / validación", "", "", f"{data['n_train']} / {data['n_validation']}"],
        ["frases núcleo (plantillas) entrenamiento / validación", "", "",
         f"{data['templates_train']} / {data['templates_validation']}"],
    ]))
    add("\nLa validación separa **frases núcleo enteras** (≈20 % por generador, clase e idioma): ningún mensaje de "
        "validación comparte frase núcleo con el entrenamiento. Por idioma en entrenamiento: "
        + ", ".join(f"{k} {v}" for k, v in data["train_by_language"].items())
        + "; por país: " + ", ".join(f"{k} {v}" for k, v in data["train_by_country"].items()) + ".\n")

    add("## Modelo\n")
    add("- **Features (`features.embed`)**: texto normalizado (minúsculas, sin tildes, NFKC, homoglifos cirílicos/"
        "griegos → latín, leet y letras espaciadas reconstruidas, dígitos → 0, números de caso → `evcaso`) y tres "
        "bloques con hashing firmado (`zlib.crc32`, 2^15 cubetas, TF sublineal, norma L2 por bloque): n-gramas de "
        "caracteres 2–5 por token, unigramas y bigramas de palabras (+ banderas de ofuscación), y un **léxico de "
        f"{len(CONCEPTS)} conceptos** multilingüe escrito a mano (comisión, cargo no "
        "reconocido, duplicado, pérdida, bloqueo, persona, caso, estado, saldo, app, sucursal, préstamo, "
        "inyección, marcado...). El léxico es la capa semántica barata que reemplaza a los embeddings "
        "multilingües del plan (no hay acceso a embeddings de Bedrock); `embed(text)` es el punto donde se "
        "enchufarían Titan/Cohere.\n"
        f"- **Clasificador**: regresión logística multinomial (scikit-learn, lbfgs, L2) con λ = {rep['model']['lambda']:g} "
        "por mensaje, elegido por macro-F1 entre generadores (entrenar en A y medir en B, y al revés). Aumento de "
        "datos: una copia de cada mensaje sin ~15 % de sus palabras. Pesos float32 en `weights.bin`.\n"
        f"- **Calibración**: temperatura T = {rep['model']['temperature']} ajustada por log-verosimilitud sobre la "
        "**validación cruzada entre generadores** (el modelo entrenado con A puntúa la validación de B y "
        "viceversa): mensajes nuevos en frase y en estilo, lo más parecido que tenemos a un autor nuevo. La "
        "validación del mismo generador haría que el modelo parezca más seguro de lo que es con texto humano.\n"
        f"- **Paridad entrenamiento/servicio**: diferencia máxima de logits entre scikit-learn y el runtime en "
        f"Python puro = {rep['model']['training_serving_parity_max_abs_logit_diff']:.1e} en 300 mensajes (solo el "
        "redondeo de los pesos a float32).\n")

    add("## Calibración\n")
    add(_table(["conjunto", "n", "exactitud", "ECE antes (T = 1)", "ECE después", "NLL antes", "NLL después"], [
        ["validación cruzada entre generadores (ajuste)", vc["n"], _r(vc["accuracy"]), _r(vc["ece_raw"], 4),
         _r(vc["ece_calibrated"], 4), _r(vc["nll_raw"]), _r(vc["nll_calibrated"])],
        ["validación del mismo generador (modelo final)", vi["n"], _r(vi["accuracy"]), _r(vi["ece_raw"], 4),
         _r(vi["ece_calibrated"], 4), _r(vi["nll_raw"]), _r(vi["nll_calibrated"])],
        ["set de prueba escrito a mano", ts["n"], _r(m2["overall"]["accuracy"]), _r(m2["calibration"]["ece_raw"], 4),
         _r(m2["calibration"]["ece_calibrated"], 4), "", ""],
    ]))
    alt = vi.get("alternative_if_calibrated_here", {})
    t = rep["model"]["temperature"]
    if abs(t - 1.0) < 0.05:
        add(f"\nCon el λ elegido la regresión llegó casi calibrada a la validación cruzada: T = {t} y el paso de "
            "temperatura cambia poco el ECE. Se mantiene porque T depende de λ y de los datos y se recalcula en cada "
            "entrenamiento.")
    add(f"\nSi se calibrara sobre la validación del mismo generador, T sería {alt.get('temperature')} y el umbral de "
        f"aceptación {alt.get('accept_threshold')} (no se usa: subestimaría el riesgo con texto nuevo).\n")
    rel = m2["calibration"]["reliability_calibrated"]
    if rel:
        add("Confiabilidad en el set de prueba (p calibrada):\n")
        add(_table(["bin de p", "n", "p media", "exactitud"],
                   [[r["bin"], r["n"], _r(r["confidence"]), _r(r["accuracy"])] for r in rel]))
        add("")

    add("## Umbrales del gate (`src/models/thresholds.yaml`, clave `m2`)\n")
    vg = vc["gate"]
    add(_table(["", "valor", "criterio"], [
        ["accept_threshold", thr["accept_threshold"], thr["accept_criterion"]],
        ["ask_threshold", thr["ask_threshold"], thr["ask_criterion"]],
    ]))
    add("")
    add(_table(["conjunto", "acepta", "pregunta", "humano", "ruteo erróneo entre aceptados", "top-2 al preguntar"], [
        ["validación cruzada (elección)", _pct(vg["accept_rate"]), _pct(vg["ask_rate"]), _pct(vg["human_rate"]),
         f"{_pct(vg['wrong_routing_accepted'], 2)} {_ci(vg['wrong_routing_accepted_ci95'], True)}",
         _pct(vg.get("top2_coverage_ask"))],
        ["validación del mismo generador", _pct(vi["gate"]["accept_rate"]), _pct(vi["gate"]["ask_rate"]),
         _pct(vi["gate"]["human_rate"]), _pct(vi["gate"]["wrong_routing_accepted"], 2), _pct(vi["gate"].get("top2_coverage_ask"))],
        ["set de prueba (solo se reporta)", _pct(m2["gate"]["accept_rate"]), _pct(m2["gate"]["ask_rate"]),
         _pct(m2["gate"]["human_rate"]),
         f"{_pct(m2['gate']['wrong_routing_accepted'], 2)} {_ci(m2['gate']['wrong_routing_accepted_ci95'], True)}",
         _pct(m2["gate"].get("top2_coverage_ask"))],
    ]))
    add("\nRegla: `p` calibrada redondeada a 3 decimales; `accept` si p ≥ accept_threshold, `ask` si "
        "ask_threshold ≤ p < accept_threshold, `human` debajo. Los umbrales se eligieron solo con validación; "
        "el set de prueba nunca se usó para elegir nada.\n")

    add("## Resultados en el set de prueba escrito a mano\n")
    add(f"{ts['n']} mensajes: {ts['n_regular']} regulares + {ts['n_adversarial']} adversariales; por idioma "
        + ", ".join(f"{k} {v}" for k, v in ts["by_language"].items()) + "; por país "
        + ", ".join(f"{k} {v}" for k, v in ts["by_country"].items())
        + f". Solapamiento con el entrenamiento (idéntico tras normalizar o Jaccard de trigramas > 0,9): "
          f"**{ts['overlap_with_training']['n']}**.\n")
    add(_table(["sistema", "macro-F1 (IC 95 %)", "exactitud (IC 95 %)", "macro-F1 es", "macro-F1 pt",
                "macro-F1 regulares", "exactitud adversariales"], [
        _sys_row(f"M2 `{rep['model_version']}`", m2, boot["m2"]),
        _sys_row("B1 palabras clave", b1, boot["b1_keywords"]),
        ["B2 LLM zero-shot", B2_NOT_RUN, "", "", "", "", ""],
    ]))
    add("\n### Por idioma y país\n")
    rows = []
    for key, label in (("by_language", "idioma"), ("by_country", "país")):
        for v, mm in m2[key].items():
            bb = b1[key].get(v, {})
            rows.append([f"{label} {v}", mm["n"], _r(mm["macro_f1"]), _r(mm["accuracy"]), _r(bb.get("macro_f1")),
                         _r(bb.get("accuracy"))])
    add(_table(["subconjunto", "n", "M2 macro-F1", "M2 exactitud", "B1 macro-F1", "B1 exactitud"], rows))
    sizes = [r[1] for r in rows]
    add(f"\nCon n entre {min(sizes)} y {max(sizes)} por subconjunto, diferencias de pocos puntos entre idiomas o "
        "países no son significativas (el portuñol, con n muy chico, no permite concluir nada).\n")
    add("### Por clase\n")
    pc, pb = m2["overall"]["per_class"], b1["overall"]["per_class"]
    add(_table(["clase", "n", "M2 precisión", "M2 recall", "M2 F1", "B1 F1"],
               [[c, pc[c]["support"], _r(pc[c]["precision"]), _r(pc[c]["recall"]), _r(pc[c]["f1"]), _r(pb[c]["f1"])]
                for c in CLASSES]))
    add("\n### Matriz de confusión de M2 (filas = clase real, columnas = predicha)\n")
    cm = m2["overall"]["confusion"]
    short = {"dispute_charge": "d_charge", "dispute_fee": "d_fee", "complaint_other": "compl", "case_status": "case",
             "account_query": "account", "lost_card": "lost", "human_request": "human", "out_of_scope": "oos",
             "manipulation": "manip", "invalid": "inv"}
    add(_table(["real \\ pred"] + [short.get(c, c) for c in cm["columns"]],
               [[short.get(lbl, lbl)] + row for lbl, row in zip(cm["labels"], cm["matrix"])]))
    add("\n### Seguridad (manipulación)\n")
    s2, s1 = m2["safety"], b1["safety"]
    add(_table(["", "M2", "B1"], [
        ["manipulaciones detectadas", f"{s2['n_manipulation'] - len(s2['missed_manipulation_ids'])} de {s2['n_manipulation']}",
         f"{s1['n_manipulation'] - len(s1['missed_manipulation_ids'])} de {s1['n_manipulation']}"],
        ["manipulaciones no detectadas y aceptadas como otra intención", s2.get("missed_manipulation_accepted"),
         s1.get("missed_manipulation_accepted")],
        ["mensajes benignos marcados como manipulación", s2["benign_flagged_as_manipulation"],
         s1["benign_flagged_as_manipulation"]],
    ]))
    add(f"\nNo detectadas por M2: {', '.join(s2['missed_manipulation_ids']) or 'ninguna'}. Benignos marcados por M2: "
        f"{', '.join(s2['benign_flagged_ids']) or 'ninguno'}. En producción la guarda por reglas del orquestador "
        "corre en OR con M2.\n")
    errs = ts["errors_m2"]
    if errs:
        add("### Errores de M2 en el set de prueba\n")
        add(_table(["id", "real", "predicha", "p", "gate", "mensaje (inicio)"],
                   [[e["id"], e["label"], e["predicted"], _r(e["p"]), e["gate"],
                     "`" + e.get("text", "")[:70].replace("`", "'") + ("…" if len(e.get("text", "")) > 70 else "") + "`"]
                    for e in errs]))
        add("")

    add("## Generalización entre generadores (chequeo de honestidad)\n")
    cg = rep["cross_generator"]
    add(_table(["entrena → evalúa", "n", "exactitud", "macro-F1", "ECE (T = 1)"], [
        ["A → B", cg["A_to_B"]["n"], _r(cg["A_to_B"]["accuracy"]), _r(cg["A_to_B"]["macro_f1"]), _r(cg["A_to_B"]["ece_raw"])],
        ["B → A", cg["B_to_A"]["n"], _r(cg["B_to_A"]["accuracy"]), _r(cg["B_to_A"]["macro_f1"]), _r(cg["B_to_A"]["ece_raw"])],
        ["A+B → validación (frases no vistas)", vi["n"], _r(vi["accuracy"]), _r(vi["macro_f1"]), _r(vi["ece_raw"])],
    ]))
    add("\nUn modelo entrenado con un solo generador pierde varios puntos con el otro: el estilo importa. Por eso la "
        "calibración y los umbrales usan la validación cruzada entre generadores.\n")

    add("## Latencia y artefacto\n")
    add(_table(["medida", "valor"], [
        ["p50 / p95 por mensaje (set de prueba)", f"{lat['p50_ms']} ms / {lat['p95_ms']} ms"],
        ["p50 con 1000 caracteres", f"{lat['p50_ms_1000_chars']} ms"],
        ["carga en frío (artefacto + umbrales)", f"{lat['cold_load_ms']} ms"],
        ["artefacto", ", ".join(f"`{f['name']}` {_size(f['bytes'])}" for f in art["files"])],
    ]))
    add("")

    add("## Limitaciones y próximos pasos\n")
    add("- Todo el texto es sintético y del mismo autor (el asistente); con clientes reales habrá jerga, temas y "
        "errores que nadie generó. Antes de producción: etiquetar mensajes reales (las decisiones del analista "
        "emiten `intent:<clase>:confirmed|corrected_to`, interfaz #10) y reentrenar.\n"
        "- El léxico de conceptos es conocimiento de dominio escrito a mano: amplía la cobertura pero también "
        "comparte sesgos con los generadores. Reemplazarlo por embeddings multilingües (Titan/Cohere vía Bedrock) es "
        "el siguiente paso natural; `embed(text)` es la costura.\n"
        "- La calibración se ajustó con texto sintético de otro estilo; con texto real hay que volver a medir ECE y "
        "el ruteo erróneo, y recalibrar con mensajes etiquetados.\n"
        "- B2 (LLM zero-shot) queda pendiente de una corrida con acceso al modelo.\n")
    add("## Cómo reproducir\n")
    add("```bash\n# desde la raíz del repo (Python 3.12, numpy, scipy, scikit-learn, PyYAML)\n"
        "PYTHONPATH=src python -m models.m2.train        # datos + entrenamiento + umbrales + reporte (~1 min)\n"
        "python -m pytest -q tests/test_m2.py\n```\n")
    add("Semillas fijas: el mismo comando produce los mismos datos (huellas sha256 en `meta.yaml`), el mismo "
        "artefacto y los mismos umbrales.\n")
    return "\n".join(L) + "\n"


def summary(rep: dict) -> str:
    ts = rep["test_set"]
    m2, b1 = ts["systems"]["m2"], ts["systems"]["b1_keywords"]
    g = m2["gate"]
    thr = rep["thresholds"]
    lines = [
        f"M2 {rep['model_version']} (SYNTHETIC data) - test set n={ts['n']} "
        f"(overlap with training: {ts['overlap_with_training']['n']})",
        f"  macro-F1  M2 {m2['overall']['macro_f1']:.3f} {ts['bootstrap']['m2']['macro_f1_ci95']}  vs  "
        f"B1 keywords {b1['overall']['macro_f1']:.3f} {ts['bootstrap']['b1_keywords']['macro_f1_ci95']}  "
        f"(B2 zero-shot: not run)",
        "  by language M2: " + ", ".join(f"{k} {v['macro_f1']:.3f} (n={v['n']})" for k, v in m2["by_language"].items())
        + " | B1: " + ", ".join(f"{k} {v['macro_f1']:.3f}" for k, v in b1["by_language"].items()),
        "  by country  M2: " + ", ".join(f"{k} {v['macro_f1']:.3f}" for k, v in m2["by_country"].items()),
        f"  ECE test {m2['calibration']['ece_raw']:.3f} -> {m2['calibration']['ece_calibrated']:.3f} "
        f"(T={rep['model']['temperature']}); validation {rep['validation']['cross_fitted']['ece_raw']:.3f} -> "
        f"{rep['validation']['cross_fitted']['ece_calibrated']:.3f}",
        f"  gate accept>={thr['accept_threshold']} ask>={thr['ask_threshold']}: test accept {g['accept_rate']:.1%} "
        f"ask {g['ask_rate']:.1%} human {g['human_rate']:.1%}, wrong among accepted {g['wrong_routing_accepted']:.1%} "
        f"({g['wrong_accepted']}/{g['n_accepted']}); validation wrong {thr['validation']['wrong_routing_accepted']:.1%}",
        f"  manipulation recall M2 {m2['safety']['manipulation_recall']:.1%} vs B1 {b1['safety']['manipulation_recall']:.1%}; "
        f"benign flagged M2 {m2['safety']['benign_flagged_as_manipulation']} vs B1 {b1['safety']['benign_flagged_as_manipulation']}",
        f"  latency p50 {rep['latency']['p50_ms']} ms, p95 {rep['latency']['p95_ms']} ms; artifact "
        f"{rep['model']['artifact']['total_bytes'] / 1e6:.2f} MB",
    ]
    return "\n".join(lines)
