// Regression tests for the review findings of M1 (SEC-07, F09, F11, FE-01..FE-10).
// Every payload is SYNTHETIC.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError, createApiClient, type ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { ChatRequest, DemoKey, SessionRequest, SessionResponse } from '../src/api/types'
import { CaseCard } from '../src/components/CaseCard'
import { Composer } from '../src/components/Composer'
import { MessageList } from '../src/components/MessageList'
import { TraceStrip } from '../src/components/TraceStrip'
import { formatClock } from '../src/format'
import { es } from '../src/i18n/es'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { getToken, loadSession, saveSession, saveTranscript, type TurnSnapshot } from '../src/session'
import { ChatView } from '../src/views/ChatView'
import { caseView, chatResponse, jsonResponse, sessionResponse } from './fixtures'

type FakeApi = ApiClient & {
  createSession: ReturnType<typeof vi.fn>
  chat: ReturnType<typeof vi.fn>
  getCase: ReturnType<typeof vi.fn>
}

function fakeApi(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): FakeApi {
  return {
    health: vi.fn(async () => ({ status: 'ok' as const, stage: 'local', deps: 'ok' as const })),
    createSession: vi.fn(async () => sessionResponse()),
    chat: vi.fn(async () => chatResponse()),
    getCase: vi.fn(async () => ({ case: caseView(), poll_after_ms: null })),
    isChatInFlight: () => false,
    ...overrides,
  } as FakeApi
}

function renderChat(api: ApiClient, demoKey: DemoKey = 'lucia') {
  return render(
    <ApiContext.Provider value={api}>
      <ChatView demoKey={demoKey} />
    </ApiContext.Provider>,
  )
}

function snapshot(over: Partial<TurnSnapshot> = {}): TurnSnapshot {
  const r = chatResponse()
  return {
    progress: r.progress,
    case_card: null,
    lane: null,
    degraded: [],
    trace_id: r.trace_id,
    trace_summary: r.trace_summary,
    client_latency_ms: 950,
    turn: 1,
    ...over,
  }
}

const joaoSession = (token = 'tok-joao'): SessionResponse =>
  sessionResponse({
    session_token: token,
    customer: { display_name: 'João', language: 'pt', locale: 'pt-BR', country: 'AR' },
    welcome: {
      turn: 0,
      reply_text: 'Olá João (sintético)',
      reply_language: 'pt',
      reply_source: 'template',
      buttons: [],
      input_mode: 'free_text',
    },
  })

afterEach(() => {
  vi.useRealTimers()
})

describe('SEC-07: the customer card does not reveal internal thresholds', () => {
  it.each([
    ['high_amount', /500|USD|monto/i],
    ['repeat_complainer', /dos o más|quejas previas/i],
    ['high_fraud_score', /fraude|score/i],
  ])('reason %s is generic in the card', (code, secret) => {
    render(
      <I18nProvider>
        <CaseCard
          snapshot={snapshot({
            case_card: caseView({ lane: 'C', lane_reason_code: code, handoff_queue: 'equipo sintético', expected_date: null }),
            lane: 'C',
          })}
          locale="es-AR"
          country="AR"
        />
      </I18nProvider>,
    )
    const reason = screen.getByText('Por qué').nextElementSibling as HTMLElement
    expect(reason.textContent).not.toMatch(secret)
    expect(reason.textContent).toMatch(/persona|equipo/)
  })

  it('the judge trace strip keeps the exact criterion', () => {
    const base = chatResponse().trace_summary
    render(
      <I18nProvider>
        <TraceStrip snapshot={snapshot({ trace_summary: { ...base, rule_id: 'high_amount' } })} />
      </I18nProvider>,
    )
    expect(screen.getByText(/mayor a 500 USD/)).toBeTruthy()
  })
})

describe('F09: customer-facing Spanish UI text does not pick tú (bot speaks tú, usted or vos)', () => {
  const CUSTOMER_PREFIXES = ['card.', 'slot.', 'rule.', 'error.', 'composer.', 'chat.', 'complaint.', 'status.']
  const SECOND_PERSON = [
    /\b(tú|tu|tus|te|ti|contigo|vos)\b/i,
    /\b\w+(aste|iste)\b/i, // preterite tú: llegaste, pediste, diste
    /\b(confirmes|reconoces|vuelves|tienes|inténtalo)\b/i,
    // sentence-initial imperatives in tú ("Revisa tu red", "Espera la respuesta")
    /(^|[.:;]\s+)(Escribe|Elige|Espera|Revisa|Usa|Vuelve|Reintenta)\b/,
  ]
  it('no second-person tú forms in customer keys', () => {
    const hits: string[] = []
    for (const [k, v] of Object.entries(es)) {
      if (!CUSTOMER_PREFIXES.some((p) => k.startsWith(p))) continue
      if (SECOND_PERSON.some((rx) => rx.test(v))) hits.push(`${k}: ${v}`)
    }
    expect(hits).toEqual([])
  })
})

