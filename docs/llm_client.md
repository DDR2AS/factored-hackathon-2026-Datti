# Cliente LLM #8 (`src/llm`)

Paquete único por el que el código llama a un modelo (INTERFACES.md #8). Elige el proveedor por variable de entorno, mapea cada rol a un model ID, renderiza los prompts versionados, normaliza respuestas y errores, y estima tokens y costo. Ningún llamador escribe un model ID ni un prompt crudo.

## API

```python
import llm

out = llm.complete("g1_extract", {"text": ..., "language": "es", "reference_date": "2026-06-15"},
                   output_schema=Extraction.model_json_schema(), model_role="chat")
# {"output": dict | str, "usage": {"tokens_in", "tokens_out", "cost_usd", "estimated",
#   "cache_read_tokens", "cache_write_tokens"}, "model_id": str, "prompt_version": "g1_extract@v1"}

turn = llm.converse(system, messages, tools, model_role="investigator")   # un turno con herramientas
# {"content": [{"type": "text", ...} | {"type": "tool_use", "id", "name", "input"} | thinking],
#  "stop_reason": "tool_use" | "end_turn" | "max_tokens" | "refusal", "usage": {...}, "model_id": str}

llm.model_id_for("chat")            # model ID del rol con el proveedor actual
llm.provider_name()                 # mock | none | anthropic | bedrock
llm.estimate_cost(model_id, tokens_in, tokens_out, cache_read=0, cache_write=0)
```

- `complete` con `output_schema` devuelve el objeto JSON ya parseado (structured outputs: `output_config.format`, con el esquema adaptado por `anthropic.transform_schema`). Si el modelo o la plataforma lo rechaza (HTTP 400), reintenta con una herramienta `strict` (`tool_choice: auto` y una instrucción) y, si también la rechaza (esquema demasiado complejo para el modo estricto), con la misma herramienta sin `strict`; recuerda para ese modelo el modo que funcionó. Sin esquema devuelve el texto. El llamador valida igual la salida (G1 lo hace con Pydantic).
- `converse` es un solo turno de la API de Messages con herramientas del cliente (`{name, description, input_schema}`, opcional `strict: true`). El loop de G2 vive en `src/investigator/`: agrega `turn["content"]` sin tocarlo como turno `assistant` (los bloques `thinking` vuelven tal cual, con su firma) y decide según `stop_reason`. `max_tokens` y `timeout` son opcionales (por defecto, los del rol).
- Errores: `TimeoutError` (builtin), `llm.ProviderUnavailable` (sin SDK o credenciales, 401/403, modelo no encontrado o no habilitado, error de red, 429/5xx después de los reintentos), `llm.ModelError` (refusal, salida truncada por `max_tokens`, JSON inválido o que no es objeto, 400/422) y `llm.NoFixture` (mock sin fixture, subclase de `ModelError`). Un prompt o rol inexistente es `FileNotFoundError`/`ValueError`.
- Nunca se envían `temperature`, `top_p` ni `top_k` (la API los rechaza desde la versión 2026-10-21) ni `tool_choice` forzado.

## Proveedores y variables de entorno

| Variable | Uso |
|---|---|
| `LLM_PROVIDER` | `mock` (local, fixtures YAML), `none` (sin modelo: `complete` lanza `ProviderUnavailable`), `anthropic` (API de Anthropic) o `bedrock` (Claude en Amazon Bedrock). Sin definir = `none`, igual que el puerto de la app |
| `MODEL_CHAT`, `MODEL_INVESTIGATOR`, `MODEL_JUDGE` | Model ID por rol. La CDK los pone con el prefijo `anthropic.` de Bedrock (`infra/cdk.json`). Con `anthropic` el cliente quita el prefijo; con `bedrock` lo agrega si falta. Sin definir: defaults de `src/llm/models.yaml` |
| `AWS_REGION` | Región de Bedrock (Lambda la define; si falta, `us-east-2`) |
| `LLM_TIMEOUT_SECONDS` | Timeout del rol `chat` (8 s por defecto) |
| `ANTHROPIC_API_KEY` | La lee el SDK, no este código. Solo en el entorno o en `.env` local, nunca en git. También sirve un perfil de `ant auth login` |
| Credenciales AWS | Cadena estándar de AWS (variables, perfil, rol de la Lambda). En la nube, el rol de la función |

- `anthropic` usa `anthropic.Anthropic()`. `bedrock` usa `anthropic.AnthropicBedrockMantle(aws_region=...)`: el endpoint de Bedrock con la misma API de Messages (`https://bedrock-mantle.<región>.api.aws/anthropic/v1/messages`). Mismo código de request para ambos.
- Los clientes se crean al primer uso, uno por proveedor y rol, y se reutilizan (thread-safe). `llm.reset_clients()` los olvida. El puerto llama a `llm.warm_up()` al cargar el paquete para importar el SDK antes del primer turno.
- `mock`: `complete` responde con el mismo mock determinista de la app (`tests/fixtures/llm/<prompt_id>/*.yaml`, `model_id` `mock-g1`, costo estimado). `converse` necesita `mock_policy(system, messages, tools)` que devuelva `{content, stop_reason}`; el cliente agrega `usage` estimado (caracteres/4) y `model_id` `mock-<rol>`. Sin política, `ProviderUnavailable`.

## Modelos por rol (D5)

| Rol | Uso | Anthropic | Bedrock |
|---|---|---|---|
| `chat` | G1, extracción por turno | `claude-haiku-4-5` | `anthropic.claude-haiku-4-5` |
| `investigator` | G2, loop con herramientas | `claude-sonnet-5` | `anthropic.claude-sonnet-5` |
| `judge` | G3, evaluación offline | `claude-opus-5` | `anthropic.claude-opus-5` |

Existe la generación 5.5, más nueva y más barata (Haiku 5.5, Sonnet 5.5, Opus 5.5). Cambiar es solo configuración (`MODEL_*` o `models.yaml`) y lo decide el equipo; D5 queda como se decidió. `effort` y `thinking` se configuran por rol en `models.yaml` y se envían solo a modelos que los aceptan (a Haiku 4.5 nunca). Por defecto no se envía ninguno: Sonnet 5 y Opus 5 piensan en modo adaptive y usan `effort` `high`.

## Costo (estimado)

`cost_usd` = tokens × precio de lista de la API de Anthropic (`src/llm/prices.yaml`, USD por millón de tokens). Es una estimación: Bedrock factura con su propia lista, así que con `bedrock` (y con `mock`) `usage.estimated` es `true`. `tokens_in` cuenta todo el input (sin caché + lecturas + escrituras de caché).

| Modelo | Input | Output | Lectura de caché | Escritura de caché (5 min) |
|---|---|---|---|---|
| Haiku 4.5 | 1,00 | 5,00 | 0,10 | 1,25 |
| Sonnet 5 | 2,00 | 10,00 | 0,20 | 2,50 |
| Opus 5 | 5,00 | 25,00 | 0,50 | 6,25 |
| Haiku 5.5 | 0,10 | 0,50 | 0,01 | 0,125 |
| Sonnet 5.5 | 2,00 | 10,00 | 0,20 | 2,50 |
| Opus 5.5 | 4,00 | 20,00 | 0,20 | 5,00 |

Ejemplo: un turno de G1 con Haiku 4.5 (~1.400 tokens de entrada con el esquema, ~80 de salida) cuesta unos 0,0018 USD.

## Timeouts, reintentos y fallback

| Rol | `max_tokens` | Timeout por intento | Reintentos del SDK |
|---|---|---|---|
| `chat` | 1024 | 8 s (`LLM_TIMEOUT_SECONDS`) | 1 |
| `investigator` | 8192 | 30 s por llamada (o `timeout=` de `converse`) | 2 |
| `judge` | 16000 | 120 s | 2 |

- El SDK reintenta 429, 5xx, errores de conexión y timeouts con backoff; después el cliente lanza `ProviderUnavailable` (o `TimeoutError`). En el peor caso una llamada dura timeout × (reintentos + 1) más el backoff; G1 corta igual a los 8 s por su lado.
- Después de un error de credenciales o permisos (sin credenciales, 401, 403) el proveedor queda bloqueado 60 s (`unavailable_backoff_seconds`): cada llamada falla al instante en vez de esperar otra vez.
- En G1 (`conversation/llm_port.PackageLLMClient`): `TimeoutError` → `model_timeout`; `ProviderUnavailable` → `model_unavailable`; `ModelError` → `model_error`. En los tres casos responde el extractor por reglas con `degraded: ["model_timeout"]` para el cliente. Sin fixture (mock) → reglas sin degradar.

## Caché de prompts

`converse` pone un breakpoint `cache_control` al final del prefijo estable (el último bloque de `system`, o la última herramienta si no hay `system`) y otro en el último bloque del último mensaje, así cada vuelta del loop lee de caché las anteriores (nunca más de 4 en total, contando los del llamador). `complete` cachea el prompt de sistema cuando el archivo tiene la división `<!-- system -->`/`<!-- user -->`. Los mínimos para cachear dependen del modelo (Haiku 4.5: 4096 tokens): por debajo no hay error, solo `cache_read_tokens` en 0. Para que funcione, `system` y `tools` no deben cambiar entre vueltas (nada de fechas ni IDs en `system`).

## Cómo lo usan G1, G2 y G3

- G1 (arturo): ya conectado. `LLM_PROVIDER=bedrock` o `anthropic` → `load_llm_client` → `PackageLLMClient` → `llm.complete("g1_extract", ..., model_role="chat")`.
- G2 (investigador): prompt en `src/prompts/<id>/v1.md`; `llm.converse(system, messages, tools, model_role="investigator")` en su propio loop, con `INVESTIGATOR_MAX_TOOL_CALLS`; el reporte final puede salir de `llm.complete(..., output_schema=..., model_role="investigator")`.
- G3 (juez): `llm.complete(prompt_id, variables, output_schema=..., model_role="judge")`.
- Prompts: ver `src/prompts/README.md` (versiones, variables, `<texto_cliente>`, división system/user, orden de búsqueda `src/prompts/` → `src/conversation/prompts/` → `prompts/`).

## Prueba rápida con un modelo real

Gasta tokens reales (una llamada). Desde la raíz, en Git Bash:

```bash
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... python scripts/llm_smoke.py      # o la clave ya exportada
LLM_PROVIDER=bedrock AWS_REGION=us-east-2 python scripts/llm_smoke.py         # credenciales AWS del entorno
python scripts/llm_smoke.py --provider bedrock --role judge                   # verifica otro modelo
```

Imprime `model_id`, `prompt_version`, tokens, costo estimado, latencia y la salida validada con el esquema de G1. Sin proveedor real o sin credenciales sale con código 2 y lo dice (1: salida inutilizable, 3: timeout). No escribas la clave en archivos del repo.

## Despliegue en AWS

- La Lambda necesita el SDK: `anthropic==2.3.0` en `src/requirements-lambda.txt` (boto3 ya viene en el runtime y firma las llamadas a Bedrock).
- Permiso IAM: el endpoint de Messages de Bedrock usa la acción `bedrock-mantle:CreateInference` sobre los modelos permitidos; `infra/stacks/common.py::allow_bedrock` la concede (recurso `*` limitado a la región de la etapa por condición; `-c bedrockMantleResources=...` lo acota cuando se confirme el formato del ARN) además de `bedrock:InvokeModel*` sobre los modelos de Anthropic y los perfiles de inferencia. Sin ese permiso cada llamada es 403 → `ProviderUnavailable` → G1 cae a reglas.
- Acceso a modelos: Haiku 4.5 y Sonnet 5 están abiertos en Bedrock; Opus 5 puede requerir habilitarlo en la consola (Model access). La región `us-east-2` está soportada.

## Tests

`python -m pytest -q tests/test_llm.py` (sin red ni credenciales: clientes falsos y el SDK real contra un transporte HTTP en memoria) y `tests/test_g1.py` (G1 con `bedrock`/`anthropic` sin credenciales cae a reglas).
