import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { clearAnalystSession } from '../src/console/auth'
import { clearDrafts } from '../src/console/drafts'
import { clearAll } from '../src/session'
import { registerStrings } from '../src/i18n'
import { es } from '../src/i18n/es'
import { pt } from '../src/i18n/pt'

// The app loads the views' strings and Portuguese on demand (RNF-11); components rendered on
// their own in tests get every dictionary up front. test/lazy_strings.test.tsx covers the loading.
registerStrings('es', es)
registerStrings('pt', pt)

afterEach(() => {
  cleanup()
  clearAll()
  clearAnalystSession()
  clearDrafts()
  window.sessionStorage.clear()
  window.location.hash = ''
})
