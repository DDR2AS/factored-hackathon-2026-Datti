import type { DemoSwitchState } from '../api/types'
import type { SwitchKey, SwitchPatch } from '../chat/useChat'
import type { DemoCustomer, Suggestion } from '../demo/customers'
import { hrefFor } from '../router'
import { IconClock, IconSwitch } from './Icons'
import { ROBUSTNESS_SUGGESTIONS } from '../demo/customers'
import { useI18n } from '../i18n'
import { DAY_S, DEMO_TIMERS_S, formatFactor, formatSpan } from '../chat/demoClock'
import { LaneChip } from './LaneChip'
import { SessionClock } from './SessionClock'

export interface JudgePanelProps {
  customer: DemoCustomer
  expiresAt: string | null
  onExpire: () => void
  /** Copies the text into the composer; never sends it. */
  onSuggestion: (text: string) => void
  suggestionsEnabled: boolean
  /** Switch state echoed by the server in the last ChatResponse (null before the first turn). */
  serverSwitches?: DemoSwitchState | null
  /** Switches staged for the next message. */
  pendingSwitches?: SwitchPatch
  onToggleSwitch?: (key: SwitchKey, value: boolean) => void
  onExpireNow?: () => void
  /** The expire button needs an idle, active session. */
  canExpireNow?: boolean
  /** Case to follow in the console, when there is one. */
  caseId?: string | null
  /** "Aplicar ahora": sends the staged switches alone (switches-only POST /chat). */
  onApplyNow?: () => void
  /** The chat is idle, the session active and something is staged. */
  canApplyNow?: boolean
  /** A switches-only request is in flight. */
  applying?: boolean
  /** The server's short confirmation of the last switches-only request. */
  switchNote?: { text: string; lang: 'es' | 'pt' } | null
  /** fast_clock factor reported by the server (GET /health); null: unknown, neutral texts. */
  clockScale?: number | null
}

const SWITCHES: readonly { key: SwitchKey; label: `judge.switch.${SwitchKey}`; hint: `judge.switch.${SwitchKey}Hint` }[] = [
  { key: 'tools_down', label: 'judge.switch.tools_down', hint: 'judge.switch.tools_downHint' },
  { key: 'model_slow', label: 'judge.switch.model_slow', hint: 'judge.switch.model_slowHint' },
  { key: 'fast_clock', label: 'judge.switch.fast_clock', hint: 'judge.switch.fast_clockHint' },
]

