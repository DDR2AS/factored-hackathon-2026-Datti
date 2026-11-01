// Catches a failed lazy route (e.g. a chunk that no longer exists after a redeploy, found in
// the 30 sep local run) and offers a reload instead of leaving a blank page.

import { Component, type ReactNode } from 'react'
import { useI18n } from '../i18n'
import { IconAlert, IconRefresh } from './Icons'

interface State {
  failed: boolean
}

export class RouteBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  render() {
    return this.state.failed ? <LoadFailed /> : this.props.children
  }
}

function LoadFailed() {
  const { t } = useI18n()
  return (
    <div className="page-loading" role="alert">
      <p>
        <IconAlert size={16} /> {t('app.loadFailed')}
      </p>
      <button type="button" className="btn btn--primary" onClick={() => window.location.reload()}>
        <IconRefresh size={14} /> {t('app.reload')}
      </button>
    </div>
  )
}
