import { createContext, useContext } from 'react'
import { getAnalystToken } from '../console/auth'
import { getToken } from '../session'
import type { ApiClient } from './client'

// The client module (retries, error classification, every route) is not in the first bundle
// (RNF-11): the app-wide client loads it on its first call. Tests inject a fake through
// ApiContext.Provider.

let real: ApiClient | null = null
let loading: Promise<ApiClient> | null = null
let chatsWaiting = 0

function client(): Promise<ApiClient> {
  if (real) return Promise.resolve(real)
  loading ??= import('./client').then(
    (m) => (real = m.createApiClient({ getToken, getAnalystToken })),
    (e: unknown) => {
      loading = null // the chunk did not load (offline): the next call tries again
      throw e
    },
  )
  return loading
}

export const defaultApiClient: ApiClient = {
  health: () => client().then((c) => c.health()),
  createSession: (req) => client().then((c) => c.createSession(req)),
  chat: (req, control) => {
    if (real) return real.chat(req, control)
    // A chat sent while the module loads already counts as in flight (one POST /chat at a time).
    chatsWaiting += 1
    return client().then(
      (c) => {
        chatsWaiting -= 1
        return c.chat(req, control)
      },
      (e: unknown) => {
        chatsWaiting -= 1
        throw e
      },
    )
  },
  getCase: (caseId, control) => client().then((c) => c.getCase(caseId, control)),
  isChatInFlight: () => chatsWaiting > 0 || (real?.isChatInFlight() ?? false),
  listAnalystCases: (query, control) => client().then((c) => c.listAnalystCases(query, control)),
  getAnalystCase: (caseId, control) => client().then((c) => c.getAnalystCase(caseId, control)),
  decide: (caseId, req, control) => client().then((c) => c.decide(caseId, req, control)),
}

export const ApiContext = createContext<ApiClient>(defaultApiClient)

export function useApi(): ApiClient {
  return useContext(ApiContext)
}
