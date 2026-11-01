// Runtime settings of the API client. Changing the session header (plan B if CloudFront
// drops Authorization on GET) only touches this file, never the views.

export type SessionHeader = 'authorization' | 'x-ev-session'

export interface ApiConfig {
  /** Relative prefix; CloudFront (and the Vite proxy locally) strips it. Never an absolute URL. */
  basePath: string
  /** 'authorization' sends `Authorization: Bearer <token>`; 'x-ev-session' sends the raw token. */
  sessionHeader: SessionHeader
  /** Per attempt; API Gateway cuts at 30 s, so the client gives up a bit earlier. */
  timeoutMs: number
  /** Delays before automatic retry 1 and 2 (plus jitter). Max retries = length. */
  retryDelaysMs: readonly number[]
  /** Max random extra delay added to each retry. */
  retryJitterMs: number
  /** First delay before repeating the same client_msg_id after a retryable 409 conflict
   *  (the server is still processing that id). Later delays double, up to 5 s. */
  conflictRetryDelayMs: number
  /** Total time spent waiting on retryable 409 conflicts before handing the retry to the
   *  user. Covers the Lambda's 29 s budget for the turn that is still running. */
  conflictMaxWaitMs: number
}

export const apiConfig: ApiConfig = {
  basePath: '/api',
  sessionHeader: 'authorization',
  timeoutMs: 25_000,
  retryDelaysMs: [1_000, 3_000],
  retryJitterMs: 400,
  conflictRetryDelayMs: 1_000,
  conflictMaxWaitMs: 30_000,
}

/** UI timings that come from the requirements (RNF-01, section 3). */
export const uiTimings = {
  healthSlowMs: 3_000,
  chatSlowMs: 6_000,
  sessionMinutes: 15,
  maxMessageChars: 1_000,
} as const
