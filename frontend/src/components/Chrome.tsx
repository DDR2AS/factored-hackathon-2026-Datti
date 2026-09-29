import type { ReactNode } from 'react'
import type { Language } from '../api/types'
import { useI18n } from '../i18n'

export function Brand() {
  const { t } = useI18n()
  return (
    <a className="brand" href="#/" aria-label={t('brand.home')}>
      <span className="brand__mark" aria-hidden="true">
        EV
      </span>
      <span className="brand__text">
        <span className="brand__product">{t('brand.product')}</span>
        <span className="brand__bank">
          {t('brand.bank')} · <em>{t('brand.demo')}</em>
        </span>
      </span>
    </a>
  )
}

export function TopBar({ children }: { children?: ReactNode }) {
  return (
    <header className="topbar">
      <Brand />
      <div className="topbar__actions">{children}</div>
    </header>
  )
}

export function SyntheticBanner() {
  const { t } = useI18n()
  // Inside a named landmark: axe "region" wants all content in one (a11y test, 29 sep).
  return (
    <aside aria-label={t('banner.label')}>
      <p className="synthetic-banner" role="note">
        {t('banner.synthetic')}
      </p>
    </aside>
  )
}

export function LanguageSwitch() {
  const { t, lang, setLang } = useI18n()
  const options: Language[] = ['es', 'pt']
  return (
    <div className="segmented" role="group" aria-label={t('lang.label')}>
      {options.map((l) => (
        <button
          key={l}
          type="button"
          className="segmented__item"
          aria-pressed={lang === l}
          lang={l === 'pt' ? 'pt-BR' : 'es'}
          onClick={() => setLang(l)}
        >
          {t(l === 'es' ? 'lang.es' : 'lang.pt')}
        </button>
      ))}
    </div>
  )
}

export function Footer() {
  const { t } = useI18n()
  return (
    <footer className="footer">
      <p>{t('footer.note')}</p>
    </footer>
  )
}
