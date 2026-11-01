// Sign-in of the analyst console. Locally: the LOCAL_ANALYST_TOKEN printed by
// scripts/local_api.py. In the cloud: Cognito (prepared, not enabled; see auth.ts).

import { useId, useState, type FormEvent } from 'react'
import { IconAlert, IconLock } from '../components/Icons'
import { useI18n } from '../i18n'
import { cognitoPasswordProvider, localTokenProvider, saveAnalystSession, type AnalystSession } from './auth'

export function LoginForm({
  onSignedIn,
  reason,
  autoFocus = true,
  titleId,
}: {
  onSignedIn: (s: AnalystSession) => void
  /** 'expired' when a 401 interrupted the work (drafts are kept). */
  reason: 'first' | 'expired'
  autoFocus?: boolean
  /** id of the heading, for a dialog's aria-labelledby. */
  titleId?: string
}) {
  const { t } = useI18n()
  const [token, setToken] = useState('')
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  const tokenId = useId()
  const hintId = useId()

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      const s = await localTokenProvider.signIn({ kind: 'local', token })
      saveAnalystSession(s)
      setToken('')
      setError(false)
      onSignedIn(s)
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="login" onSubmit={submit} noValidate>
      <h1 className="login__title" id={titleId}>
        <IconLock size={18} /> {t(reason === 'expired' ? 'login.expiredTitle' : 'login.title')}
      </h1>
      <p className="muted">{t(reason === 'expired' ? 'login.expiredBody' : 'login.body')}</p>

      <fieldset className="login__provider">
        <legend className="block__title">{t('login.provider')}</legend>
        <label className="login__option">
          <input type="radio" name="provider" checked readOnly /> {t('login.local')}
        </label>
        <label className="login__option login__option--off">
          <input type="radio" name="provider" disabled checked={false} readOnly /> {t('login.cognito')}
          <span className="tag">{cognitoPasswordProvider.available ? t('login.cognitoOn') : t('login.cognitoOff')}</span>
        </label>
        <p className="block__note">{t('login.cognitoNote')}</p>
      </fieldset>

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
          aria-invalid={error || undefined}
          // oxlint-disable-next-line jsx-a11y/no-autofocus -- the only field of a sign-in screen
          autoFocus={autoFocus}
        />
      </label>
      <p id={hintId} className="block__note">
        {t('login.tokenHint')}
      </p>
      {error && (
        <p className="notice notice--error login__error" role="alert">
          <IconAlert size={14} /> {t('login.invalid')}
        </p>
      )}
      <button type="submit" className="btn btn--primary" disabled={busy || !token.trim()}>
        {t('login.submit')}
      </button>
      <p className="block__note">{t('login.storage')}</p>
    </form>
  )
}
