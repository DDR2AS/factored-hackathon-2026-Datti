import { useEffect, useRef, useState } from 'react'
import { formatCountdown } from '../format'
import { useI18n } from '../i18n'
import { IconClock } from './Icons'

/** 15-minute session countdown. Calls onExpire once when it reaches zero (no silent extension). */
export function SessionClock({ expiresAt, onExpire }: { expiresAt: string | null; onExpire?: () => void }) {
  const { t } = useI18n()
  const [now, setNow] = useState(() => Date.now())
  const fired = useRef<string | null>(null)

  useEffect(() => {
    if (!expiresAt) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [expiresAt])

  const target = expiresAt ? Date.parse(expiresAt) : NaN
  const left = Number.isNaN(target) ? 0 : target - now

  useEffect(() => {
    if (expiresAt && !Number.isNaN(target) && left <= 0 && fired.current !== expiresAt) {
      fired.current = expiresAt
      onExpire?.()
    }
  }, [expiresAt, target, left, onExpire])

  const text = !expiresAt ? t('clock.none') : left <= 0 ? t('clock.expired') : t('clock.left', { time: formatCountdown(left) })
  const low = expiresAt !== null && left > 0 && left < 2 * 60_000
  return (
    <div className={`clock ${low ? 'clock--low' : ''}`} role="timer" aria-label={t('clock.label')}>
      <IconClock size={14} />
      <span>{text}</span>
    </div>
  )
}
