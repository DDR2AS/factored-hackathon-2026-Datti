// The case's conversation, folded under the structured package (docs/contrato_consola.md,
// AnalystCaseView.conversation). The server sends it REDACTED (no document, address or phone)
// and it is synthetic; the section says both. Secondary to the package: closed by default.
// Every turn is rendered as text (React escapes it; neutralize shows invisible characters),
// with who wrote it (role) and where the text came from (template, model, person).

import type { ConversationRole, ConversationTurn, TurnSource } from '../api/types'
import { SourceBadge } from '../components/SourceBadge'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/es'
import type { OriginKind } from '../trace'
import { neutralize } from './decision'
import { dateTime } from './format'

const ROLE_KEY: Record<ConversationRole, MessageKey> = {
  customer: 'console.convo.role.customer',
  system: 'console.convo.role.system',
  analyst: 'console.convo.role.analyst',
}

// template -> "Plantilla"; model -> "GenAI"; human -> "Humano" (the customer or the analyst).
const SOURCE_KIND: Record<TurnSource, OriginKind> = { template: 'template', model: 'genai', human: 'human' }

export function Conversation({ turns }: { turns: ConversationTurn[] | undefined }) {
  const { t, locale } = useI18n()
  const list = Array.isArray(turns) ? turns : []
  return (
    <details className="card convo" data-testid="console-conversation">
      <summary className="convo__summary">
        <span className="card__title">{t('console.convo.title')}</span>
        <span className="tag">
          {list.length === 1 ? t('console.convo.countOne') : t('console.convo.count', { n: list.length })}
        </span>
      </summary>
      <p className="block__note">{t('console.convo.note')}</p>
      {list.length === 0 ? (
        <p className="block__empty">{t('console.convo.empty')}</p>
      ) : (
        <ol className="convo__list" aria-label={t('console.convo.list')}>
          {list.map((turn, i) => (
            <li key={`${turn.at}-${i}`} className={`convo__turn convo__turn--${ROLE_KEY[turn.role] ? turn.role : 'system'}`}>
              <div className="convo__meta">
                <strong className="convo__role">{ROLE_KEY[turn.role] ? t(ROLE_KEY[turn.role]) : turn.role}</strong>
                <SourceBadge kind={SOURCE_KIND[turn.source] ?? 'template'} />
                <span className="muted small">{dateTime(turn.at, locale)}</span>
              </div>
              <p className="convo__text" lang={turn.language === 'pt' ? 'pt-BR' : 'es'}>
                {neutralize(turn.text)}
              </p>
            </li>
          ))}
        </ol>
      )}
    </details>
  )
}
