// Pure helpers of the console and the analyst routes of the API client. SYNTHETIC data.
import { describe, expect, it, vi } from 'vitest'
import { analystListPath, createApiClient } from '../src/api/client'
import type { CaseIntent, DecisionRequest } from '../src/api/types'
import {
  buildDecisionRequest,
  decisionProblems,
  isProvisionalInvestigator,
  isReportReliable,
  labelsFor,
  mayPromiseRefund,
  neutralize,
  wordDiff,
} from '../src/console/decision'
import { emptyDraft, getDraft, setDraft } from '../src/console/drafts'
import { withJitter } from '../src/poll'
import { decisionResponse, jsonResponse, listResponse } from './fixtures'

const intent: CaseIntent = { class: 'dispute_charge', p: 0.9, gate_action: 'accept', model_version: 'm2@0' }

describe('report reliability (same rule as contract.report_reliable)', () => {
  it.each([
    [true, 0, true],
    [true, 1, true],
    [true, 2, false],
    [false, 0, false],
  ])('citations_valid=%s removed_claims=%s -> %s', (citations_valid, removed_claims, expected) => {
    expect(isReportReliable({ citations_valid, removed_claims })).toBe(expected)
  })

  it('the stub or a report without model is provisional, a model id is not', () => {
    expect(isProvisionalInvestigator({ model_id: 'stub-g2-deterministic', prompt_version: 'stub-provisional' })).toBe(true)
    expect(isProvisionalInvestigator({ model_id: null as unknown as string, prompt_version: 'x' })).toBe(true)
    expect(isProvisionalInvestigator({ model_id: 'claude-sonnet-5', prompt_version: 'g2@1' })).toBe(false)
  })
})

describe('buildDecisionRequest follows the approve/edit/reject table', () => {
  const common = { clientDecisionId: 'id-1', version: 7, caseLanguage: 'pt' as const, intent }

  it('approve has no reply and no next', () => {
    const req = buildDecisionRequest({ ...common, action: 'approve', draft: { ...emptyDraft(), rejectNext: 'escalate', editText: 'x' }, reliable: true })
    expect(req).toEqual({ client_decision_id: 'id-1', version: 7, action: 'approve' })
  })

  it('edit carries reply in the case language and the reason', () => {
    const req = buildDecisionRequest({
      ...common,
      action: 'edit',
      draft: { ...emptyDraft(), editText: '  Olá João  ', editReason: ' tom ', rejectNext: 'escalate' },
      reliable: true,
    })
    expect(req).toEqual({ client_decision_id: 'id-1', version: 7, action: 'edit', reply: { language: 'pt', text: 'Olá João' }, reason: 'tom' })
  })

  it('reject + escalate never carries a reply; request_information carries it only if written', () => {
    const esc = buildDecisionRequest({
      ...common,
      action: 'reject',
      draft: { ...emptyDraft(), rejectReason: 'r', rejectNext: 'escalate', rejectReply: 'pergunta' },
      reliable: true,
    })
    expect(esc).toEqual({ client_decision_id: 'id-1', version: 7, action: 'reject', reason: 'r', next: 'escalate' })
    const ask = buildDecisionRequest({
      ...common,
      action: 'reject',
      draft: { ...emptyDraft(), rejectReason: 'r', rejectNext: 'request_information', rejectReply: '' },
      reliable: true,
    })
    expect(ask.reply).toBeUndefined()
  })

  it('evidence_reviewed only for an unreliable report, on approve or edit', () => {
    const draft = { ...emptyDraft(), evidenceReviewed: true }
    expect(buildDecisionRequest({ ...common, action: 'approve', draft, reliable: false }).evidence_reviewed).toBe(true)
    expect(buildDecisionRequest({ ...common, action: 'approve', draft, reliable: true }).evidence_reviewed).toBeUndefined()
  })

  it('labels: confirmed, corrected, and never both keys', () => {
    expect(labelsFor({ kind: 'confirmed' }, intent)).toEqual({ intent_confirmed: true })
    expect(labelsFor({ kind: 'corrected', intentClass: 'dispute_fee' }, intent)).toEqual({ intent_class: 'dispute_fee' })
    expect(labelsFor({ kind: 'corrected', intentClass: 'dispute_charge' }, intent)).toEqual({ intent_confirmed: true })
    expect(labelsFor({ kind: 'confirmed' }, null)).toBeUndefined()
    expect(labelsFor(null, intent)).toBeUndefined()
  })

  it('decisionProblems blocks what the server would reject', () => {
    const d = emptyDraft()
    expect(decisionProblems('approve', d, { reliable: false, originalText: 'x' })).toContain('console.problem.reviewEvidence')
    expect(decisionProblems('approve', d, { reliable: true, originalText: null })).toContain('console.problem.noDraft')
    expect(decisionProblems('edit', { ...d, editText: 'x' }, { reliable: true, originalText: 'x' })).toEqual([
      'console.problem.replyUnchanged',
      'console.problem.reasonEmpty',
    ])
    expect(decisionProblems('edit', { ...d, editText: 'a'.repeat(2001), editReason: 'r' }, { reliable: true, originalText: 'x' })).toEqual([
      'console.problem.replyTooLong',
    ])
    expect(decisionProblems('reject', { ...d, rejectReason: 'r'.repeat(501) }, { reliable: true, originalText: 'x' })).toEqual([
      'console.problem.reasonTooLong',
      'console.problem.nextMissing',
    ])
  })
})

