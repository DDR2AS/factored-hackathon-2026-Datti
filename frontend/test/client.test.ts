import { describe, expect, it, vi } from 'vitest'
import { ApiClientError, classifyHttpError, createApiClient, newClientMsgId } from '../src/api/client'
import { chatResponse, jsonResponse } from './fixtures'

type FetchArgs = [RequestInfo | URL, RequestInit | undefined]

function setup(responses: Array<Response | Error | (() => Promise<Response>)>, opts: { token?: string | null } = {}) {
  const calls: FetchArgs[] = []
  const queue = [...responses]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push([input, init])
    const next = queue.shift()
    if (next === undefined) throw new Error('no more responses')
    if (next instanceof Error) throw next
    if (typeof next === 'function') return next()
    return next
  })
  const sleeps: number[] = []
  const client = createApiClient({
    fetch: fetchMock as unknown as typeof fetch,
    sleep: async (ms) => {
      sleeps.push(ms)
    },
    random: () => 0.5,
    getToken: () => (opts.token === undefined ? 'tok-1' : opts.token),
  })
  const bodies = () => calls.map(([, init]) => JSON.parse(String(init?.body ?? 'null')))
  const headers = (i: number) => (calls[i][1]?.headers ?? {}) as Record<string, string>
  return { client, calls, sleeps, bodies, headers, fetchMock }
}

const req = { client_msg_id: '11111111-2222-4333-8444-555555555555', message: 'hola' }

describe('api client: retries', () => {
  it('retries 503 and 502 with the same client_msg_id, then succeeds', async () => {
    const { client, calls, sleeps, bodies } = setup([
      jsonResponse(503, { error: { code: 'internal', message: 'x', retryable: true } }),
      jsonResponse(502, { message: 'Bad Gateway' }),
      jsonResponse(200, chatResponse()),
    ])
    const res = await client.chat(req)
    expect(res.trace_id).toBe('tr-0001')
    expect(calls).toHaveLength(3)
    expect(bodies().map((b) => b.client_msg_id)).toEqual([req.client_msg_id, req.client_msg_id, req.client_msg_id])
    // 1 s and 3 s plus jitter (random 0.5 * 400 ms)
    expect(sleeps).toEqual([1200, 3200])
  })

  it('gives up after 2 retries and reports the attempts', async () => {
    const { client, calls } = setup([
      jsonResponse(504, { message: 'Endpoint request timed out' }),
      jsonResponse(504, { message: 'Endpoint request timed out' }),
      jsonResponse(504, { message: 'Endpoint request timed out' }),
    ])
    const err = await client.chat(req).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiClientError)
    expect((err as ApiClientError).code).toBe('internal')
    expect((err as ApiClientError).attempts).toBe(3)
    expect((err as ApiClientError).fromGateway).toBe(true)
    expect(calls).toHaveLength(3)
  })

  it('retries network errors and API Gateway 429 {"message"} throttling', async () => {
    const { client, calls } = setup([
      new TypeError('Failed to fetch'),
      jsonResponse(429, { message: 'Too Many Requests' }),
      jsonResponse(200, { status: 'ok', stage: 'local', deps: 'ok' }),
    ])
    const res = await client.health()
    expect(res.stage).toBe('local')
    expect(calls).toHaveLength(3)
  })

  it('maps a persistent 429 to rate_limited', async () => {
    const r = () => jsonResponse(429, { message: 'Too Many Requests' })
    const { client } = setup([r(), r(), r()])
    const err = (await client.health().catch((e: unknown) => e)) as ApiClientError
    expect(err.code).toBe('rate_limited')
    expect(err.fromGateway).toBe(true)
  })

  it.each([
    [400, { error: { code: 'invalid_request', message: 'bad', retryable: false } }, 'invalid_request'],
    [401, { error: { code: 'session_expired', message: 'expired', retryable: false } }, 'session_expired'],
    [403, { error: { code: 'not_authorized', message: 'no', retryable: false } }, 'not_authorized'],
    [409, { error: { code: 'session_limit', message: 'limit', retryable: false } }, 'session_limit'],
    [409, { error: { code: 'conflict', message: 'stale button', retryable: false } }, 'conflict'],
    [500, { error: { code: 'internal', message: 'boom', retryable: true } }, 'internal'],
    [501, { error: { code: 'not_implemented', message: 'soon', retryable: false } }, 'not_implemented'],
    [422, { error: { code: 'precondition', message: 'not awaiting_analyst', retryable: false } }, 'precondition'],
    [422, { error: { code: 'invalid_request', message: 'bad', retryable: false } }, 'invalid_request'],
  ])('does not auto-retry HTTP %i (%s)', async (status, body, code) => {
    const { client, calls } = setup([jsonResponse(status, body), jsonResponse(200, chatResponse())])
    const err = (await client.chat(req).catch((e: unknown) => e)) as ApiClientError
    expect(err.code).toBe(code)
    expect(calls).toHaveLength(1)
  })

  it('repeats the same client_msg_id once after a retryable 409 conflict (request in flight)', async () => {
    const { client, calls, sleeps, bodies } = setup([
      jsonResponse(409, { error: { code: 'conflict', message: 'in flight', retryable: true } }),
      jsonResponse(200, chatResponse()),
    ])
    await client.chat(req)
    expect(calls).toHaveLength(2)
    expect(bodies()[1].client_msg_id).toBe(req.client_msg_id)
    expect(sleeps).toEqual([1200])
  })
})

