import { useApi } from '../api/context'
import { useHealth, type HealthState } from '../chat/useHealth'
import { Footer, LanguageSwitch, SyntheticBanner, TopBar } from '../components/Chrome'
import { IconAlert, IconCheck, IconRefresh } from '../components/Icons'
import { LaneChip } from '../components/LaneChip'
import { DEMO_CUSTOMERS, type DemoCustomer } from '../demo/customers'
import { useI18n } from '../i18n'
import { hrefFor } from '../router'

export function JudgeLanding() {
  const { t } = useI18n()
  const api = useApi()
  const { health, recheck } = useHealth(api)

  return (
    <div className="page">
      <TopBar>
        <LanguageSwitch />
      </TopBar>
      <SyntheticBanner />
      <main className="landing" id="main" tabIndex={-1}>
        <section className="hero" aria-labelledby="hero-title">
          <p className="eyebrow">{t('landing.eyebrow')}</p>
          <h1 id="hero-title">{t('landing.title')}</h1>
          <ul className="hero__lines">
            <li>{t('landing.line1')}</li>
            <li>{t('landing.line2')}</li>
            <li>{t('landing.line3')}</li>
          </ul>
          <HealthLine health={health} onRetry={recheck} />
        </section>

        <section aria-labelledby="pick-title">
          <div className="section-head">
            <h2 id="pick-title">{t('landing.pick')}</h2>
            <p className="muted">{t('landing.pickHint')}</p>
          </div>
          <ul className="customers">
            {DEMO_CUSTOMERS.map((c) => (
              <li key={c.key}>
                <CustomerCard customer={c} />
              </li>
            ))}
          </ul>
          <p className="note">{t('landing.shared')}</p>
        </section>

        <nav className="later" aria-label={t('nav.label')}>
          <a className="later__item later__item--on" href={hrefFor({ name: 'console', caseId: null })}>
            {t('nav.console')}
          </a>
          <a className="later__item later__item--on" href={hrefFor({ name: 'trace', traceId: null })}>
            {t('nav.traces')}
          </a>
          <span className="later__item" aria-disabled="true">
            {t('nav.evaluation')} <span className="tag">{t('nav.soon', { date: t('avail.fri2') })}</span>
          </span>
        </nav>
      </main>
      <Footer />
    </div>
  )
}

function CustomerCard({ customer: c }: { customer: DemoCustomer }) {
  const { t } = useI18n()
  const name = c.nameKey ? t(c.nameKey) : c.firstName
  const active = c.status === 'active'
  return (
    <article className={`customer ${active ? '' : 'customer--soon'}`} aria-labelledby={`cust-${c.key}`}>
      <header className="customer__head">
        <span className="avatar" aria-hidden="true">
          {c.firstName.charAt(0)}
        </span>
        <div>
          <h3 id={`cust-${c.key}`} className="customer__name">
            {name}
          </h3>
          <span className="tag tag--lang">{c.tag}</span>
        </div>
        <span className={`tag ${active ? 'tag--active' : ''}`}>
          {active ? t('landing.active') : t('landing.soon', { date: c.availableKey ? t(c.availableKey) : '' })}
        </span>
      </header>
      <dl className="customer__body">
        <dt>{t('landing.demonstrates')}</dt>
        <dd>{t(c.demonstratesKey)}</dd>
        <dt>
          {t('landing.expected')} <LaneChip lane={c.expectedLane} />
        </dt>
        <dd>{t(c.expectedKey)}</dd>
      </dl>
      {active ? (
        <a className="btn btn--primary customer__cta" href={hrefFor({ name: 'chat', demoKey: c.key })}>
          {t('landing.start', { name: c.firstName })}
        </a>
      ) : (
        <span className="btn btn--disabled customer__cta" aria-disabled="true">
          {t('landing.soon', { date: c.availableKey ? t(c.availableKey) : '' })}
        </span>
      )}
    </article>
  )
}

function HealthLine({ health, onRetry }: { health: HealthState; onRetry: () => void }) {
  const { t } = useI18n()
  switch (health.kind) {
    case 'checking':
      return (
        <p className="health health--checking" role="status">
          <span className="spinner" aria-hidden="true" /> {t('health.checking')}
        </p>
      )
    case 'starting':
      return (
        <p className="health health--checking" role="status">
          <span className="spinner" aria-hidden="true" /> {t('health.starting')}
        </p>
      )
    case 'ok':
      return (
        <p className="health health--ok" role="status">
          <IconCheck size={14} /> {health.stage ? t('health.ok', { stage: health.stage }) : t('health.okNoStage')}
        </p>
      )
    case 'deps_missing':
      return (
        <p className="health health--warn" role="alert">
          <IconAlert size={14} /> {t('health.depsMissing')}
        </p>
      )
    case 'down':
      return (
        <p className="health health--error" role="alert">
          <IconAlert size={14} /> {t('health.down')} <span className="muted">({health.code})</span>
          <button type="button" className="btn btn--ghost btn--small" onClick={onRetry}>
            <IconRefresh size={14} /> {t('health.retry')}
          </button>
        </p>
      )
  }
}