describe('text helpers', () => {
  it('wordDiff marks removed and added words', () => {
    expect(wordDiff('a b c', 'a x c')).toEqual([
      { op: 'same', text: 'a ' },
      { op: 'del', text: 'b' },
      { op: 'add', text: 'x' },
      { op: 'same', text: ' c' },
    ])
    expect(wordDiff('igual', 'igual')).toEqual([{ op: 'same', text: 'igual' }])
  })

  it('mayPromiseRefund flags promises and lets disclaimers through', () => {
    for (const p of ['Te ' + 'reembolsaremos mañana', 'Vamos a ' + 'devolverte el dinero', 'Reembolso ' + 'garantizado', 'Você receberá o ' + 'estorno', 'Vamos ' + 'estornar o valor']) {
      expect(mayPromiseRefund(p), p).toBe(true)
    }
    for (const p of ['No podemos prometer una devolución.', 'Uma pessoa vai analisar o caso.', 'Revisamos el cargo.']) {
      expect(mayPromiseRefund(p), p).toBe(false)
    }
  })

  it('neutralize makes bidi and zero-width characters visible', () => {
    expect(neutralize('a‮b​c')).toBe('a[U+202E]b[U+200B]c')
    expect(neutralize(null)).toBe('')
  })

  it('drafts live in memory per case', () => {
    setDraft('EV-1', { ...emptyDraft(), editReason: 'r' })
    expect(getDraft('EV-1').editReason).toBe('r')
    expect(getDraft('EV-2').editReason).toBe('')
  })

  it('jitter stays within +/- 20 %', () => {
    expect(withJitter(5000, 0.2, () => 0)).toBe(4000)
    expect(withJitter(5000, 0.2, () => 1)).toBe(6000)
  })
})

describe('analyst routes of the API client', () => {
  it('builds the list query with only the keys set', () => {
    expect(analystListPath()).toBe('/analyst/cases')
    expect(analystListPath({ lane: 'B', limit: 20, cursor: 'c 1' })).toBe('/analyst/cases?lane=B&limit=20&cursor=c+1')
  })

  it('sends the analyst token (never the customer token) as Bearer', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, listResponse([])))
    const api = createApiClient({
      fetch: fetchMock as unknown as typeof fetch,
      getToken: () => 'customer-token',
      getAnalystToken: () => 'analyst-token',
    })
    await api.listAnalystCases({ status: 'awaiting_analyst' })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/analyst/cases?status=awaiting_analyst')
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer analyst-token')
  })

  it('without an analyst token it fails as session_expired without calling fetch', async () => {
    const fetchMock = vi.fn()
    const api = createApiClient({ fetch: fetchMock as unknown as typeof fetch, getToken: () => 'customer-token' })
    await expect(api.getAnalystCase('EV-1')).rejects.toMatchObject({ code: 'session_expired' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('POSTs the decision body as is and does not retry a 409', async () => {
    const req: DecisionRequest = { client_decision_id: 'u-1', version: 2, action: 'approve' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(409, { error: { code: 'conflict', message: 'decided', retryable: false } }))
      .mockResolvedValue(jsonResponse(200, decisionResponse(req)))
    const api = createApiClient({ fetch: fetchMock as unknown as typeof fetch, getAnalystToken: () => 't', sleep: async () => {} })
    await expect(api.decide('EV-1A2B3C4D', req)).rejects.toMatchObject({ code: 'conflict', status: 409 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/analyst/cases/EV-1A2B3C4D/decision')
    expect(JSON.parse(init.body as string)).toEqual(req)
  })

  it('retries a decision on 503 with the same body (idempotent by client_decision_id)', async () => {
    const req: DecisionRequest = { client_decision_id: 'u-2', version: 2, action: 'approve' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(503, { message: 'Service Unavailable' }))
      .mockResolvedValue(jsonResponse(200, decisionResponse(req)))
    const api = createApiClient({ fetch: fetchMock as unknown as typeof fetch, getAnalystToken: () => 't', sleep: async () => {} })
    await api.decide('EV-1A2B3C4D', req)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const bodies = fetchMock.mock.calls.map((c) => (c as unknown as [string, RequestInit])[1].body)
    expect(bodies[0]).toBe(bodies[1])
  })

  it('noRetry makes a poll a single attempt', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(503, { message: 'x' }))
    const api = createApiClient({ fetch: fetchMock as unknown as typeof fetch, getToken: () => 't', sleep: async () => {} })
    await expect(api.getCase('EV-1', { noRetry: true })).rejects.toMatchObject({ status: 503 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