describe('F11: a duplicated charge is not labeled as a fee', () => {
  it('before the case the provisional type covers fee or duplicate', () => {
    render(
      <I18nProvider>
        <CaseCard
          snapshot={snapshot({ progress: { ...chatResponse().progress, complaint_type: 'wrong_fee' } })}
          locale="es-CO"
          country="CO"
        />
      </I18nProvider>,
    )
    const claimed = screen.getByTestId('block-claimed')
    expect(within(claimed).queryByText(/Comisión que no coincide/)).toBeNull()
    expect(within(claimed).getByText(/Cobro indebido \(comisión o duplicado\)/)).toBeTruthy()
  })

  it('with the duplicate rule it says "Cargo duplicado"', () => {
    render(
      <I18nProvider>
        <CaseCard
          snapshot={snapshot({
            progress: { ...chatResponse().progress, complaint_type: 'wrong_fee' },
            case_card: caseView({ subcategory: 'wrong_fee', lane_reason_code: 'duplicate_not_reversed' }),
            lane: 'B',
          })}
          locale="es-CO"
          country="CO"
        />
      </I18nProvider>,
    )
    expect(within(screen.getByTestId('block-claimed')).getByText('Cargo duplicado (provisional)')).toBeTruthy()
  })
})

describe('FE-01: a stale POST /session never replaces the current session', () => {
  it('João resolving after Lucía opened does not touch the token or storage', async () => {
    let resolveJoao: (r: SessionResponse) => void = () => {}
    const createSession = vi.fn((req: SessionRequest) =>
      req.demo_key === 'joao'
        ? new Promise<SessionResponse>((r) => {
            resolveJoao = r
          })
        : Promise.resolve(sessionResponse()),
    )
    const api = fakeApi({ createSession })
    const joao = renderChat(api, 'joao')
    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1))
    joao.unmount()
    renderChat(api, 'lucia')
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await act(async () => {
      resolveJoao(joaoSession())
    })
    expect(getToken()).toBe('tok-synthetic-1')
    expect(loadSession()?.demo_key).toBe('lucia')
    expect(JSON.parse(window.sessionStorage.getItem('ev.session.v1') ?? '{}').demo_key).toBe('lucia')
    expect(screen.getByText('Hola Lucía, ¿en qué te ayudo? (sintético)')).toBeTruthy()
  })
})

describe('FE-02: a reload with a message in flight', () => {
  it('re-sends it with the same client_msg_id and shows the answer', async () => {
    const user = userEvent.setup()
    const hang = vi.fn(() => new Promise(() => {}))
    const first = renderChat(fakeApi({ chat: hang }))
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'Como 450 pesos{Enter}')
    await screen.findByText('Enviando…')
    const id = (hang.mock.calls[0] as unknown as [ChatRequest])[0].client_msg_id
    first.unmount() // the reload: only sessionStorage survives

    const api2 = fakeApi()
    renderChat(api2)
    await screen.findByRole('button', { name: 'Es este' })
    expect(api2.createSession).not.toHaveBeenCalled()
    expect(api2.chat).toHaveBeenCalledTimes(1)
    expect((api2.chat.mock.calls[0][0] as ChatRequest).client_msg_id).toBe(id)
    expect(screen.queryByText('Enviando…')).toBeNull()
  })

  it('an old transcript with a pending bubble and no stored turn shows "No se envió", not "Enviando…"', async () => {
    const s = sessionResponse()
    saveSession({ token: s.session_token, expires_at: s.expires_at, demo_key: 'lucia', customer: s.customer })
    saveTranscript({
      demo_key: 'lucia',
      entries: [{ kind: 'customer', id: 'c-x', text: 'hola', at: new Date().toISOString(), via_button: false, status: 'pending' }],
      used_button_ids: [],
      last: null,
    })
    renderChat(fakeApi())
    await screen.findByText('hola')
    expect(screen.queryByText('Enviando…')).toBeNull()
    expect(screen.getByText('No se envió')).toBeTruthy()
  })
})

