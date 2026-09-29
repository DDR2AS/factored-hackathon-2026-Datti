import { Footer, SyntheticBanner, TopBar } from '../components/Chrome'
import { IconBack, IconClock } from '../components/Icons'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/es'

/** Placeholder for routes that exist in the plan but not yet in code. */
export function ComingSoon({ nameKey, dateKey, detail }: { nameKey: MessageKey; dateKey: MessageKey; detail?: string }) {
  const { t } = useI18n()
  const name = t(nameKey)
  return (
    <div className="page">
      <TopBar />
      <SyntheticBanner />
      <main className="soon" id="main" tabIndex={-1}>
        <div className="card soon__card">
          <IconClock size={28} />
          <h1>{name}</h1>
          <p className="tag">{t('soon.title')}</p>
          <p>{t('soon.body', { name, date: t(dateKey) })}</p>
          {detail && <p className="muted mono small">{detail}</p>}
          <a className="btn btn--primary" href="#/">
            <IconBack size={14} /> {t('soon.back')}
          </a>
        </div>
      </main>
      <Footer />
    </div>
  )
}

export function NotFound() {
  const { t } = useI18n()
  return (
    <div className="page">
      <TopBar />
      <main className="soon" id="main" tabIndex={-1}>
        <div className="card soon__card">
          <h1>{t('notFound.title')}</h1>
          <a className="btn btn--primary" href="#/">
            <IconBack size={14} /> {t('soon.back')}
          </a>
        </div>
      </main>
    </div>
  )
}
