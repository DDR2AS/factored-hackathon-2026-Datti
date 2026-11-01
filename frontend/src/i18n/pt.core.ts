// Portuguese UI strings of the first screen. SYNTHETIC: generated without native review (the
// UI says so). Same keys as es.core.ts, enforced by CoreMessages. Loaded only when Portuguese
// is on screen (RNF-11). Never promise refunds or compensation.

import type { CoreMessages } from './es.core'

export const ptCore: CoreMessages = {
  'brand.bank': 'LATAM Bank',
  'brand.demo': 'demo',
  'brand.product': 'Expediente Vivo',
  'brand.home': 'Início do modo juiz',

  'banner.label': 'Aviso de dados sintéticos',
  'banner.synthetic': 'Dados sintéticos · identidade simulada · português gerado sem revisão nativa',
  'footer.note':
    'Demo de hackathon com dados sintéticos. Nada movimenta dinheiro e o sistema não promete devoluções nem compensações: só um analista decide.',
  'footer.build': 'Versão',

  'lang.label': 'Idioma da interface',
  'lang.es': 'Español',
  'lang.pt': 'Português',

  'avail.wed30': 'qua 30',
  'avail.thu1': 'qui 1',
  'avail.fri2': 'sex 2',
  'avail.reserve': 'como reserva do M1',

  'nav.console': 'Console do analista',
  'nav.traces': 'Rastros',
  'nav.evaluation': 'Avaliação',
  'nav.soon': 'disponível {date}',
  'nav.label': 'Outras telas',

  'landing.eyebrow': 'Modo juiz',
  'landing.title': 'Reclamações bancárias com regras, dados verificados e uma pessoa quando precisa',
  'landing.line1':
    'Você conversa como um cliente sintético: o sistema entende a reclamação, procura a cobrança no registro do banco e pede sua confirmação.',
  'landing.line2':
    'Uma regra publicada, e não um modelo, decide a rota: A se explica com o dado, B abre um caso com data prometida e C passa para uma pessoa.',
  'landing.line3': 'Cada turno deixa um rastro visível: qual regra, qual modelo ou ferramenta e quanto demorou.',
  'landing.pick': 'Escolha um cliente',
  'landing.pickHint': 'Um clique abre uma sessão de 15 minutos. Não precisa de conta nem de instruções prévias.',
  'landing.shared':
    'Vários juízes podem usar o mesmo cliente ao mesmo tempo: cada sessão vê só os casos que abriu.',
  'landing.demonstrates': 'O que demonstra',
  'landing.expected': 'Resultado esperado',
  'landing.start': 'Conversar como {name}',
  'landing.soon': 'Disponível {date}',
  'landing.active': 'Ativo',

  'health.checking': 'Verificando a API…',
  'health.starting': 'Iniciando… a primeira chamada acorda o servidor.',
  'health.ok': 'API disponível · etapa {stage}',
  'health.okNoStage': 'API disponível',
  'health.depsMissing':
    'A API responde, mas faltam dependências do backend: o chat não vai funcionar até o próximo deploy.',
  'health.down': 'Não conseguimos contatar a API.',
  'health.retry': 'Tentar de novo',

  'customer.lucia.demonstrates':
    'Caminho normal: descreve uma cobrança com as próprias palavras ("como 450 en el súper el 12"); o sistema encontra a cobrança exata e pede confirmação.',
  'customer.lucia.expected':
    'Se responder "No lo reconozco": rota B, caso EV-… com data prometida e primeira resposta. Se reconhecer: rota A.',
  'customer.sofia.demonstrates':
    'Mensagem ambígua ("me cobraron algo raro"): escolhe entre três cobranças, com "Nenhuma" e "Falar com uma pessoa"; depois pede um empréstimo, que está fora do escopo.',
  'customer.sofia.expected':
    'Nada é aberto sem a confirmação dela: escolhe a cobrança e, se não a reconhece, rota B. O empréstimo é encaminhado sem prometer nada.',
  'customer.andres.name': 'Andrés (cliente de demonstração)',
  'customer.andres.demonstrates': 'Duas cobranças idênticas com minutos de diferença que não foram estornadas.',
  'customer.andres.expected':
    'Rota B por cobrança duplicada não estornada; o investigador documenta e um analista aprova.',
  'customer.joao.demonstrates': 'Conversa inteira em português: uma tarifa que não bate com a tabela publicada.',
  'customer.joao.expected':
    'Rota B por tarifa que não bate; nunca se pergunta se ele reconhece a cobrança. Modelos de texto, botões e cartão em PT.',
  'customer.martina.demonstrates':
    'Cobrança com score de fraude alto: o sistema oferece bloquear o cartão e só bloqueia com um "sim" explícito.',
  'customer.martina.expected':
    'Rota C rápida por score de fraude 82 (regra high_fraud_score), fila de fraude; bloqueio só com "sim" e leitura do novo estado.',
  'customer.carlos.demonstrates': 'Três reclamações anteriores e menciona o regulador.',
  'customer.carlos.expected': 'Rota C com prioridade alta e uma transferência estruturada para uma pessoa.',

  'app.skip': 'Pular para o conteúdo',
  'app.loadFailed': 'Não foi possível carregar esta tela (talvez haja uma versão nova publicada).',
  'app.reload': 'Recarregar',
  'chat.opening': 'Abrindo uma sessão segura…',

  'lane.label': 'Rota {lane}',
  'lane.A': 'se explica com o dado',
  'lane.B': 'caso com data prometida',
  'lane.C': 'passa para uma pessoa',
  'console.opening': 'Abrindo…',

  'soon.title': 'Disponível em breve',
  'soon.body': '{name} será construído em {date} sobre o mesmo contrato da API.',
  'soon.back': 'Voltar ao modo juiz',
  'notFound.title': 'Não encontramos essa página',
}