describe('FE-03: closing the error notice', () => {
  it('keeps Reintentar on the failed bubble, with the same client_msg_id', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError({ code: 'network', status: null, retryable: true, message: 'x' }))
      .mockResolvedValueOnce(chatResponse())
    renderChat(fakeApi({ chat }))
    await user.click(await screen.findByRole('button', { name: 'Hablar con una persona' }))
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'Cerrar aviso' }))
    expect(screen.queryByRole('alert')).toBeNull()
    const retry = screen.getByRole('button', { name: 'Reintentar' })
    await user.click(retry)
    await screen.findByRole('button', { name: 'Es este' })
    expect(chat).toHaveBeenCalledTimes(2)
    expect(chat.mock.calls[1][0].client_msg_id).toBe(chat.mock.calls[0][0].client_msg_id)
  })

  it('a button the server rejected (not retryable) is free again, not "(elegida)"', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError({ code: 'invalid_request', status: 400, retryable: false, message: 'x' }))
    renderChat(fakeApi({ chat }))
    await user.click(await screen.findByRole('button', { name: 'Hablar con una persona' }))
    await screen.findByRole('alert')
    const chip = screen.getByRole('button', { name: 'Hablar con una persona' }) as HTMLButtonElement
    expect(chip.disabled).toBe(false)
    expect(chip.getAttribute('aria-disabled')).toBeNull()
    expect(chip.textContent).not.toContain('elegida')
  })
})

describe('FE-04: 409 conflict retryable ("still processing")', () => {
  it('says the message is still processing and offers Reintentar', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError({ code: 'conflict', status: 409, retryable: true, message: 'in flight' }))
      .mockResolvedValueOnce(chatResponse())
    renderChat(fakeApi({ chat }))
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'me cobraron 450 el 12{Enter}')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/todavía se está procesando/)
    expect(alert.textContent).not.toMatch(/opción/)
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }))
    await screen.findByRole('button', { name: 'Es este' })
    expect(chat.mock.calls[1][0].client_msg_id).toBe(chat.mock.calls[0][0].client_msg_id)
  })

  it('a stale button (409 not retryable) keeps the "opción" text without retry', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError({ code: 'conflict', status: 409, retryable: false, message: 'stale' }))
    renderChat(fakeApi({ chat }))
    await user.click(await screen.findByRole('button', { name: 'Hablar con una persona' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/ya no está vigente/)
    expect(screen.queryByRole('button', { name: 'Reintentar' })).toBeNull()
  })

  it('the client keeps waiting on a retryable 409 for about 30 s before giving up', async () => {
    const sleeps: number[] = []
    const fetchMock = vi.fn(async () =>
      jsonResponse(409, { error: { code: 'conflict', message: 'in flight', retryable: true } }),
    )
    const client = createApiClient({
      fetch: fetchMock as unknown as typeof fetch,
      getToken: () => 't',
      random: () => 0,
      sleep: async (ms) => {
        sleeps.push(ms)
      },
    })
    const err = (await client.chat({ client_msg_id: 'id-1', message: 'x' }).catch((e: unknown) => e)) as ApiClientError
    expect(err.code).toBe('conflict')
    expect(err.retryable).toBe(true)
    const waited = sleeps.reduce((a, b) => a + b, 0)
    expect(waited).toBeGreaterThanOrEqual(29_000)
    expect(waited).toBeLessThanOrEqual(30_000)
    expect(sleeps[0]).toBe(1_000)
    expect(Math.max(...sleeps)).toBeLessThanOrEqual(5_000)
    expect(fetchMock).toHaveBeenCalledTimes(sleeps.length + 1)
  })
})

describe('FE-05: leaving the chat with a turn in flight', () => {
  function hangingUntilAbort(init?: RequestInit) {
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })
  }

  it('the client aborts on the caller signal and frees the chat slot', async () => {
    const fetchMock = vi.fn((_i: RequestInfo | URL, init?: RequestInit) => hangingUntilAbort(init))
    const client = createApiClient({ fetch: fetchMock as unknown as typeof fetch, getToken: () => 't' })
    const ctrl = new AbortController()
    const p = client.chat({ client_msg_id: 'a', message: 'x' }, { signal: ctrl.signal }).catch((e: unknown) => e)
    expect(client.isChatInFlight()).toBe(true)
    ctrl.abort()
    const err = (await p) as ApiClientError
    expect(err.code).toBe('aborted')
    expect(client.isChatInFlight()).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('the next customer can chat right away (shared client, no "busy")', async () => {
    const user = userEvent.setup()
    let chatCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/session')) {
        const body = JSON.parse(String(init?.body)) as SessionRequest
        return jsonResponse(200, body.demo_key === 'joao' ? joaoSession() : sessionResponse())
      }
      if (url.endsWith('/chat')) {
        chatCalls++
        if (chatCalls === 1) return hangingUntilAbort(init)
        return jsonResponse(200, chatResponse())
      }
      return jsonResponse(404, { message: 'Not Found' })
    })
    const client = createApiClient({ fetch: fetchMock as unknown as typeof fetch, getToken })

    const joao = renderChat(client, 'joao')
    await screen.findByText('Olá João (sintético)')
    await user.type(screen.getByRole('textbox'), 'hola, tengo un problema{Enter}')
    await waitFor(() => expect(client.isChatInFlight()).toBe(true))
    joao.unmount()
    await waitFor(() => expect(client.isChatInFlight()).toBe(false))

    renderChat(client, 'lucia')
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'No reconozco un cargo de 450{Enter}')
    await screen.findByRole('button', { name: 'Es este' })
    expect(screen.queryByText(es['error.busy'])).toBeNull()
  })

  it('"busy" is retryable', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError({ code: 'busy', status: null, retryable: false, message: 'busy' }))
      .mockResolvedValueOnce(chatResponse())
    renderChat(fakeApi({ chat }))
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'hola{Enter}')
    await user.click((await screen.findAllByRole('button', { name: 'Reintentar' }))[0])
    await screen.findByRole('button', { name: 'Es este' })
  })
})

