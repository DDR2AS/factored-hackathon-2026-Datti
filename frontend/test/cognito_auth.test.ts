// Cognito sign-in of the analyst console (console/auth.ts) with a fake fetch: no network, no AWS.
// The pool ids, the user and the token below are SYNTHETIC.
import { describe, expect, it, vi } from 'vitest'
import {
  CognitoSignInError,
  createCognitoPasswordProvider,
  parseCognitoConfig,
  type CognitoConfig,
} from '../src/console/auth'
import { ID_TOKEN, PASSWORD, TEMP_PASSWORD, WRONG_PASSWORD } from './synthetic'

const CONFIG: CognitoConfig = {
  region: 'us-east-2',
  userPoolId: 'us-east-2_SyNtHeTiC1',
  userPoolClientId: '1h57kf5cpq17m0eml12exampl',
}
const ENDPOINT = 'https://cognito-idp.us-east-2.amazonaws.com/'

/** The subset of Response the provider reads. */
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response

function provider(fetchImpl: typeof fetch) {
  const p = createCognitoPasswordProvider({ fetch: fetchImpl })
  p.primeConfig(CONFIG)
  return p
}

async function reasonOf(promise: Promise<unknown>): Promise<CognitoSignInError> {
  try {
    await promise
  } catch (e) {
    expect(e).toBeInstanceOf(CognitoSignInError)
    return e as CognitoSignInError
  }
  throw new Error('expected the sign-in to fail')
}

