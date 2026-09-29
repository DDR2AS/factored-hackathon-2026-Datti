// fetch wrapper for /api/* (contract: ./types.ts, INTERFACES.md #1).
// - Relative paths only; the browser sees one origin (CloudFront or the Vite proxy).
// - Timeout per attempt with AbortController.
// - Automatic retries only on network error, 429, 502, 503, 504 (and a 409 conflict that the
//   server marks retryable, repeating the same client_msg_id while the server still processes
//   it, up to conflictMaxWaitMs). Never on 400/401/403/409 limit.
// - Errors are classified by HTTP status first and then by error.code, because API Gateway
//   answers throttling, route 404 and its own 5xx as {"message": ...} without a code.
// - At most one POST /chat in flight. The caller can abort it (leaving the chat), which frees
//   the slot at once and stops the automatic retries.

import { apiConfig, type ApiConfig } from './config'
import { ApiClientError, isApiClientError } from './errors'
import type {
  AnalystCaseDetail,
  AnalystCaseListQuery,
  AnalystCaseListResponse,
  CaseResponse,
  ChatRequest,
  ChatResponse,
  DecisionRequest,
  DecisionResponse,
  ErrorCode,
  HealthResponse,
  SessionRequest,
  SessionResponse,
} from './types'

export { ApiClientError, isApiClientError, type ClientErrorCode } from './errors'


const KNOWN_CODES: readonly ErrorCode[] = [
  'invalid_request',
  'session_expired',
  'not_authorized',
  'conflict',
  'session_limit',
  'rate_limited',
  'tool_unavailable',
  'model_timeout',
  'not_implemented',
  'precondition',
  'internal',
]

const RETRY_STATUSES = new Set([429, 502, 503, 504])

export interface ApiClientDeps {
  fetch?: typeof fetch
  sleep?: (ms: number) => Promise<void>
  random?: () => number
  getToken?: () => string | null
  /** Analyst bearer (Cognito id token in the cloud, LOCAL_ANALYST_TOKEN locally). Separate
   *  from the customer session token: /analyst/* never sees the customer's token. */
  getAnalystToken?: () => string | null
  config?: Partial<ApiConfig>
}

export interface RequestControl {
  /** Aborting it cancels the current attempt and any pending automatic retry ('aborted'). */
  signal?: AbortSignal
  /** Pollers set it: one attempt only, the poller applies its own backoff (5 -> 10 -> 20 -> 60 s). */
  noRetry?: boolean
}

export interface ApiClient {
  health(): Promise<HealthResponse>
  createSession(req: SessionRequest): Promise<SessionResponse>
  chat(req: ChatRequest, control?: RequestControl): Promise<ChatResponse>
  getCase(caseId: string, control?: RequestControl): Promise<CaseResponse>
  isChatInFlight(): boolean
  // Analyst console (docs/contrato_consola.md). Always `Authorization: Bearer <analyst token>`:
  // the API Gateway JWT authorizer reads that header, so there is no x-ev-session plan B here.
  listAnalystCases(query?: AnalystCaseListQuery, control?: RequestControl): Promise<AnalystCaseListResponse>
  getAnalystCase(caseId: string, control?: RequestControl): Promise<AnalystCaseDetail>
  /** Safe to retry with the same client_decision_id and body (the server returns the stored answer). */
  decide(caseId: string, req: DecisionRequest, control?: RequestControl): Promise<DecisionResponse>
}

/** Query string for GET /analyst/cases; only the keys that are set, in a fixed order. */
export function analystListPath(query: AnalystCaseListQuery = {}): string {
  const params = new URLSearchParams()
  for (const key of ['status', 'lane', 'language', 'limit', 'cursor'] as const) {
    const v = query[key]
    if (v !== undefined && v !== null && v !== '') params.set(key, String(v))
  }
  const qs = params.toString()
  return qs ? `/analyst/cases?${qs}` : '/analyst/cases'
}

/** New idempotency key for POST /chat. Reuse it when repeating the same message. */
export function newClientMsgId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  // randomUUID needs a secure context; fall back to getRandomValues (RFC 4122 v4 layout).
  const b = new Uint8Array(16)
  c.getRandomValues(b)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

interface ParsedBody {
  code: ErrorCode | null
  message: string | null
  retryable: boolean | null
  fromGateway: boolean
}

function parseErrorBody(text: string): ParsedBody {
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (body && typeof body === 'object') {
    const err = (body as { error?: unknown }).error
    if (err && typeof err === 'object') {
      const e = err as { code?: unknown; message?: unknown; retryable?: unknown }
      const code =
        typeof e.code === 'string' && (KNOWN_CODES as readonly string[]).includes(e.code)
          ? (e.code as ErrorCode)
          : null
      return {
        code,
        message: typeof e.message === 'string' ? e.message : null,
        retryable: typeof e.retryable === 'boolean' ? e.retryable : null,
        fromGateway: false,
      }
    }
    const msg = (body as { message?: unknown }).message
    if (typeof msg === 'string') return { code: null, message: msg, retryable: null, fromGateway: true }
  }
  return { code: null, message: null, retryable: null, fromGateway: false }
}

