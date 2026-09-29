// Customer-facing texts the SERVER sends after an analyst decides, copied here only so the
// console can show the analyst the exact text before confirming (CX-02). They are in the
// CASE language, not the console's. The source of truth is src/conversation/templates/*.yaml
// (resolution_stamp, request_information_default) and analyst._stamp; tests/test_frontend_static.py
// fails if these copies drift from the templates.

import type { Language } from '../api/types'

type Register = 'tu' | 'usted' | 'vos'

const REGISTER_BY_COUNTRY: Record<string, Register> = { MX: 'tu', CO: 'usted', AR: 'vos' }

export const RESOLUTION_STAMP: Record<Language, string> = {
  es: 'Respuesta aprobada por una persona del equipo. En esta demo ningún dinero se mueve.',
  pt: 'Resposta aprovada por uma pessoa da equipe. Nesta demonstração nenhum dinheiro é movimentado.',
}

export const REQUEST_INFORMATION_DEFAULT: { es: Record<Register, string>; pt: string } = {
  es: {
    tu: 'Para avanzar con tu caso {case_id} necesitamos un dato más. Respóndenos por este chat y lo agregamos a tu caso.',
    usted: 'Para avanzar con su caso {case_id} necesitamos un dato más. Respóndanos por este chat y lo agregamos a su caso.',
    vos: 'Para avanzar con tu caso {case_id} necesitamos un dato más. Respondenos por este chat y lo agregamos a tu caso.',
  },
  pt: 'Para avançar com o seu caso {case_id} precisamos de mais uma informação. Responda por este chat e nós a adicionamos ao seu caso.',
}

/** The template the server sends when "Pedir información" goes without a written question. */
export function requestInformationDefault(language: Language, country: string | null | undefined, caseId: string): string {
  const template =
    language === 'pt' ? REQUEST_INFORMATION_DEFAULT.pt : REQUEST_INFORMATION_DEFAULT.es[REGISTER_BY_COUNTRY[country ?? ''] ?? 'tu']
  return template.replace('{case_id}', caseId)
}

/** Same rule as analyst._stamp: trimmed text, then the stamp unless it already ends with it. */
export function stamped(text: string, language: Language): string {
  const stamp = RESOLUTION_STAMP[language]
  const t = text.trim()
  return t.endsWith(stamp) ? t : `${t} ${stamp}`
}
