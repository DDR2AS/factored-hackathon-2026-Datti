import { useEffect, useState } from 'react'

/** matchMedia as state; without matchMedia (tests, old browsers) it assumes the desktop layout. */
export function useMediaQuery(query: string, fallback = true): boolean {
  const get = () => (typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : fallback)
  const [matches, setMatches] = useState(get)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return matches
}

export const DESKTOP_QUERY = '(min-width: 1024px)'
