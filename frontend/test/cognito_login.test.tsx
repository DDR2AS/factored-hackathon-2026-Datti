// The sign-in form on a site that publishes /config.json: Cognito is offered first and the
// analyst signs in with e-mail and password through the provider (fake fetch; SYNTHETIC data).
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createCognitoPasswordProvider, getAnalystToken, type CognitoConfig } from '../src/console/auth'
import { LoginForm } from '../src/console/LoginScreen'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { ID_TOKEN, PASSWORD, WRONG_PASSWORD } from './synthetic'

const CONFIG: CognitoConfig = {
  region: 'us-east-2',
  userPoolId: 'us-east-2_SyNtHeTiC1',
  userPoolClientId: '1h57kf5cpq17m0eml12exampl',
}

const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response

function cloudProvider(fetchImpl: typeof fetch) {
  const p = createCognitoPasswordProvider({ fetch: fetchImpl })
  p.primeConfig(CONFIG)
  return p
}

function renderForm(fetchImpl: typeof fetch, onSignedIn = vi.fn()) {
  render(
    <I18nProvider>
      <LoginForm reason="first" onSignedIn={onSignedIn} cognito={cloudProvider(fetchImpl)} />
    </I18nProvider>,
  )
  return onSignedIn
}

describe('console sign-in with Cognito', () => {
  it('offers Cognito first where /config.json names a pool, and signs in with e-mail and password', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn(async () => reply(200, { AuthenticationResult: { IdToken: ID_TOKEN, ExpiresIn: 3600, TokenType: 'Bearer' } }))
    const onSignedIn = renderForm(fetchMock as unknown as typeof fetch)
    const cognitoRadio = await screen.findByRole('radio', { name: /Cognito/ })
    await waitFor(() => expect(cognitoRadio).toHaveProperty('checked', true))
    expect(screen.getByText('disponible')).toBeTruthy()
    expect(screen.queryByLabelText('Token de analista')).toBeNull()

    await user.type(screen.getByLabelText('Correo del analista'), 'analista@demo.local')
    await user.type(screen.getByLabelText('Contraseña'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1))
    expect(onSignedIn.mock.calls[0][0]).toMatchObject({ token: ID_TOKEN, provider: 'cognito' })
    expect(getAnalystToken()).toBe(ID_TOKEN)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    // The password leaves the form once it was sent.
    expect((screen.getByLabelText('Contraseña') as HTMLInputElement).value).toBe('')
  })

  it('names a wrong password, and the local token stays one click away', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn(async () => reply(400, { __type: 'NotAuthorizedException', message: 'Incorrect username or password.' }))
    const onSignedIn = renderForm(fetchMock as unknown as typeof fetch)
    await waitFor(() => expect(screen.getByRole('radio', { name: /Cognito/ })).toHaveProperty('checked', true))

    await user.type(screen.getByLabelText('Correo del analista'), 'analista@demo.local')
    await user.type(screen.getByLabelText('Contraseña'), WRONG_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Correo o contraseña incorrectos.')
    expect(alert.textContent).not.toContain(WRONG_PASSWORD)
    expect(onSignedIn).not.toHaveBeenCalled()
    expect(getAnalystToken()).toBeNull()

    await user.click(screen.getByRole('radio', { name: /Token de analista local/ }))
    expect(screen.getByLabelText('Token de analista')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('explains a temporary password and a network failure', async () => {
    const user = userEvent.setup()
    const answers = [
      async () => reply(200, { ChallengeName: 'NEW_PASSWORD_REQUIRED', Session: 'synthetic', ChallengeParameters: {} }),
      async () => {
        throw new TypeError('Failed to fetch')
      },
    ]
    const fetchMock = vi.fn(() => (answers.shift() as () => Promise<Response>)())
    renderForm(fetchMock as unknown as typeof fetch)
    await waitFor(() => expect(screen.getByRole('radio', { name: /Cognito/ })).toHaveProperty('checked', true))
    await user.type(screen.getByLabelText('Correo del analista'), 'analista@demo.local')
    await user.type(screen.getByLabelText('Contraseña'), 'Temp-Pass-1!')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect((await screen.findByRole('alert')).textContent).toContain('contraseña temporal')

    await user.type(screen.getByLabelText('Contraseña'), 'Temp-Pass-1!')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('No se pudo contactar a Cognito'))
  })
})
