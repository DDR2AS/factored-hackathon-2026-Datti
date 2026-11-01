import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClientError, type ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { ChatRequest } from '../src/api/types'
import { CaseCard } from '../src/components/CaseCard'
import { MessageList } from '../src/components/MessageList'
import { I18nProvider } from '../src/i18n/I18nProvider'
import type { TurnSnapshot } from '../src/session'
import { ChatView } from '../src/views/ChatView'
import { caseView, chatResponse, sessionResponse } from './fixtures'

const XSS = '<img src=x onerror=alert(1)><script>alert(2)</script><b>negrita</b>'

type FakeApi = ApiClient & {
  health: ReturnType<typeof vi.fn>
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

function renderChat(api: ApiClient, demoKey: 'lucia' | 'joao' = 'lucia') {
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

describe('MessageList', () => {
  it('shows pasted HTML literally and never creates elements from it', () => {
    const { container } = render(
      <I18nProvider>
        <MessageList
          entries={[
            { kind: 'customer', id: 'c1', text: XSS, at: '2026-09-29T15:00:00Z', via_button: false, status: 'sent' },
            {
              kind: 'system',
              id: 's1',
              turn: 1,
              text: `Eco: ${XSS}`,
              lang: 'es',
              source: 'model',
              at: '2026-09-29T15:00:01Z',
              buttons: [],
              input_mode: 'free_text',
              degraded: ['model_timeout'],
            },
          ]}
          usedButtonIds={[]}
          canPress
          onPress={() => {}}
          onRetry={() => {}}
          retryEntryId={null}
          customerLang="es"
        />
      </I18nProvider>,
    )
    const log = screen.getByRole('log')
    expect(log.querySelector('img')).toBeNull()
    expect(log.querySelector('script')).toBeNull()
    expect(log.querySelector('b')).toBeNull()
    expect(screen.getByText(XSS)).toBeTruthy()
    expect(screen.getByText(`Eco: ${XSS}`)).toBeTruthy()
    expect(screen.getByText('Generado por IA · sintético')).toBeTruthy()
    expect(screen.getByText('Respuesta de respaldo: el modelo no respondió')).toBeTruthy()
    expect(container.querySelector('article[lang="es"]')).not.toBeNull()
    expect(log.getAttribute('aria-live')).toBe('polite')
  })
})

describe('CaseCard', () => {
  it('keeps "sin verificar" separate from the bank record', () => {
    render(
      <I18nProvider>
        <CaseCard snapshot={snapshot({ case_card: caseView(), lane: 'B' })} locale="es-MX" country="MX" />
      </I18nProvider>,
    )
    const claimed = screen.getByTestId('block-claimed')
    const bank = screen.getByTestId('block-bank')
    expect(within(claimed).getByText('Sin verificar')).toBeTruthy()
    expect(within(claimed).getByText('el súper')).toBeTruthy()
    expect(within(claimed).queryByText('SUPER AHORRO SA')).toBeNull()
    expect(within(bank).getByText('SUPER AHORRO SA')).toBeTruthy()
    expect(within(bank).getByText('•••• 4821')).toBeTruthy()
    expect(within(bank).getByText('$449.90')).toBeTruthy()
    expect(within(bank).queryByText('el súper')).toBeNull()
    expect(within(bank).queryByText('Sin verificar')).toBeNull()
    expect(screen.getByText('EV-1A2B3C4D')).toBeTruthy()
    expect(screen.getByText(/El cargo no fue reconocido y el riesgo es bajo/)).toBeTruthy()
    expect(screen.getByText('Ruta B')).toBeTruthy()
  })

  it('before any turn shows empty states, not invented data', () => {
    render(
      <I18nProvider>
        <CaseCard snapshot={null} locale="es-MX" country="MX" />
      </I18nProvider>,
    )
    expect(screen.getByText('Todavía no hay detalles.')).toBeTruthy()
    expect(screen.getByText(/Aún no hay un cargo confirmado/)).toBeTruthy()
  })
})

describe('ChatView', () => {
  it('opens a session, shows the welcome with its badge, and disables a button after use', async () => {
    const user = userEvent.setup()
    const api = fakeApi()
    renderChat(api)
    const chip = await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    expect(api.createSession).toHaveBeenCalledWith({ demo_key: 'lucia', channel: 'web', language: 'es' })
    expect(screen.getAllByText('Plantilla').length).toBeGreaterThan(0)

    await user.click(chip)
    expect(api.chat).toHaveBeenCalledTimes(1)
    const sent = api.chat.mock.calls[0][0] as ChatRequest
    expect(sent.button_id).toBe('start:n1')
    expect(sent.message).toBeUndefined()

    await screen.findByRole('button', { name: 'Es este' })
    const used = screen.getByRole('button', { name: /Un cargo que no reconozco/ }) as HTMLButtonElement
    expect(used.disabled).toBe(true)
    // An unused button of an earlier message is not clickable either.
    expect((screen.getByRole('button', { name: 'Hablar con una persona' }) as HTMLButtonElement).disabled).toBe(true)
    // buttons_only turns off free text.
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true)
    // Trace strip with actor badges.
    expect(screen.getByText('find_candidate_txns')).toBeTruthy()
    expect(screen.getAllByText('GenAI').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Herramienta').length).toBeGreaterThan(0)
  })

  it('a suggested message is copied to the composer and not sent', async () => {
    const user = userEvent.setup()
    const api = fakeApi()
    renderChat(api)
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.click(screen.getByRole('button', { name: /Copiar al cuadro de texto: Tengo un cargo/ }))
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      'Tengo un cargo en mi tarjeta que no reconozco',
    )
    expect(api.chat).not.toHaveBeenCalled()
  })

  it('Enter sends, Shift+Enter adds a line, and the message goes as text', async () => {
    const user = userEvent.setup()
    const api = fakeApi()
    renderChat(api)
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    await user.type(box, 'hola{Shift>}{Enter}{/Shift}mundo')
    expect(box.value).toBe('hola\nmundo')
    expect(api.chat).not.toHaveBeenCalled()
    await user.type(box, '{Enter}')
    expect(api.chat).toHaveBeenCalledTimes(1)
    expect((api.chat.mock.calls[0][0] as ChatRequest).message).toBe('hola\nmundo')
  })

  it('session_expired wipes the conversation and offers "Volver a empezar"', async () => {
    const user = userEvent.setup()
    const api = fakeApi({
      chat: vi.fn(async () => {
        throw new ApiClientError({ code: 'session_expired', status: 401, retryable: false, message: 'expired' })
      }),
    })
    renderChat(api)
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'hola{Enter}')
    expect(await screen.findByText('La sesión terminó')).toBeTruthy()
    expect(window.sessionStorage.getItem('ev.session.v1')).toBeNull()
    expect(window.sessionStorage.getItem('ev.transcript.v1')).toBeNull()
    expect(screen.queryByText('hola')).toBeNull()
    const restart = screen.getAllByRole('button', { name: 'Volver a empezar' })
    await user.click(restart[restart.length - 1])
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    expect(api.createSession).toHaveBeenCalledTimes(2)
  })

  it('a network failure offers Reintentar with the same client_msg_id', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError({ code: 'network', status: null, retryable: true, message: 'x' }))
      .mockResolvedValueOnce(chatResponse())
    const api = fakeApi({ chat })
    renderChat(api)
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'hola{Enter}')
    const retry = await screen.findAllByRole('button', { name: 'Reintentar' })
    await user.click(retry[0])
    await screen.findByRole('button', { name: 'Es este' })
    expect(chat).toHaveBeenCalledTimes(2)
    expect(chat.mock.calls[0][0].client_msg_id).toBe(chat.mock.calls[1][0].client_msg_id)
  })

  it('session_limit stops without retry', async () => {
    const user = userEvent.setup()
    const api = fakeApi({
      chat: vi.fn(async () => {
        throw new ApiClientError({ code: 'session_limit', status: 409, retryable: false, message: 'limit' })
      }),
    })
    renderChat(api)
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'hola{Enter}')
    expect(await screen.findByText('Se alcanzó el máximo de mensajes')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Reintentar' })).toBeNull()
  })

  it('João gets the UI in Portuguese', async () => {
    const api = fakeApi({
      createSession: vi.fn(async () =>
        sessionResponse({
          customer: { display_name: 'João', language: 'pt', locale: 'pt-BR', country: 'MX' },
          welcome: {
            turn: 0,
            reply_text: 'Olá João (sintético)',
            reply_language: 'pt',
            reply_source: 'template',
            buttons: [],
            input_mode: 'free_text',
          },
        }),
      ),
    })
    renderChat(api, 'joao')
    await screen.findByText('Olá João (sintético)')
    expect(screen.getByLabelText('Escreva sua mensagem')).toBeTruthy()
    expect(screen.getByText('Modelo de texto')).toBeTruthy()
    expect(screen.getByText('Painel do juiz')).toBeTruthy()
  })

  it('restores the conversation after a reload from sessionStorage', async () => {
    const api = fakeApi()
    const first = renderChat(api)
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    first.unmount()
    await act(async () => {
      renderChat(api)
    })
    await screen.findByText('Hola Lucía, ¿en qué te ayudo? (sintético)')
    await waitFor(() => expect(api.createSession).toHaveBeenCalledTimes(1))
  })
})