describe('api client: errors and sessions', () => {
  it('branches by status first: API Gateway 401 {"message"} is session_expired', () => {
    const e = classifyHttpError(401, '{"message":"Unauthorized"}')
    expect(e.code).toBe('session_expired')
    expect(e.fromGateway).toBe(true)
  })

  it('API Gateway route 404 {"message":"Not Found"} is not_found, not retryable', () => {
    const e = classifyHttpError(404, '{"message":"Not Found"}')
    expect(e.code).toBe('not_found')
    expect(e.retryable).toBe(false)
  })

  it('non-JSON 5xx bodies still classify', () => {
    const e = classifyHttpError(500, '<html>oops</html>')
    expect(e.code).toBe('internal')
    expect(e.retryable).toBe(true)
  })

  it('session_expired from the API surfaces without retry', async () => {
    const { client, calls } = setup([
      jsonResponse(401, { error: { code: 'session_expired', message: 'expired', retryable: false } }),
    ])
    const err = (await client.getCase('EV-1A2B3C4D').catch((e: unknown) => e)) as ApiClientError
    expect(err.code).toBe('session_expired')
    expect(err.status).toBe(401)
    expect(calls).toHaveLength(1)
  })

  it('without a token, chat fails locally as session_expired and never calls fetch', async () => {
    const { client, calls } = setup([jsonResponse(200, chatResponse())], { token: null })
    const err = (await client.chat(req).catch((e: unknown) => e)) as ApiClientError
    expect(err.code).toBe('session_expired')
    expect(calls).toHaveLength(0)
  })

  it('sends Authorization: Bearer by default and never puts the token in the URL', async () => {
    const { client, calls, headers } = setup([jsonResponse(200, chatResponse())])
    await client.chat(req)
    expect(headers(0).authorization).toBe('Bearer tok-1')
    expect(String(calls[0][0])).toBe('/api/chat')
    expect(String(calls[0][0])).not.toContain('tok-1')
  })

  it('plan B header x-ev-session is a config switch', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, chatResponse()))
    const client = createApiClient({
      fetch: fetchMock as unknown as typeof fetch,
      getToken: () => 'tok-2',
      config: { sessionHeader: 'x-ev-session' },
    })
    await client.chat(req)
    const init = (fetchMock.mock.calls[0] as unknown as FetchArgs)[1]
    const h = init?.headers as Record<string, string>
    expect(h['x-ev-session']).toBe('tok-2')
    expect(h.authorization).toBeUndefined()
  })

  it('POST /session carries no token', async () => {
    const { client, headers } = setup([jsonResponse(200, { ok: true })], { token: null })
    await client.createSession({ demo_key: 'lucia', channel: 'web' })
    expect(headers(0).authorization).toBeUndefined()
  })

  it('allows only one chat request in flight', async () => {
    let release: (r: Response) => void = () => {}
    const pending = new Promise<Response>((r) => (release = r))
    const { client, calls } = setup([() => pending])
    const first = client.chat(req)
    expect(client.isChatInFlight()).toBe(true)
    const second = (await client.chat({ ...req, client_msg_id: 'other' }).catch((e: unknown) => e)) as ApiClientError
    expect(second.code).toBe('busy')
    release(jsonResponse(200, chatResponse()))
    await first
    expect(client.isChatInFlight()).toBe(false)
    expect(calls).toHaveLength(1)
  })

  it('aborts after the timeout and reports timeout (no automatic retry)', async () => {
    vi.useFakeTimers()
    try {
      const fetchMock = vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
          }),
      )
      const client = createApiClient({ fetch: fetchMock as unknown as typeof fetch, getToken: () => 't' })
      const p = client.chat(req).catch((e: unknown) => e)
      await vi.advanceTimersByTimeAsync(25_000)
      const err = (await p) as ApiClientError
      expect(err.code).toBe('timeout')
      expect(err.retryable).toBe(true)
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('newClientMsgId returns distinct RFC 4122 v4 ids', () => {
    const a = newClientMsgId()
    const b = newClientMsgId()
    expect(a).not.toBe(b)
    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })
})
