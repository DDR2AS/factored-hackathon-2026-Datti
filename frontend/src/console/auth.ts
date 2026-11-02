// Analyst sign-in for the console (#/consola).
//
// The token lives in memory plus sessionStorage (per tab, survives a reload, gone when the tab
// closes). Never in the URL, never in persistent browser storage, never in logs. Separate from
// the customer session token (session.ts): the console never sends the customer's token.
//
// Two providers behind one interface:
// - localTokenProvider: the analyst pastes the LOCAL_ANALYST_TOKEN that scripts/local_api.py
//   prints when it starts. local_api.py checks it and injects the claims {sub, email}; src/ has
//   no bypass.
// - cognitoPasswordProvider (cloud): InitiateAuth with USER_PASSWORD_AUTH through fetch, no SRP
//   library, against the `analysts` pool of infra/stacks/api_stack.py (the CDK enables that
//   flow on the app client). The pool ids come from /config.json, which the Web stack publishes
//   next to the site (infra/stacks/web_stack.py: cognitoRegion, userPoolId, userPoolClientId);
//   where there is none (local dev) the provider is not available. The session token is the ID
//   token: the JWT authorizer of /analyst/* validates its audience (= the app client) and
//   src/handlers/api.py reads its `sub` and `email` claims (an access token has no email). It
//   lasts 1 h (id_token_validity) and there is no refresh: on 401 the console asks to sign in
//   again without losing the draft in memory. The endpoint host must be in the CSP connect-src
//   of index.html (cognito-idp.us-east-2.amazonaws.com; another region means editing it).

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
  /** Whether this provider can sign in on this site (Cognito: only where /config.json names a pool). */
  available(): Promise<boolean>
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
  async available() {
    return true
  },
  async signIn(input) {
    if (input.kind !== 'local') throw new AuthNotAvailableError('local provider takes a token')
    const token = input.token.trim()
    if (!token || /\s/.test(token) || token.length > 4096) throw new AuthNotAvailableError('invalid token format')
    return { token, provider: 'local', signed_in_at: new Date().toISOString() }
  },
}

// ---------------------------------------------------------------- Cognito (cloud)

/** What the Web stack writes to /config.json (infra/stacks/web_stack.py). */
export interface CognitoConfig {
  region: string
  userPoolId: string
  userPoolClientId: string
}

export type CognitoSignInReason =
  /** NotAuthorizedException or UserNotFoundException (also an empty field). */
  | 'bad_credentials'
  /** The user still has the temporary password of admin-create-user. */
  | 'new_password_required'
  /** PasswordResetRequiredException or UserNotConfirmedException. */
  | 'password_reset'
  /** TooManyRequestsException or LimitExceededException. */
  | 'rate_limited'
  /** fetch failed: offline, DNS, or the CSP does not allow the host. */
  | 'network'
  /** No /config.json on this site. */
  | 'not_configured'
  /** Any other answer; `detail` carries Cognito's exception type, challenge or HTTP status. */
  | 'unavailable'

/** Never carries the username, the password or a token: it may end up on screen or in a log. */
export class CognitoSignInError extends Error {
  readonly reason: CognitoSignInReason
  readonly detail: string
  constructor(reason: CognitoSignInReason, detail = '') {
    super(detail ? `${reason}: ${detail}` : reason)
    this.name = 'CognitoSignInError'
    this.reason = reason
    this.detail = detail
  }
}

// Shapes Cognito uses (us-east-2, us-east-2_AbC123xyz, 26 lowercase alphanumerics). Anything else
// in config.json is treated as "no pool": the sign-in URL is built from the region.
const REGION_RE = /^[a-z]{2}(?:-[a-z]+)+-\d$/
const POOL_ID_RE = /^[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]{1,64}$/
const CLIENT_ID_RE = /^[a-z0-9]{1,128}$/

export function parseCognitoConfig(raw: unknown): CognitoConfig | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const region = typeof r.cognitoRegion === 'string' ? r.cognitoRegion : ''
  const userPoolId = typeof r.userPoolId === 'string' ? r.userPoolId : ''
  const userPoolClientId = typeof r.userPoolClientId === 'string' ? r.userPoolClientId : ''
  if (!REGION_RE.test(region) || !POOL_ID_RE.test(userPoolId) || !CLIENT_ID_RE.test(userPoolClientId)) return null
  return { region, userPoolId, userPoolClientId }
}

export interface CognitoDeps {
  /** Defaults to the global fetch (a relative /config.json resolves against the page). */
  fetch?: typeof fetch
  /** Where the site publishes its runtime settings. */
  configPath?: string
}

