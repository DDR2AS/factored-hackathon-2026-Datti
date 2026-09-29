import type { JSX } from 'react'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/es'
import type { OriginKind } from '../trace'
import { IconHuman, IconMl, IconRule, IconSpark, IconTemplate, IconTool } from './Icons'

const LABELS: Record<OriginKind, MessageKey> = {
  rule: 'actor.rule',
  ml: 'actor.ml',
  genai: 'actor.genai',
  tool: 'actor.tool',
  human: 'actor.human',
  template: 'source.template',
}

const ICONS: Record<OriginKind, (p: { size?: number }) => JSX.Element> = {
  rule: IconRule,
  ml: IconMl,
  genai: IconSpark,
  tool: IconTool,
  human: IconHuman,
  template: IconTemplate,
}

/** Icon + text, never color alone. `label` overrides the default text (e.g. "Generado por IA · sintético"). */
export function SourceBadge({ kind, label }: { kind: OriginKind; label?: string }) {
  const { t } = useI18n()
  const Icon = ICONS[kind]
  return (
    <span className={`badge badge--${kind}`}>
      <Icon size={12} />
      <span>{label ?? t(LABELS[kind])}</span>
    </span>
  )
}