export function JudgePanel({
  customer,
  expiresAt,
  onExpire,
  onSuggestion,
  suggestionsEnabled,
  serverSwitches = null,
  pendingSwitches = {},
  onToggleSwitch,
  onExpireNow,
  canExpireNow = false,
  caseId = null,
  onApplyNow,
  canApplyNow = false,
  applying = false,
  switchNote = null,
  clockScale = null,
}: JudgePanelProps) {
  const { t, locale } = useI18n()
  // The fast clock's label and hint say the server's real scale, or no figure without one.
  const label = (sw: (typeof SWITCHES)[number]) =>
    sw.key !== 'fast_clock'
      ? t(sw.label)
      : clockScale
        ? t('judge.switch.fast_clock', { ratio: t('judge.clock.ratio', { t: formatSpan(DAY_S * clockScale, locale) }) })
        : t('judge.switch.fast_clockNeutral')
  const hint = (sw: (typeof SWITCHES)[number]) =>
    sw.key !== 'fast_clock'
      ? t(sw.hint)
      : clockScale
        ? t('judge.switch.fast_clockHint', { factor: formatFactor(clockScale, locale) })
        : t('judge.switch.fast_clockHintNeutral')
  return (
    <section className="card judge" aria-labelledby="judge-title">
      <header className="card__header">
        <h2 id="judge-title" className="card__title">
          {t('judge.title')}
        </h2>
        <SessionClock expiresAt={expiresAt} onExpire={onExpire} />
      </header>

      <div className="block">
        <h3 className="block__title">{t('judge.scenario')}</h3>
        <p>{t(customer.demonstratesKey)}</p>
      </div>

      <div className="block">
        <div className="block__head">
          <h3 className="block__title">{t('judge.expected')}</h3>
          <LaneChip lane={customer.expectedLane} />
        </div>
        <p>{t(customer.expectedKey)}</p>
        <p className="muted small">
          {t('trace.rule')}: <code className="mono">{customer.expectedRule}</code>
        </p>
      </div>

      <div className="block">
        <h3 className="block__title">{t('judge.suggested')}</h3>
        <p className="muted small">{t('judge.suggestedHint')}</p>
        <SuggestionList items={customer.suggestions} onPick={onSuggestion} enabled={suggestionsEnabled} />
        <h4 className="block__subtitle">{t('judge.robustness')}</h4>
        <SuggestionList items={ROBUSTNESS_SUGGESTIONS} onPick={onSuggestion} enabled={suggestionsEnabled} />
      </div>

      <div className="block">
        <h3 className="block__title">{t('judge.lookFor')}</h3>
        <ul className="bullets">
          <li>{t('judge.look1')}</li>
          <li>{t('judge.look2')}</li>
          <li>{t('judge.look3')}</li>
        </ul>
      </div>

      <div className="block switches" role="group" aria-labelledby="switches-title">
        <h3 id="switches-title" className="block__title">
          <IconSwitch size={14} /> {t('judge.switches')}
        </h3>
        <p className="muted small">{t('judge.switchesHow')}</p>
        <ul className="switches__list">
          {SWITCHES.map((sw) => {
            const server = serverSwitches?.[sw.key] ?? false
            const staged = sw.key in pendingSwitches
            const wanted = staged ? !!pendingSwitches[sw.key] : server
            return (
              <li key={sw.key} className="switch">
                <label className="switch__label">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={wanted}
                    disabled={!onToggleSwitch}
                    onChange={(e) => onToggleSwitch?.(sw.key, e.target.checked)}
                    aria-describedby={`sw-${sw.key}-state`}
                  />
                  <span>{label(sw)}</span>
                </label>
                <p className="muted small">{hint(sw)}</p>
                {sw.key === 'fast_clock' && <ClockSchedule scale={clockScale} />}
                <p id={`sw-${sw.key}-state`} className="switch__state small">
                  {t('judge.switch.server', {
                    state: serverSwitches ? t(server ? 'judge.switch.on' : 'judge.switch.off') : t('judge.switch.unknown'),
                  })}
                  {staged && (
                    <span className="tag tag--unverified">
                      <IconClock size={12} /> {t('judge.switch.pending', { state: t(wanted ? 'judge.switch.on' : 'judge.switch.off') })}
                    </span>
                  )}
                </p>
              </li>
            )
          })}
        </ul>
        <div className="switches__apply">
          <button
            type="button"
            className="btn btn--ghost btn--small"
            onClick={onApplyNow}
            disabled={!onApplyNow || !canApplyNow || applying}
            aria-describedby="switches-apply-hint"
          >
            {t(applying ? 'judge.applying' : 'judge.applyNow')}
          </button>
          <p id="switches-apply-hint" className="muted small">
            {t('judge.applyNowHint')}
          </p>
          {/* Always in the DOM so the confirmation is announced once, when it arrives. */}
          <p className="switch__note small" role="status" lang={switchNote?.lang === 'pt' ? 'pt-BR' : switchNote ? 'es' : undefined}>
            {switchNote?.text ?? ''}
          </p>
        </div>
        <button type="button" className="btn btn--ghost btn--small" onClick={onExpireNow} disabled={!onExpireNow || !canExpireNow}>
          {t('judge.expireNow')}
        </button>
        <p className="muted small">{t('judge.expireNowHint')}</p>
      </div>

      <div className="block">
        <h3 className="block__title">{t('judge.followCase')}</h3>
        <a
          className="btn btn--ghost btn--small"
          href={hrefFor({ name: 'console', caseId: caseId ?? null })}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t('judge.openConsole')}
        </a>
        <p className="muted small">{t(caseId ? 'judge.openConsoleCase' : 'judge.openConsoleHint', { id: caseId ?? '' })}</p>
      </div>
    </section>
  )
}

/** What the judge will see with the demo clock, and when (plan v2 sections 6 and 12). */
function ClockSchedule({ scale }: { scale: number | null }) {
  const { t, locale } = useI18n()
  const when = (key: 't1When' | 't2When' | 't3When', seconds: number) =>
    scale ? t(`judge.clock.${key}`, { t: formatSpan(seconds * scale, locale) }) : t(`judge.clock.${key}Neutral`)
  return (
    <div className="clock-plan" data-testid="clock-plan">
      <p className="small clock-plan__title">{t('judge.clock.title')}</p>
      <ol className="clock-plan__list small">
        <li>
          <strong>{when('t1When', DEMO_TIMERS_S.unassigned)}</strong> {t('judge.clock.t1')}
        </li>
        <li>
          <strong>{when('t2When', DEMO_TIMERS_S.sla_80)}</strong> {t('judge.clock.t2')}
        </li>
        <li>
          <strong>{when('t3When', DEMO_TIMERS_S.breached)}</strong> {t('judge.clock.t3')}
        </li>
      </ol>
      <p className="muted small">{t('judge.clock.note')}</p>
    </div>
  )
}

function SuggestionList({
  items,
  onPick,
  enabled,
}: {
  items: readonly Suggestion[]
  onPick: (text: string) => void
  enabled: boolean
}) {
  const { t } = useI18n()
  return (
    <ul className="suggestions">
      {items.map((s) => (
        <li key={s.text}>
          <button
            type="button"
            className="suggestion"
            lang={s.lang === 'pt' ? 'pt-BR' : 'es'}
            onClick={() => onPick(s.text)}
            disabled={!enabled}
            aria-label={t('judge.suggestionUse', { text: s.text })}
          >
            {s.text}
          </button>
        </li>
      ))}
    </ul>
  )
}
