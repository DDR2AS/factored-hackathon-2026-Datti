import { useId, type KeyboardEvent, type RefObject } from 'react'
import { uiTimings } from '../api/config'
import { formatCount } from '../format'
import { useI18n } from '../i18n'
import { IconSend } from './Icons'

export interface ComposerProps {
  value: string
  onChange: (v: string) => void
  onSend: (text: string) => void
  /** Chat idle and session active. */
  canSend: boolean
  /** Session not usable at all (expired, limit, starting). */
  disabled: boolean
  buttonsOnly: boolean
  inputRef?: RefObject<HTMLTextAreaElement | null>
}

export function Composer({ value, onChange, onSend, canSend, disabled, buttonsOnly, inputRef }: ComposerProps) {
  const { t, locale } = useI18n()
  const hintId = useId()
  const counterId = useId()
  const max = uiTimings.maxMessageChars
  const length = value.length
  const tooLong = length > max
  const n = formatCount(length, locale)
  const maxText = formatCount(max, locale)
  const locked = disabled || buttonsOnly
  const sendable = canSend && !locked && !tooLong && value.trim().length > 0

  const submit = () => {
    if (!sendable) return
    onSend(value)
    onChange('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <label className="sr-only" htmlFor={`${hintId}-input`}>
        {t('composer.label')}
      </label>
      <div className="composer__box">
        <textarea
          id={`${hintId}-input`}
          ref={inputRef}
          className="composer__input"
          rows={2}
          value={value}
          placeholder={buttonsOnly ? t('composer.buttonsOnly') : t('composer.placeholder')}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={locked}
          aria-invalid={tooLong || undefined}
          aria-describedby={`${hintId} ${counterId}`}
          autoComplete="off"
          spellCheck
        />
        <button type="submit" className="composer__send" disabled={!sendable} aria-label={t('composer.send')}>
          <IconSend size={18} />
        </button>
      </div>
      <div className="composer__foot">
        <span id={hintId} className={tooLong ? 'composer__error' : undefined}>
          {buttonsOnly
            ? t('composer.buttonsOnly')
            : disabled
              ? t('composer.disabled')
              : tooLong
                ? t('composer.tooLong', { max: maxText })
                : t('composer.hint')}
        </span>
        <span id={counterId} className={`composer__counter ${tooLong ? 'composer__error' : ''}`}>
          <span aria-hidden="true">{t('composer.counter', { n, max: maxText })}</span>
          <span className="sr-only">{t('composer.counterLabel', { n, max: maxText })}</span>
        </span>
      </div>
    </form>
  )
}
