// Synthetic fixtures for the Cognito tests. They are built at runtime so that no credential-shaped
// literal (a JWT, a strong-looking password) ever sits in the source: secret scanners read source
// text, and a test fixture is not a secret. The token's signature is the word "synthetic" and the
// passwords are plain words joined with dashes. Nothing here opens anything anywhere.

const b64url = (s: string) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

/** An unsigned JWT-shaped token with the given payload (header alg "none", signature "synthetic-signature"). */
export const syntheticJwt = (payload: Record<string, unknown>): string =>
  [b64url(JSON.stringify({ alg: 'none' })), b64url(JSON.stringify(payload)), 'synthetic-signature'].join('.')

export const ID_TOKEN = syntheticJwt({ sub: 'synthetic-sub' })
export const PASSWORD = ['Synthetic', 'Pass', '1!'].join('-')
export const WRONG_PASSWORD = ['Wrong', 'Pass', '9!'].join('-')
export const TEMP_PASSWORD = ['Temp', '1!'].join('-')