/** Status first, then error.code. */
export function classifyHttpError(status: number, text: string): ApiClientError {
  const b = parseErrorBody(text)
  const base = { status, fromGateway: b.fromGateway, message: b.message ?? `HTTP ${status}` }
  switch (status) {
    case 422:
      // 422 precondition: the analyst decision does not fit the case state (docs/contrato_consola.md).
      if (b.code === 'precondition') {
        return new ApiClientError({ ...base, code: 'precondition', retryable: false })
      }
      return new ApiClientError({ ...base, code: 'invalid_request', retryable: false })
    case 400:
    case 413:
      return new ApiClientError({ ...base, code: 'invalid_request', retryable: false })
    case 401:
      return new ApiClientError({ ...base, code: 'session_expired', retryable: false })
    case 403:
      return new ApiClientError({ ...base, code: 'not_authorized', retryable: false })
    case 404:
      return new ApiClientError({ ...base, code: b.code ?? 'not_found', retryable: false })
    case 409:
      if (b.code === 'session_limit') {
        return new ApiClientError({ ...base, code: 'session_limit', retryable: false })
      }
      return new ApiClientError({ ...base, code: 'conflict', retryable: b.retryable === true })
    case 429:
      return new ApiClientError({ ...base, code: 'rate_limited', retryable: true })
    case 501:
      return new ApiClientError({ ...base, code: 'not_implemented', retryable: false })
    default:
      if (status >= 500) {
        const code = b.code === 'tool_unavailable' || b.code === 'model_timeout' ? b.code : 'internal'
        return new ApiClientError({ ...base, code, retryable: true })
      }
      return new ApiClientError({ ...base, code: b.code ?? 'unexpected', retryable: false })
  }
}

interface RequestOptions {
  method: 'GET' | 'POST'
  path: string
  body?: unknown
  auth: false | 'session' | 'analyst'
  signal?: AbortSignal
  noRetry?: boolean
}

function abortedError(): ApiClientError {
  return new ApiClientError({ code: 'aborted', status: null, retryable: false, message: 'aborted by the caller' })
}

