import type { ClientErrorCode } from '../api/client'
import type { MessageKey } from '../i18n/es'

/** UI text for an API error code. Neutral wording; never reveals whether a case exists.
 *  `retryable` separates a 409 for a message the server still processes from a stale button. */
export function errorMessageKey(code: ClientErrorCode, retryable = false): MessageKey {
  switch (code) {
    case 'session_expired':
      return 'error.session_expired.title'
    case 'session_limit':
      return 'error.session_limit.title'
    case 'invalid_request':
      return 'error.invalid_request'
    case 'not_authorized':
      return 'error.not_authorized'
    case 'conflict':
      return retryable ? 'error.conflict_processing' : 'error.conflict'
    case 'rate_limited':
      return 'error.rate_limited'
    case 'tool_unavailable':
      return 'error.tool_unavailable'
    case 'model_timeout':
      return 'error.model_timeout'
    case 'not_implemented':
      return 'error.not_implemented'
    case 'precondition':
      return 'error.precondition'
    case 'internal':
      return 'error.internal'
    case 'network':
      return 'error.network'
    case 'timeout':
      return 'error.timeout'
    case 'busy':
      return 'error.busy'
    case 'not_found':
      return 'error.not_found'
    case 'unexpected':
    case 'aborted':
      return 'error.unexpected'
  }
}
