import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClientError, type ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { JudgeLanding } from '../src/views/JudgeLanding'

function renderLanding(health: ApiClient['health']) {
  const api = { health: vi.fn(health) } as unknown as ApiClient
  render(
    <ApiContext.Provider value={api}>
      <I18nProvider>
        <JudgeLanding />
      </I18nProvider>
    </ApiContext.Provider>,
  )
  return api
}

describe('JudgeLanding', () => {
  it('shows six customers; only active ones link to the chat', async () => {
    renderLanding(async () => ({ status: 'ok', stage: 'local', deps: 'ok', demo_clock_scale: null }))
    expect(await screen.findByText('API disponible · etapa local')).toBeTruthy()
    for (const name of ['Lucía', 'Sofía', 'Andrés (cliente demo)', 'João', 'Martina', 'Carlos']) {
      expect(screen.getByRole('heading', { name })).toBeTruthy()
    }
    const links = screen.getAllByRole('link', { name: /Conversar como/ })
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '#/chat/lucia',
      '#/chat/sofia',
      '#/chat/andres',
      '#/chat/joao',
      '#/chat/martina',
      '#/chat/carlos',
    ])
    expect(screen.getByText('Datos sintéticos · identidad simulada · portugués generado sin revisión nativa')).toBeTruthy()
  })

  it('warns when backend dependencies are missing', async () => {
    renderLanding(async () => ({ status: 'ok', stage: 'dev', deps: 'missing', demo_clock_scale: null }))
    expect(await screen.findByText(/le faltan dependencias del backend/)).toBeTruthy()
  })

  it('says "iniciando…" after 3 s and offers a retry when the API is down', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let fail: (e: unknown) => void = () => {}
      const api = renderLanding(
        () =>
          new Promise((_resolve, reject) => {
            fail = reject
          }),
      )
      await vi.advanceTimersByTimeAsync(3100)
      expect(await screen.findByText(/Iniciando/)).toBeTruthy()
      fail(new ApiClientError({ code: 'network', status: null, retryable: true, message: 'x' }))
      const retry = await screen.findByRole('button', { name: /Reintentar/ })
      vi.useRealTimers()
      await userEvent.click(retry)
      expect(api.health).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('switches the landing to Portuguese', async () => {
    renderLanding(async () => ({ status: 'ok', stage: null, deps: 'ok', demo_clock_scale: null }))
    await userEvent.click(screen.getByRole('button', { name: 'Português' }))
    expect(screen.getByText('Escolha um cliente')).toBeTruthy()
  })
})
