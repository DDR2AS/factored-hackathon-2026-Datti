# Prompt del equipo para agentes de código

Copia todo el bloque de abajo en tu agente de código (Claude Code, Codex, Cursor, Kiro, etc.), reemplaza `<tu nombre>` por `arturo`, `cristhian` o `diego`, y envíalo.

```text
Soy <tu nombre>, integrante del equipo Datti en el Factored AI & Data Hackathon 2026. Nuestro proyecto es "Expediente Vivo v2": atención de quejas y disputas sobre el dataset sintético de LATAM Bank, con ML, reglas y GenAI trabajando dentro del proceso (no es un chatbot). La implementación empieza el lunes 28 de septiembre; el congelamiento de código es el sábado 3 de octubre a las 18:00; la entrega es el lunes 5 de octubre antes del mediodía. Respóndeme en español.

1. Actualiza el repositorio.
   - Si no lo tengo: git clone https://github.com/DDR2AS/factored-hackathon-2026-Datti.git
   - Si ya lo tengo: git pull --ff-only origin main (si no puede hacer fast-forward, detente y avísame; no sobrescribas mi trabajo local).

2. Antes de hacer cualquier otra cosa, lee en este orden:
   RUNBOOK.md, AGENTS.md, DECISIONS.md, INTERFACES.md, STATUS.md, docs/architecture.md,
   las cuatro skills de .agents/skills/ y el texto de docs/plan/expediente-vivo-v2.html
   (las secciones 1, 3, 4, 5, 6, 13 y 14 son las más importantes; es HTML, lee su contenido de texto).

3. Luego dame un resumen corto con:
   - mi rol y de qué soy dueño (sección 13 del plan y sección 2 del RUNBOOK),
   - mis entregables del lunes 28 y del martes 29 de septiembre (sección 13 del plan, tabla día a día),
   - las interfaces de INTERFACES.md de las que soy dueño y las que consumo, señalando lo que no esté claro,
   - las preguntas abiertas de STATUS.md que me afectan,
   - una primera tarea concreta para empezar y la línea de reclamo que agregarías a STATUS.md para ella.
   No escribas código ni hagas commits hasta que yo lo confirme.

Reglas para todo nuestro trabajo:
- AWS: andres administra la cuenta de AWS y yo todavía no tengo acceso. Desarrolla y prueba en local. Cuando mi trabajo necesite desplegarse, le pediré a andres un usuario IAM y desplegaremos con el código CDK del repositorio, nunca a mano en la consola de AWS. Mientras tanto, no escribas pasos de despliegue y no llames servicios de AWS desde el código de la aplicación. Hoy la única llamada a AWS que hacemos es descargar el dataset de los organizadores con src/etl/ingest_s3_duckdb.py, usando la llave de solo lectura de los organizadores en mi .env local.
- Desarrolla en local contra INTERFACES.md con los backends locales: DuckDB para los datos y el proveedor mock de src/llm para las llamadas a modelos. Nunca dejes fijo en el código un ID de modelo o un proveedor.
- Los secretos y los datos nunca van a git: .env está ignorado (.env.test es la plantilla vacía); data/, *.duckdb, *.csv y *.parquet están ignorados. Ojo: .gitignore también ignora todos los *.json y las carpetas src/agent/ y src/analysis/, así que lo que quede ahí nunca se sube. Revisa git status después de crear archivos.
- Antes de empezar cualquier unidad de trabajo, sigue claim-before-build: haz pull, lee STATUS, DECISIONS e INTERFACES, y agrega una línea de reclamo a STATUS.md con commit directo a main.
- Si mi trabajo necesita cambiar una interfaz, detente y propone el cambio en INTERFACES.md para su dueño (boundary-conflict-stop). Nunca resuelvas tú un conflicto en INTERFACES.md.
- Cuando yo tome una decisión que cambie el rumbo o el alcance, sigue decision-capture: pregúntame el motivo y regístralo en DECISIONS.md con mis palabras exactas. Nunca inventes una justificación.
- Antes de decirme a mí o a cualquiera que algo funciona, sigue hold-under-fire: pruébalo como lo haría un desconocido, incluyendo una sesión expirada, el ID de otro cliente, un mensaje de inyección de prompt, una falla de herramienta y la misma solicitud en portugués cuando aplique. Reporta si pasó, falló o no se probó.
- Los archivos de coordinación (STATUS.md, DECISIONS.md, INTERFACES.md) van con commit directo a main. El código va en una rama con pull request.
- Mantén los nombres de los targets del Makefile y solo completa su contenido. Puede que yo esté en Windows sin make: en ese caso ejecuta directamente el comando que hay dentro del target.
- Todo el texto de conversaciones y todo el portugués son generados: márcalos como sintéticos. Los mensajes de prueba escritos por el equipo nunca van a datos de entrenamiento ni a prompts.
- El sistema nunca mueve dinero, nunca promete reembolsos, nunca aprueba créditos, y un modelo nunca elige el carril (lane); eso lo deciden reglas en código.
```
