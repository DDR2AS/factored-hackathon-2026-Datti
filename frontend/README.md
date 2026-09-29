# Front de Expediente Vivo (M1)

React 19 + Vite 8 + TypeScript (strict). Sin librerías de UI, estado, router ni i18n. Todo lo que muestra es sintético: clientes demo, transacciones, conversaciones y el portugués (generado sin revisión nativa).

## Cómo correr

```
cd frontend
npm install            # o npm ci desde un clon limpio (Node 24 (o 22.22+))
npm run dev            # Vite en http://localhost:5173; /api/* va tal cual a http://127.0.0.1:8000 (local_api.py quita el prefijo, como CloudFront)
npm test               # vitest (jsdom): cliente API, i18n, componentes
npm run build          # tsc -b + vite build -> dist/ (lo que sube el CDK)
npm run preview        # sirve dist/ con la CSP real y el mismo proxy /api
npx oxlint             # lint
```

La API local va aparte (`scripts/local_api.py --port 8000`). Otra URL: `LOCAL_API_URL=http://127.0.0.1:9000 npm run dev`. Las pruebas estáticas corren desde la raíz con pytest: `python -m pytest -q tests/test_frontend_static.py`.

En `npm run dev` la CSP de `index.html` se quita (Vite necesita scripts y estilos en línea para recargar en caliente); `npm run build` y `npm run preview` la conservan. Antes de desplegar, reemplazar `public/build.txt` (hoy dice `dev`) por el sha y la hora.

## Rutas

`#/` modo juez · `#/chat/<demo_key>` chat con tarjeta en vivo (sondea el caso de ruta B, interruptores del juez) · `#/consola` y `#/consola/<case_id>` consola del analista (entra con el `LOCAL_ANALYST_TOKEN` que imprime `scripts/local_api.py`; Cognito preparado, no habilitado: ver `src/console/auth.ts`) · `#/traza` y `#/traza/<trace_id>` trazas de los turnos de la pestaña (leídas del transcript en sessionStorage, sin ruta nueva) · `#/evaluacion`: "disponible pronto". Rutas hash para no tocar CloudFront.

## Dónde está cada cosa

- `src/api/types.ts`: contrato de la API (fuente de verdad; se cambia junto con el backend, nunca solo aquí).
- `src/api/client.ts`: fetch a `/api/*`, timeout 25 s, reintentos solo ante red, 429, 502, 503, 504 (máximo 2, 1 s y 3 s + jitter) y ante 409 `conflict` marcado reintentable (mismo `client_msg_id`), una sola petición de chat en vuelo, ramifica por status y luego por `error.code`.
- `src/api/config.ts`: header de sesión (`authorization` Bearer; plan B `x-ev-session`), tiempos.
- `src/session.ts`: token en memoria + sessionStorage; transcript en sessionStorage; se borra ante `session_expired` o "Volver a empezar".
- `src/demo/customers.ts`: los 6 clientes demo; `status: 'active' | 'soon'` activa cada tarjeta.
- `src/i18n/es.ts`, `pt.ts`: mismas claves (lo obliga el tipo `Messages`). La UI del chat sigue el idioma del cliente (João en PT).
- `src/styles/tokens.css`: colores, tipografía y espacios; claro por defecto, oscuro con `prefers-color-scheme`.

## Reglas

- Nunca `dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function`; todo texto dinámico se muestra como texto (React lo escapa). Sin markdown.
- Nunca guardar el token en almacenamiento persistente del navegador (localStorage) ni en la URL; en la URL solo `case_id` o `trace_id`.
- Sin URLs externas ni fuentes externas: solo rutas relativas `/api/...`. CSP en `index.html`.
- Ningún modelo decide la ruta A/B/C: la UI solo muestra `lane` y traduce `lane_reason_code`. Nada mueve dinero y ningún texto promete reembolso ni compensación.
- El front nunca envía `customer_id`: solo `demo_key` de la lista permitida del servidor.
- Solo nombre de pila; tarjeta por últimos 4; nunca documento, dirección ni teléfono.
- `tests/test_frontend_static.py` hace cumplir lo anterior (HTML peligroso, eval, localStorage, URLs externas, claves de i18n, frases de reembolso en ES y PT, CSP).
