// Spanish UI strings of the first screen (landing, page chrome, loading and error states).
// They ship in the initial bundle; everything else is in es.ts, which loads with the chat,
// console or trace view (RNF-11). pt.core.ts must have exactly the same keys (CoreMessages).
// Placeholders use {name}. Never promise refunds or compensation in any string.

export const esCore = {
  'brand.bank': 'LATAM Bank',
  'brand.demo': 'demo',
  'brand.product': 'Expediente Vivo',
  'brand.home': 'Inicio del modo juez',

  'banner.label': 'Aviso de datos sintéticos',
  'banner.synthetic': 'Datos sintéticos · identidad simulada · portugués generado sin revisión nativa',
  'footer.note':
    'Demo de hackathon con datos sintéticos. Nada mueve dinero y el sistema no promete devoluciones ni compensaciones: solo un analista decide.',
  'footer.build': 'Versión',

  'lang.label': 'Idioma de la interfaz',
  'lang.es': 'Español',
  'lang.pt': 'Português',

  'avail.wed30': 'mié 30',
  'avail.thu1': 'jue 1',
  'avail.fri2': 'vie 2',
  'avail.reserve': 'como reserva de M1',

  'nav.console': 'Consola del analista',
  'nav.traces': 'Trazas',
  'nav.evaluation': 'Evaluación',
  'nav.soon': 'disponible {date}',
  'nav.label': 'Otras vistas',

  'landing.eyebrow': 'Modo juez',
  'landing.title': 'Quejas bancarias con reglas, datos verificados y una persona cuando hace falta',
  'landing.line1':
    'Conversas como un cliente sintético: el sistema entiende la queja, busca el cargo en el registro del banco y te pide confirmarlo.',
  'landing.line2':
    'Una regla publicada, no un modelo, decide la ruta: A se explica con el dato, B abre un caso con fecha prometida y C pasa a una persona.',
  'landing.line3': 'Cada turno deja una traza visible: qué regla, qué modelo o herramienta y cuánto tardó.',
  'landing.pick': 'Elige un cliente',
  'landing.pickHint': 'Un clic abre una sesión de 15 minutos. No hace falta cuenta ni instrucciones previas.',
  'landing.shared':
    'Varios jueces pueden usar el mismo cliente a la vez: cada sesión ve solo los casos que abrió.',
  'landing.demonstrates': 'Qué demuestra',
  'landing.expected': 'Resultado esperado',
  'landing.start': 'Conversar como {name}',
  'landing.soon': 'Disponible {date}',
  'landing.active': 'Activo',

  'health.checking': 'Comprobando la API…',
  'health.starting': 'Iniciando… la primera llamada despierta el servidor.',
  'health.ok': 'API disponible · etapa {stage}',
  'health.okNoStage': 'API disponible',
  'health.depsMissing':
    'La API responde, pero le faltan dependencias del backend: el chat no funcionará hasta el próximo despliegue.',
  'health.down': 'No pudimos contactar la API.',
  'health.retry': 'Reintentar',

  'customer.lucia.demonstrates':
    'Camino normal: describe un cargo con sus palabras ("como 450 en el súper el 12"); el sistema encuentra el cargo exacto y le pide confirmarlo.',
  'customer.lucia.expected':
    'Si responde "No lo reconozco": ruta B, caso EV-… con fecha prometida y primera respuesta. Si lo reconoce: ruta A.',
  'customer.sofia.demonstrates':
    'Mensaje ambiguo ("me cobraron algo raro"): elige entre tres cargos, con "Ninguno" y "Hablar con una persona"; luego pide un préstamo, que está fuera de alcance.',
  'customer.sofia.expected':
    'Nada se abre sin su confirmación: elige el cargo y, si no lo reconoce, ruta B. El préstamo se deriva sin prometer nada.',
  'customer.andres.name': 'Andrés (cliente demo)',
  'customer.andres.demonstrates': 'Dos cargos idénticos con minutos de diferencia que no se revirtieron.',
  'customer.andres.expected': 'Ruta B por cargo duplicado no revertido; el investigador lo documenta y un analista aprueba.',
  'customer.joao.demonstrates': 'Conversación completa en portugués: una comisión que no coincide con la tabla publicada.',
  'customer.joao.expected':
    'Ruta B por comisión que no coincide; nunca se le pregunta si reconoce el cargo. Plantillas, botones y tarjeta en PT.',
  'customer.martina.demonstrates':
    'Cargo con score de fraude alto: el sistema ofrece bloquear la tarjeta y solo lo hace con un "sí" explícito.',
  'customer.martina.expected':
    'Ruta C rápida por score de fraude 82 (regla high_fraud_score), cola de fraude; bloqueo solo con "sí" y lectura del nuevo estado.',
  'customer.carlos.demonstrates': 'Tres quejas previas y menciona al regulador.',
  'customer.carlos.expected': 'Ruta C con prioridad alta y un traspaso estructurado a una persona.',

  'app.skip': 'Saltar al contenido',
  'app.loadFailed': 'No se pudo cargar esta vista (quizás hay una versión nueva publicada).',
  'app.reload': 'Recargar',
  'chat.opening': 'Abriendo una sesión segura…',

  'lane.label': 'Ruta {lane}',
  'lane.A': 'se explica con el dato',
  'lane.B': 'caso con fecha prometida',
  'lane.C': 'pasa a una persona',
  'console.opening': 'Abriendo…',

  'soon.title': 'Disponible pronto',
  'soon.body': '{name} se construye el {date} sobre el mismo contrato de la API.',
  'soon.back': 'Volver al modo juez',
  'notFound.title': 'No encontramos esa página',
}

export type CoreKey = keyof typeof esCore
export type CoreMessages = { readonly [K in CoreKey]: string }
