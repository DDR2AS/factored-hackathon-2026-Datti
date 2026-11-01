// Error type of the API client, in its own module: the landing's health check needs it, and the
// client itself (retries, classification, every route) loads on demand (RNF-11).

import type { ErrorCode } from './types'

export type ClientErrorCode = ErrorCode | 'network' | 'timeout' | 'busy' | 'not_found' | 'unexpected' | 'aborted'

export class ApiClientError extends Error {
  readonly code: ClientErrorCode
  readonly status: number | null
  /** The user may press "Reintentar" (same client_msg_id for chat). */
  readonly retryable: boolean
  /** Total attempts made, including automatic retries. */
  readonly attempts: number
  /** True when the body was API Gateway's {"message": ...} instead of our error shape. */
  readonly fromGateway: boolean

  constructor(init: {
    code: ClientErrorCode
    status: number | null
    retryable: boolean
    message: string
    attempts?: number
    fromGateway?: boolean
  }) {
    super(init.message)
    this.name = 'ApiClientError'
    this.code = init.code
    this.status = init.status
    this.retryable = init.retryable
    this.attempts = init.attempts ?? 1
    this.fromGateway = init.fromGateway ?? false
  }
}

export function isApiClientError(e: unknown): e is ApiClientError {
  return e instanceof ApiClientError
}
