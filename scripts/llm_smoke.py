"""Prueba rápida del cliente #8 (src/llm) contra un modelo real: UNA llamada a
``complete("g1_extract", ...)`` con un texto sintético (sugerencia del modo juez de Lucía).

Uso, desde la raíz del repo (Git Bash):

    LLM_PROVIDER=anthropic python scripts/llm_smoke.py             # ANTHROPIC_API_KEY en el entorno
    LLM_PROVIDER=bedrock AWS_REGION=us-east-2 python scripts/llm_smoke.py   # credenciales AWS
    python scripts/llm_smoke.py --provider anthropic --role judge  # otro rol (otro modelo)

Imprime model_id, prompt_version, tokens, costo estimado, latencia y la salida validada con el
esquema de G1. Gasta tokens reales (unos 1.400 de entrada con el chat). Sin proveedor real o sin
credenciales sale con código 2 y lo dice. Nunca imprime ni guarda claves.

Códigos de salida: 0 ok · 1 el modelo respondió algo inutilizable · 2 sin proveedor o sin
credenciales/acceso · 3 timeout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
# SINTÉTICO: primera sugerencia del modo juez de Lucía (tests/fixtures/llm/g1_extract/lucia.yaml).
SAMPLE_TEXT = "Hola, me salió un cobro como de 450 en el súper el 12 y no lo ubico"
SAMPLE_REFERENCE_DATE = "2026-06-15"
REAL_PROVIDERS = ("anthropic", "bedrock")
HELP_CREDENTIALS = ("  anthropic: exporta ANTHROPIC_API_KEY (o usa un perfil de `ant auth login`).\n"
                    "  bedrock: credenciales AWS en el entorno (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY o un perfil) y "
                    "AWS_REGION; el modelo habilitado en Bedrock y permiso bedrock-mantle:CreateInference.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Una llamada real a llm.complete('g1_extract') (gasta tokens).")
    parser.add_argument("--provider", choices=REAL_PROVIDERS, default=None, help="reemplaza LLM_PROVIDER")
    parser.add_argument("--role", choices=("chat", "investigator", "judge"), default="chat",
                        help="rol del modelo (chat por defecto)")
    args = parser.parse_args(argv)
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import llm
    from conversation import extract
    from conversation.redact import redact

    provider = (args.provider or os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if provider not in REAL_PROVIDERS:
        print(f"llm_smoke: LLM_PROVIDER={provider or '(sin definir)'} no llama a un modelo real. Usa "
              "LLM_PROVIDER=anthropic o LLM_PROVIDER=bedrock, o --provider.\n" + HELP_CREDENTIALS, file=sys.stderr)
        return 2
    model_id = llm.model_id_for(args.role, provider)
    variables = {"text": redact(SAMPLE_TEXT, "es"), "language": "es", "reference_date": SAMPLE_REFERENCE_DATE}
    print(f"llm_smoke: proveedor {provider}, rol {args.role}, modelo {model_id}; una llamada...", flush=True)
    started = time.perf_counter()
    try:
        out = llm.complete("g1_extract", variables, output_schema=extract.Extraction.model_json_schema(),
                           model_role=args.role, provider=provider)
    except llm.ProviderUnavailable as e:
        print(f"llm_smoke: proveedor {provider} no disponible: {e}\n" + HELP_CREDENTIALS, file=sys.stderr)
        return 2
    except TimeoutError as e:
        print(f"llm_smoke: timeout: {e}", file=sys.stderr)
        return 3
    except llm.ModelError as e:
        print(f"llm_smoke: el modelo respondió algo inutilizable: {e}", file=sys.stderr)
        return 1
    latency_ms = int(round((time.perf_counter() - started) * 1000))
    usage = out["usage"]
    print(f"model_id={out['model_id']} prompt_version={out['prompt_version']} latency_ms={latency_ms}")
    print(f"tokens_in={usage['tokens_in']} tokens_out={usage['tokens_out']} "
          f"cache_read={usage.get('cache_read_tokens', 0)} cache_write={usage.get('cache_write_tokens', 0)} "
          f"cost_usd={usage['cost_usd']:.6f} (estimado={usage['estimated']})")
    print(json.dumps(out["output"], ensure_ascii=False, indent=2))
    try:
        extract.Extraction.model_validate(out["output"])
    except Exception as e:  # the G1 schema; G1 itself would drop this output
        print(f"llm_smoke: la salida no valida con el esquema de G1 ({type(e).__name__})", file=sys.stderr)
        return 1
    print("llm_smoke: OK (la salida valida con el esquema Extraction de G1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