export function createApiClient(deps: ApiClientDeps = {}): ApiClient {
  const cfg: ApiConfig = { ...apiConfig, ...deps.config }
  const doFetch: typeof fetch = deps.fetch ?? ((input, init) => globalThis.fetch(input, init))
  const sleep = deps.sleep ?? defaultSleep
  const random = deps.random ?? Math.random
  const getToken = deps.getToken ?? (() => null)
  const getAnalystToken = deps.getAnalystToken ?? (() => null)
  let chatInFlight = false

  function analystHeaders(): Record<string, string> {
    const token = getAnalystToken()
    if (!token) {
      throw new ApiClientError({
        code: 'session_expired',
        status: null,
        retryable: false,
        message: 'no analyst token',
        attempts: 0,
      })
    }
    return { authorization: `Bearer ${token}` }
  }

  function authHeaders(): Record<string, string> {
    const token = getToken()
    if (!token) {
      throw new ApiClientError({
        code: 'session_expired',
        status: null,
        retryable: false,
        message: 'no session token',
        attempts: 0,
      })
    }
    return cfg.sessionHeader === 'authorization'
      ? { authorization: `Bearer ${token}` }
      : { 'x-ev-session': token }
  }

  async function attempt<T>(opts: RequestOptions): Promise<T> {
    const headers: Record<string, string> = { accept: 'application/json' }
    if (opts.body !== undefined) headers['content-type'] = 'application/json'
    if (opts.auth === 'session') Object.assign(headers, authHeaders())
    else if (opts.auth === 'analyst') Object.assign(headers, analystHeaders())

    if (opts.signal?.aborted) throw abortedError()
    const controller = new AbortController()
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, cfg.timeoutMs)
    const onCallerAbort = () => controller.abort()
    opts.signal?.addEventListener('abort', onCallerAbort, { once: true })

    let res: Response
    try {
      res = await doFetch(cfg.basePath + opts.path, {
        method: opts.method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'same-origin',
      })
    } catch {
      clearTimeout(timer)
      opts.signal?.removeEventListener('abort', onCallerAbort)
      if (opts.signal?.aborted) throw abortedError()
      if (timedOut) {
        throw new ApiClientError({ code: 'timeout', status: null, retryable: true, message: 'timeout' })
      }
      throw new ApiClientError({ code: 'network', status: null, retryable: true, message: 'network error' })
    }

    try {
      const text = await res.text()
      if (!res.ok) throw classifyHttpError(res.status, text)
      try {
        return JSON.parse(text) as T
      } catch {
        throw new ApiClientError({
          code: 'unexpected',
          status: res.status,
          retryable: true,
          message: 'invalid JSON in response',
        })
      }
    } catch (e) {
      if (isApiClientError(e)) throw e
      if (opts.signal?.aborted) throw abortedError()
      if (timedOut) {
        throw new ApiClientError({ code: 'timeout', status: null, retryable: true, message: 'timeout' })
      }
      throw new ApiClientError({ code: 'network', status: null, retryable: true, message: 'network error' })
    } finally {
      clearTimeout(timer)
      opts.signal?.removeEventListener('abort', onCallerAbort)
    }
  }

  /** Sleeps, but wakes up early (as 'aborted') if the caller aborts. */
  async function pause(ms: number, signal?: AbortSignal): Promise<void> {
    if (!signal) return sleep(ms)
    if (signal.aborted) throw abortedError()
    let onAbort: () => void = () => {}
    const aborted = new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(abortedError())
      signal.addEventListener('abort', onAbort, { once: true })
    })
    try {
      await Promise.race([sleep(ms), aborted])
    } finally {
      signal.removeEventListener('abort', onAbort)
    }
  }

  function shouldAutoRetry(e: ApiClientError): boolean {
    if (e.code === 'network') return true
    return e.status !== null && RETRY_STATUSES.has(e.status)
  }

  async function request<T>(opts: RequestOptions): Promise<T> {
    const maxRetries = opts.noRetry ? 0 : cfg.retryDelaysMs.length
    let retries = 0
    let conflictRetries = 0
    let conflictWaitedMs = 0
    for (let attemptNo = 1; ; attemptNo++) {
      try {
        return await attempt<T>(opts)
      } catch (err) {
        const e = isApiClientError(err)
          ? err
          : new ApiClientError({ code: 'unexpected', status: null, retryable: true, message: String(err) })
        if (e.code === 'aborted') throw e
        // The same client_msg_id is still being processed: wait for it, not for a new turn.
        if (!opts.noRetry && e.code === 'conflict' && e.retryable && conflictWaitedMs < cfg.conflictMaxWaitMs) {
          const base = Math.min(cfg.conflictRetryDelayMs * 2 ** conflictRetries, 5_000)
          const delay = Math.min(
            base + Math.floor(random() * cfg.retryJitterMs),
            cfg.conflictMaxWaitMs - conflictWaitedMs,
          )
          conflictRetries++
          conflictWaitedMs += delay
          await pause(delay, opts.signal)
          continue
        }
        if (retries < maxRetries && shouldAutoRetry(e)) {
          const delay = cfg.retryDelaysMs[retries] + Math.floor(random() * cfg.retryJitterMs)
          retries++
          await pause(delay, opts.signal)
          continue
        }
        throw new ApiClientError({
          code: e.code,
          status: e.status,
          retryable: e.retryable,
          message: e.message,
          attempts: attemptNo,
          fromGateway: e.fromGateway,
        })
      }
    }
  }

  return {
    health: () => request<HealthResponse>({ method: 'GET', path: '/health', auth: false }),
    createSession: (req) => request<SessionResponse>({ method: 'POST', path: '/session', body: req, auth: false }),
    async chat(req, control) {
      if (control?.signal?.aborted) throw abortedError()
      if (chatInFlight) {
        throw new ApiClientError({
          code: 'busy',
          status: null,
          retryable: false,
          message: 'a chat request is already in flight',
          attempts: 0,
        })
      }
      chatInFlight = true
      try {
        return await request<ChatResponse>({
          method: 'POST',
          path: '/chat',
          body: req,
          auth: 'session',
          signal: control?.signal,
        })
      } finally {
        chatInFlight = false
      }
    },
    getCase: (caseId, control) =>
      request<CaseResponse>({
        method: 'GET',
        path: `/cases/${encodeURIComponent(caseId)}`,
        auth: 'session',
        signal: control?.signal,
        noRetry: control?.noRetry,
      }),
    isChatInFlight: () => chatInFlight,
    listAnalystCases: (query, control) =>
      request<AnalystCaseListResponse>({
        method: 'GET',
        path: analystListPath(query),
        auth: 'analyst',
        signal: control?.signal,
        noRetry: control?.noRetry,
      }),
    getAnalystCase: (caseId, control) =>
      request<AnalystCaseDetail>({
        method: 'GET',
        path: `/analyst/cases/${encodeURIComponent(caseId)}`,
        auth: 'analyst',
        signal: control?.signal,
        noRetry: control?.noRetry,
      }),
    decide: (caseId, req, control) =>
      request<DecisionResponse>({
        method: 'POST',
        path: `/analyst/cases/${encodeURIComponent(caseId)}/decision`,
        body: req,
        auth: 'analyst',
        signal: control?.signal,
        noRetry: control?.noRetry,
      }),
  }
}
