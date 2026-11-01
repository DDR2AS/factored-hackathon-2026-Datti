// Lane chip (A, B, C) with its short description. Its own module: the landing shows it on
// each customer card without pulling the case card into the first bundle (RNF-11).

import type { Lane } from '../api/types'
import { useI18n } from '../i18n'

export function LaneChip({ lane }: { lane: Lane }) {
  const { t } = useI18n()
  return (
    <span className={`lane lane--${lane}`}>
      <strong>{t('lane.label', { lane })}</strong>
      <span className="lane__desc">· {t(`lane.${lane}`)}</span>
    </span>
  )
}
