import { describe, expect, it } from 'vitest'
import { hrefFor, parseRoute } from '../src/router'

describe('hash router', () => {
  it('parses the known routes', () => {
    expect(parseRoute('')).toEqual({ name: 'judge' })
    expect(parseRoute('#/')).toEqual({ name: 'judge' })
    expect(parseRoute('#/chat/lucia')).toEqual({ name: 'chat', demoKey: 'lucia' })
    expect(parseRoute('#/chat/joao/')).toEqual({ name: 'chat', demoKey: 'joao' })
    expect(parseRoute('#/consola')).toEqual({ name: 'console', caseId: null })
    expect(parseRoute('#/consola/EV-1A2B3C4D')).toEqual({ name: 'console', caseId: 'EV-1A2B3C4D' })
    expect(parseRoute('#/traza/tr-0001')).toEqual({ name: 'trace', traceId: 'tr-0001' })
    expect(parseRoute('#/evaluacion')).toEqual({ name: 'evaluation' })
  })

  it('rejects unknown demo keys and unsafe ids', () => {
    expect(parseRoute('#/chat/mallory')).toEqual({ name: 'notFound' })
    expect(parseRoute('#/traza/<script>')).toEqual({ name: 'notFound' })
    expect(parseRoute('#/consola/<img>')).toEqual({ name: 'notFound' })
    expect(parseRoute('#/consola/EV-1/extra')).toEqual({ name: 'notFound' })
    expect(parseRoute('#/nope')).toEqual({ name: 'notFound' })
  })

  it('round-trips hrefs', () => {
    expect(parseRoute(hrefFor({ name: 'chat', demoKey: 'martina' }))).toEqual({ name: 'chat', demoKey: 'martina' })
    expect(parseRoute(hrefFor({ name: 'console', caseId: 'EV-9Z' }))).toEqual({ name: 'console', caseId: 'EV-9Z' })
  })
})
