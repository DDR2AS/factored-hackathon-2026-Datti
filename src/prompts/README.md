# Prompts del cliente #8 (`src/llm`)

Carpeta común para los prompts de G2 (investigador), G3 (juez) y la línea base B2 de M2 (`m2_zero_shot`, clasificación sin entrenamiento). El de G1 sigue en `src/conversation/prompts/g1_extract/`. Están bajo `src/` porque la Lambda solo despliega `src/`.

- Un prompt es una carpeta `src/prompts/<prompt_id>/` con un archivo por versión: `v1.md`, `v2.md`… El `prompt_id` va en minúsculas, dígitos y `_`.
- `llm.complete(prompt_id, variables, output_schema=None, model_role=...)` usa la versión más alta. `prompt_version` es `<prompt_id>@v<n>` y llega al trace y al reporte. Una versión publicada no se edita: se crea la siguiente.
- El primer comentario `<!-- ... -->` es la cabecera (prompt_id, versión, dueño, fecha, rol, variables). Todos los comentarios HTML se quitan antes de enviar.
- Variables: `{{nombre}}` se reemplaza por `str(valor)`; una que falta queda vacía. Los datos estructurados se pasan ya serializados (`json.dumps`). `{{output_schema}}` es el esquema JSON que recibe `complete` (con structured outputs el modelo ya está obligado a cumplirlo).
- Texto del cliente: va en la variable `text`, dentro de `<texto_cliente>…</texto_cliente>`, y el prompt lo declara como DATO, nunca instrucciones. Las copias de esas etiquetas dentro del texto se quitan solas. Otros textos del cliente se pasan antes por `llm.as_data`.
- System + user (opcional): una línea `<!-- system -->` y otra `<!-- user -->`. Lo que está entre ambas va como prompt de sistema, con caché; lo que sigue a `<!-- user -->` va como mensaje del usuario. Sin marcas, todo el archivo es el mensaje del usuario. Conviene dejar en system las instrucciones fijas y en user los datos del caso.
- Orden de búsqueda: `src/prompts/` → `src/conversation/prompts/` → `prompts/` en la raíz (esta última no se despliega). Gana la primera carpeta que tiene el `prompt_id`.
- Sin IDs de modelo ni secretos. Los mensajes de prueba escritos por el equipo nunca van a un prompt (D7); los ejemplos son sintéticos y lo dicen.
- Guía del cliente: `docs/llm_client.md`.
