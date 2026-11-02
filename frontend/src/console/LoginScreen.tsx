// Sign-in of the analyst console. Locally: the LOCAL_ANALYST_TOKEN printed by
// scripts/local_api.py. In the cloud: Cognito with e-mail and password (USER_PASSWORD_AUTH),
// offered as soon as the site publishes /config.json (auth.ts).

import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { IconAlert, IconLock } from '../components/Icons'
import { useI18n, type MessageKey } from '../i18n'
import {
  CognitoSignInError,
  cognitoPasswordProvider,
  localTokenProvider,
  saveAnalystSession,
  type AnalystSession,
  type AuthProviderKind,
  type CognitoAuthProvider,
  type CognitoSignInReason,
} from './auth'

const COGNITO_ERROR_KEYS: Record<CognitoSignInReason, MessageKey> = {
  bad_credentials: 'login.cognitoErr.bad_credentials',
  new_password_required: 'login.cognitoErr.new_password_required',
  password_reset: 'login.cognitoErr.password_reset',
  rate_limited: 'login.cognitoErr.rate_limited',
  network: 'login.cognitoErr.network',
  not_configured: 'login.cognitoErr.not_configured',
  unavailable: 'login.cognitoErr.unavailable',
}

export function LoginForm({
  onSignedIn,
  reason,
  autoFocus = true,
  titleId,
  cognito = cognitoPasswordProvider,
}: {
  onSignedIn: (s: AnalystSession) => void
  /** 'expired' when a 401 interrupted the work (drafts are kept). */
  reason: 'first' | 'expired'
  autoFocus?: boolean
  /** id of the heading, for a dialog's aria-labelledby. */
  titleId?: string
  /** The cloud provider; tests inject one with a fake fetch. */
  cognito?: CognitoAuthProvider
}) {
  const { t } = useI18n()
  const [provider, setProvider] = useState<AuthProviderKind>('local')
  const chosen = useRef(false) // the analyst picked a provider: never switch under them
  const [cognitoReady, setCognitoReady] = useState(false)
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<{ key: MessageKey; detail: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const groupName = useId()
  const tokenId = useId()
  const hintId = useId()
  const userId = useId()
  const passId = useId()
  const cognitoHintId = useId()

  useEffect(() => {
    let alive = true
    cognito.available().then((ok) => {
      if (!alive) return
      setCognitoReady(ok)
      // In the cloud there is no local token: start on Cognito unless the analyst already chose.
      if (ok && !chosen.current) setProvider('cognito')
    })
    return () => {
      alive = false
    }
  }, [cognito])

  const pick = (kind: AuthProviderKind) => {
    chosen.current = true
    setProvider(kind)
    setError(null)
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      const s =
        provider === 'cognito'
          ? await cognito.signIn({ kind: 'cognito', username, password })
          : await localTokenProvider.signIn({ kind: 'local', token })
      saveAnalystSession(s)
      setToken('')
      setPassword('')
      setError(null)
      onSignedIn(s)
    } catch (err) {
      setError(
        err instanceof CognitoSignInError
          ? { key: COGNITO_ERROR_KEYS[err.reason], detail: err.detail }
          : { key: 'login.invalid', detail: '' },
      )
    } finally {
      setBusy(false)
    }
  }

  const canSubmit = provider === 'cognito' ? Boolean(username.trim() && password) : Boolean(token.trim())

  return (
    <form className="login" onSubmit={submit} noValidate>
      <h1 className="login__title" id={titleId}>
        <IconLock size={18} /> {t(reason === 'expired' ? 'login.expiredTitle' : 'login.title')}
      </h1>
      <p className="muted">{t(reason === 'expired' ? 'login.expiredBody' : 'login.body')}</p>

      <fieldset className="login__provider">
        <legend className="block__title">{t('login.provider')}</legend>
        <label className="login__option">
          <input type="radio" name={groupName} value="local" checked={provider === 'local'} onChange={() => pick('local')} />{' '}
          {t('login.local')}
        </label>
        <label className={`login__option${cognitoReady ? '' : ' login__option--off'}`}>
          <input
            type="radio"
            name={groupName}
            value="cognito"
            checked={provider === 'cognito'}
            disabled={!cognitoReady}
            onChange={() => pick('cognito')}
          />{' '}
          {t('login.cognito')}
          <span className="tag">{t(cognitoReady ? 'login.cognitoOn' : 'login.cognitoOff')}</span>
        </label>
        <p className="block__note">{t(cognitoReady ? 'login.cognitoHint' : 'login.cognitoNote')}</p>
      </fieldset>

      {provider === 'cognito' ? (
        <>
          <label className="field" htmlFor={userId}>
            <span className="field__label">{t('login.username')}</span>
            <input
              id={userId}
              className="field__input"
              type="email"
              autoComplete="username"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              aria-describedby={cognitoHintId}
              aria-invalid={error ? true : undefined}
              // oxlint-disable-next-line jsx-a11y/no-autofocus -- the first field of a sign-in screen
              autoFocus={autoFocus}
            />
          </label>
          <label className="field" htmlFor={passId}>
            <span className="field__label">{t('login.password')}</span>
            <input
              id={passId}
              className="field__input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={error ? true : undefined}
            />
          </label>
          <p id={cognitoHintId} className="block__note">
            {t('login.cognitoFieldsHint')}
          </p>
        </>
      ) : (
        <>
          <label className="field" htmlFor={tokenId}>
            <span className="field__label">{t('login.token')}</span>
            <input
              id={tokenId}
              className="field__input mono"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              aria-describedby={hintId}
              aria-invalid={error ? true : undefined}
              // oxlint-disable-next-line jsx-a11y/no-autofocus -- the only field of a sign-in screen
              autoFocus={autoFocus}
            />
          </label>
          <p id={hintId} className="block__note">
            {t('login.tokenHint')}
          </p>
        </>
      )}
      {error && (
        <p className="notice notice--error login__error" role="alert">
          <IconAlert size={14} /> {t(error.key, { detail: error.detail })}
        </p>
      )}
      <button type="submit" className="btn btn--primary" disabled={busy || !canSubmit}>
        {t('login.submit')}
      </button>
      <p className="block__note">{t('login.storage')}</p>
    </form>
  )
}
