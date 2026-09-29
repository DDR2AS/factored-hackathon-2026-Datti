// Analyst sign-in for the console (#/consola).
//
// The token lives in memory plus sessionStorage (per tab, survives a reload, gone when the tab
// closes). Never in the URL, never in persistent browser storage, never in logs. Separate from
// the customer session token (session.ts): the console never sends the customer's token.
//
// Two providers behind one interface:
// - LocalTokenProvider (works today): the analyst pastes the LOCAL_ANALYST_TOKEN that
//   scripts/local_api.py prints when it starts. local_api.py checks it and injects the claims
//   {sub, email}; src/ has no bypass.
// - CognitoPasswordProvider (PREPARED, NOT IMPLEMENTED): InitiateAuth with USER_PASSWORD_AUTH
//   through fetch, no SRP library. Needs Andrés to (1) enable USER_PASSWORD_AUTH on the app
//   client of the `analysts` pool, (2) create a demo analyst user whose credentials travel only
//   with the submission, never in the repo, and (3) add cognito-idp.<region>.amazonaws.com to
//   the CSP connect-src and to ALLOWED_URLS in tests/test_frontend_static.py. Tokens last 1 h;
//   when one expires the console asks to sign in again without losing the draft in memory.

const ANALYST_KEY = 'ev.analyst.v1'

export type AuthProviderKind = 'local' | 'cognito'

export interface AnalystSession {
  token: string
  provider: AuthProviderKind
  /** ISO time the token was accepted; the server decides whether it is still valid. */
  signed_in_at: string
}

export type SignInInput = { kind: 'local'; token: string } | { kind: 'cognito'; username: string; password: string }

export interface AuthProvider {
  readonly kind: AuthProviderKind
  /** False when the provider exists only as a prepared interface. */
  readonly available: boolean
  signIn(input: SignInInput): Promise<AnalystSession>
}

export class AuthNotAvailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AuthNotAvailableError'
  }
}

/** Token pasted by the analyst. The first API call tells whether it is right (401 if not). */
export const localTokenProvider: AuthProvider = {
  kind: 'local',
  available: true,
  async signIn(input) {
    if (input.kind !== 'local') throw new AuthNotAvailableError('local provider takes a token')
    const token = input.token.trim()
    if (!token || /\s/.test(token) || token.length > 4096) throw new AuthNotAvailableError('invalid token format')
    return { token, provider: 'local', signed_in_at: new Date().toISOString() }
  },
}

/** Prepared for the cloud; see the header of this file for what Andrés has to enable first. */
export const cognitoPasswordProvider: AuthProvider = {
  kind: 'cognito',
  available: false,
  async signIn() {
    throw new AuthNotAvailableError('Cognito sign-in is not enabled yet (USER_PASSWORD_AUTH + demo user)')
  },
}

export const AUTH_PROVIDERS: readonly AuthProvider[] = [localTokenProvider, cognitoPasswordProvider]

let memory: AnalystSession | null = null

export function saveAnalystSession(s: AnalystSession): void {
  memory = s
  try {
    window.sessionStorage.setItem(ANALYST_KEY, JSON.stringify(s))
  } catch {
    // Storage blocked: the session still works in memory for this page view.
  }
}

export function loadAnalystSession(): AnalystSession | null {
  if (memory) return memory
  try {
    const raw = window.sessionStorage.getItem(ANALYST_KEY)
    const s = raw ? (JSON.parse(raw) as AnalystSession) : null
    if (s && typeof s.token === 'string' && s.token) {
      memory = s
      return s
    }
  } catch {
    // ignore
  }
  return null
}

export function getAnalystToken(): string | null {
  return loadAnalystSession()?.token ?? null
}

/** Sign out, or drop a token the server rejected (401). Drafts in memory are kept. */
export function clearAnalystSession(): void {
  memory = null
  try {
    window.sessionStorage.removeItem(ANALYST_KEY)
  } catch {
    // ignore
  }
}