export interface CognitoAuthProvider extends AuthProvider {
  /** The pool of this site, read once from /config.json; null where there is none (local). */
  loadConfig(): Promise<CognitoConfig | null>
  /** Settings already known (or known to be absent): skips the fetch. Tests use it. */
  primeConfig(config: CognitoConfig | null | undefined): void
}

type JsonObject = Record<string, unknown>

async function jsonBody(res: Response): Promise<JsonObject | null> {
  try {
    const body: unknown = await res.json()
    return body && typeof body === 'object' ? (body as JsonObject) : null
  } catch {
    return null
  }
}

/** Cognito answers errors with {"__type": "NotAuthorizedException", "message": "..."}; the type
 *  may carry a namespace prefix ("com.amazonaws...#NotAuthorizedException"). */
function errorFor(status: number, body: JsonObject | null): CognitoSignInError {
  const type = typeof body?.__type === 'string' ? (body.__type.split('#').pop() ?? '') : ''
  switch (type) {
    case 'NotAuthorizedException':
    case 'UserNotFoundException':
      return new CognitoSignInError('bad_credentials')
    case 'PasswordResetRequiredException':
    case 'UserNotConfirmedException':
      return new CognitoSignInError('password_reset')
    case 'TooManyRequestsException':
    case 'LimitExceededException':
      return new CognitoSignInError('rate_limited')
    default:
      return new CognitoSignInError('unavailable', type || `HTTP ${status}`)
  }
}

export function createCognitoPasswordProvider(deps: CognitoDeps = {}): CognitoAuthProvider {
  const doFetch: typeof fetch = deps.fetch ?? ((input, init) => globalThis.fetch(input, init))
  const configPath = deps.configPath ?? '/config.json'
  let config: CognitoConfig | null | undefined
  let loading: Promise<CognitoConfig | null> | null = null

  async function fetchConfig(): Promise<CognitoConfig | null> {
    try {
      const res = await doFetch(configPath, { headers: { accept: 'application/json' } })
      if (res.status === 404) return (config = null) // this site has no pool (local build)
      if (!res.ok) return null // not remembered: a 5xx may clear up on the next try
      return (config = parseCognitoConfig(await jsonBody(res)))
    } catch {
      return null // offline, or not a browser; not remembered either
    }
  }

  const provider: CognitoAuthProvider = {
    kind: 'cognito',
    loadConfig() {
      if (config !== undefined) return Promise.resolve(config)
      if (!loading) {
        loading = fetchConfig().finally(() => {
          loading = null
        })
      }
      return loading
    },
    primeConfig(c) {
      config = c
    },
    async available() {
      return (await provider.loadConfig()) !== null
    },
    async signIn(input) {
      if (input.kind !== 'cognito') throw new AuthNotAvailableError('cognito provider takes a username and a password')
      const cfg = await provider.loadConfig()
      if (!cfg) throw new CognitoSignInError('not_configured')
      const username = input.username.trim()
      if (!username || !input.password) throw new CognitoSignInError('bad_credentials')
      let res: Response
      try {
        res = await doFetch(`https://cognito-idp.${cfg.region}.amazonaws.com/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-amz-json-1.1',
            'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
          },
          body: JSON.stringify({
            AuthFlow: 'USER_PASSWORD_AUTH',
            ClientId: cfg.userPoolClientId,
            AuthParameters: { USERNAME: username, PASSWORD: input.password },
          }),
        })
      } catch {
        throw new CognitoSignInError('network')
      }
      const body = await jsonBody(res)
      if (!res.ok) throw errorFor(res.status, body)
      const challenge = typeof body?.ChallengeName === 'string' ? body.ChallengeName : ''
      if (challenge === 'NEW_PASSWORD_REQUIRED') throw new CognitoSignInError('new_password_required')
      if (challenge) throw new CognitoSignInError('unavailable', challenge)
      const result = body?.AuthenticationResult
      const idToken = result && typeof result === 'object' ? (result as JsonObject).IdToken : undefined
      if (typeof idToken !== 'string' || !idToken) throw new CognitoSignInError('unavailable', 'no id token')
      return { token: idToken, provider: 'cognito', signed_in_at: new Date().toISOString() }
    },
  }
  return provider
}

/** The site's Cognito pool, if /config.json names one. */
export const cognitoPasswordProvider: CognitoAuthProvider = createCognitoPasswordProvider()

export const AUTH_PROVIDERS: readonly AuthProvider[] = [localTokenProvider, cognitoPasswordProvider]

// ---------------------------------------------------------------- session storage

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