describe('FE-06: keyboard focus after pressing a clarification chip', () => {
  it('the pressed chip stays focusable while sending and focus lands on the next chip', async () => {
    const user = userEvent.setup()
    let answer: (v: unknown) => void = () => {}
    const chat = vi.fn(
      () =>
        new Promise((r) => {
          answer = r
        }),
    )
    renderChat(fakeApi({ chat }))
    const chip = await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    chip.focus()
    await user.keyboard('{Enter}')
    expect(chat).toHaveBeenCalledTimes(1)
    expect(chip.hasAttribute('disabled')).toBe(false)
    expect(chip.getAttribute('aria-disabled')).toBe('true')
    await act(async () => {
      answer(chatResponse())
    })
    const next = await screen.findByRole('button', { name: 'Es este' })
    await waitFor(() => expect(document.activeElement).toBe(next))
  })

  it('with a free-text reply, focus goes to the composer', async () => {
    const user = userEvent.setup()
    const chat = vi.fn(async () => chatResponse({ buttons: [], input_mode: 'free_text', reply_text: '¿Me cuentas más? (sintético)' }))
    renderChat(fakeApi({ chat }))
    const chip = await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    chip.focus()
    await user.keyboard('{Enter}')
    await screen.findByText('¿Me cuentas más? (sintético)')
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox')))
  })
})

describe('FE-09: one instant, one time zone; counters with local grouping', () => {
  it('formatClock uses the given zone', () => {
    expect(formatClock('2026-09-29T03:37:00Z', 'pt-BR', 'America/Argentina/Buenos_Aires')).toBe('00:37')
  })

  it('MessageList shows chat times in the customer zone', () => {
    render(
      <I18nProvider>
        <MessageList
          entries={[
            { kind: 'customer', id: 'c1', text: 'oi (sintético)', at: '2026-09-29T03:37:00Z', via_button: false, status: 'sent' },
          ]}
          usedButtonIds={[]}
          canPress
          onPress={() => {}}
          onRetry={() => {}}
          retryEntryId={null}
          customerLang="pt-BR"
          timeZone="America/Argentina/Buenos_Aires"
        />
      </I18nProvider>,
    )
    expect(screen.getByText('00:37')).toBeTruthy()
  })

  it('the Portuguese counter groups thousands (1.000)', () => {
    render(
      <I18nProvider initial="pt">
        <Composer value="" onChange={() => {}} onSend={() => {}} canSend disabled={false} buttonsOnly={false} />
      </I18nProvider>,
    )
    expect(screen.getByText('0/1.000')).toBeTruthy()
  })
})

describe('FE-10: the "Copiado" timer', () => {
  it('a second click restarts the 1.5 s instead of cutting it short', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(
      <I18nProvider>
        <TraceStrip snapshot={snapshot()} />
      </I18nProvider>,
    )
    const btn = screen.getByRole('button', { name: 'Copiar' })
    await act(async () => {
      fireEvent.click(btn)
    })
    expect(screen.getByText('Copiado')).toBeTruthy()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_400)
    })
    await act(async () => {
      fireEvent.click(btn)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200) // t = 1.6 s: the first timer would have hidden it
    })
    expect(screen.queryByText('Copiado')).not.toBeNull()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_400)
    })
    expect(screen.queryByText('Copiado')).toBeNull()
  })

  it('unmounting clears the timer', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const view = render(
      <I18nProvider>
        <TraceStrip snapshot={snapshot()} />
      </I18nProvider>,
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copiar' }))
    })
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