describe('cognito sign-in (USER_PASSWORD_AUTH over fetch)', () => {
  it('sends InitiateAuth to the region of config.json and keeps the id token', async () => {
    const fetchMock = vi.fn(async () =>
      reply(200, {
        AuthenticationResult: { AccessToken: 'synthetic-access', ExpiresIn: 3600, IdToken: ID_TOKEN, TokenType: 'Bearer' },
        ChallengeParameters: {},
      }),
    )
    const p = provider(fetchMock as unknown as typeof fetch)
    expect(await p.available()).toBe(true)
    const session = await p.signIn({ kind: 'cognito', username: ' analista@demo.local ', password: PASSWORD })
    expect(session).toMatchObject({ token: ID_TOKEN, provider: 'cognito' })
    expect(Date.parse(session.signed_in_at)).not.toBeNaN()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe(ENDPOINT)
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
    })
    expect(JSON.parse(init.body as string)).toEqual({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: CONFIG.userPoolClientId,
      AuthParameters: { USERNAME: 'analista@demo.local', PASSWORD: PASSWORD },
    })
  })

  it('a wrong password is bad_credentials, and the error never carries the password', async () => {
    const p = provider(vi.fn(async () => reply(400, { __type: 'NotAuthorizedException', message: 'Incorrect username or password.' })) as unknown as typeof fetch)
    const err = await reasonOf(p.signIn({ kind: 'cognito', username: 'analista@demo.local', password: WRONG_PASSWORD }))
    expect(err.reason).toBe('bad_credentials')
    expect(err.message).not.toContain(WRONG_PASSWORD)
    expect(err.message).not.toContain('analista@demo.local')
    // An unknown user reads the same (the pool may or may not hide user existence).
    const q = provider(vi.fn(async () => reply(400, { __type: 'com.amazonaws.cognito#UserNotFoundException', message: 'User does not exist.' })) as unknown as typeof fetch)
    expect((await reasonOf(q.signIn({ kind: 'cognito', username: 'nadie@demo.local', password: 'x' }))).reason).toBe('bad_credentials')
  })

  it('a network failure (offline, CSP) is network', async () => {
    const p = provider(vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }) as unknown as typeof fetch)
    const err = await reasonOf(p.signIn({ kind: 'cognito', username: 'analista@demo.local', password: PASSWORD }))
    expect(err.reason).toBe('network')
  })

  it('a temporary password (NEW_PASSWORD_REQUIRED) and other answers have their own reasons', async () => {
    const challenge = provider(vi.fn(async () => reply(200, { ChallengeName: 'NEW_PASSWORD_REQUIRED', Session: 'synthetic', ChallengeParameters: {} })) as unknown as typeof fetch)
    expect((await reasonOf(challenge.signIn({ kind: 'cognito', username: 'a@demo.local', password: TEMP_PASSWORD }))).reason).toBe('new_password_required')

    const throttled = provider(vi.fn(async () => reply(400, { __type: 'TooManyRequestsException', message: 'Rate exceeded' })) as unknown as typeof fetch)
    expect((await reasonOf(throttled.signIn({ kind: 'cognito', username: 'a@demo.local', password: 'x' }))).reason).toBe('rate_limited')

    const flowOff = provider(vi.fn(async () => reply(400, { __type: 'InvalidParameterException', message: 'USER_PASSWORD_AUTH flow not enabled for this client' })) as unknown as typeof fetch)
    const err = await reasonOf(flowOff.signIn({ kind: 'cognito', username: 'a@demo.local', password: 'x' }))
    expect(err.reason).toBe('unavailable')
    expect(err.detail).toBe('InvalidParameterException')

    const noToken = provider(vi.fn(async () => reply(200, { AuthenticationResult: { AccessToken: 'only' } })) as unknown as typeof fetch)
    expect((await reasonOf(noToken.signIn({ kind: 'cognito', username: 'a@demo.local', password: 'x' }))).reason).toBe('unavailable')

    const empty = provider(vi.fn() as unknown as typeof fetch)
    expect((await reasonOf(empty.signIn({ kind: 'cognito', username: '   ', password: '' }))).reason).toBe('bad_credentials')
  })

  it('reads the pool from /config.json once, and is not available where there is none', async () => {
    const fetchMock = vi.fn(async (url: string) =>
      url === '/config.json'
        ? reply(200, { stage: 'dev', cognitoRegion: CONFIG.region, userPoolId: CONFIG.userPoolId, userPoolClientId: CONFIG.userPoolClientId })
        : reply(404, {}),
    )
    const p = createCognitoPasswordProvider({ fetch: fetchMock as unknown as typeof fetch })
    expect(await p.loadConfig()).toEqual(CONFIG)
    expect(await p.available()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/config.json')

    const local = createCognitoPasswordProvider({ fetch: vi.fn(async () => reply(404, '')) as unknown as typeof fetch })
    expect(await local.available()).toBe(false)
    expect((await reasonOf(local.signIn({ kind: 'cognito', username: 'a@demo.local', password: 'x' }))).reason).toBe('not_configured')

    const offline = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    const flaky = createCognitoPasswordProvider({ fetch: offline as unknown as typeof fetch })
    expect(await flaky.available()).toBe(false)
    expect(await flaky.available()).toBe(false)
    expect(offline).toHaveBeenCalledTimes(2) // a failed read is not remembered
  })

  it('rejects a config.json that is not a Cognito pool (the URL is built from the region)', () => {
    expect(parseCognitoConfig({ cognitoRegion: 'evil.example.com/', userPoolId: CONFIG.userPoolId, userPoolClientId: CONFIG.userPoolClientId })).toBeNull()
    expect(parseCognitoConfig({ cognitoRegion: CONFIG.region, userPoolId: 'nope', userPoolClientId: CONFIG.userPoolClientId })).toBeNull()
    expect(parseCognitoConfig({ cognitoRegion: CONFIG.region, userPoolId: CONFIG.userPoolId })).toBeNull()
    expect(parseCognitoConfig(null)).toBeNull()
    expect(parseCognitoConfig({ ...CONFIG, cognitoRegion: 'sa-east-1', userPoolId: 'sa-east-1_AbC123xyz' })).toEqual({
      region: 'sa-east-1',
      userPoolId: 'sa-east-1_AbC123xyz',
      userPoolClientId: CONFIG.userPoolClientId,
    })
  })
})
