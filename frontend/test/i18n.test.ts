import { describe, expect, it } from 'vitest'
import { es } from '../src/i18n/es'
import { translate } from '../src/i18n'
import { pt } from '../src/i18n/pt'

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

describe('i18n dictionaries', () => {
  it('es and pt have exactly the same keys', () => {
    expect(Object.keys(pt).sort()).toEqual(Object.keys(es).sort())
  })

  it('no empty strings', () => {
    for (const [k, v] of [...Object.entries(es), ...Object.entries(pt)]) {
      expect(v.trim(), k).not.toBe('')
    }
  })

  it('placeholders match between languages', () => {
    for (const k of Object.keys(es) as (keyof typeof es)[]) {
      expect(placeholders(pt[k]), k).toEqual(placeholders(es[k]))
    }
  })

  it('every lane rule id in lane_rules.yaml has a translated reason', () => {
    const ruleIds = [
      'identity_not_verified', 'tool_failure', 'lost_card', 'asks_for_human', 'regulator_or_legal',
      'repeat_complainer', 'compensation_requested', 'high_amount', 'high_fraud_score',
      'complaint_about_person', 'no_progress', 'missing_evidence', 'duplicate_already_reversed',
      'duplicate_not_reversed', 'already_reversed', 'customer_recognizes', 'fee_matches_schedule',
      'fee_does_not_match', 'purchase_amount_disputed', 'unrecognized_low_risk', 'other_complaint_complete',
      'explanation_not_accepted', 'no_rule_matched',
    ]
    for (const id of ruleIds) expect(Object.keys(es)).toContain(`rule.${id}`)
  })

  it('interpolates variables and leaves unknown ones visible', () => {
    expect(translate('pt', 'landing.start', { name: 'João' })).toBe('Conversar como João')
    expect(translate('es', 'landing.start')).toBe('Conversar como {name}')
  })
})
