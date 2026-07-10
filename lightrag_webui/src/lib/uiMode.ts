// UI mode flag — lets the new "next" shell run alongside the classic UI.
// The redesigned shell is now the DEFAULT; the classic UI is the escape hatch
// at ?ui=classic (persisted). A stored preference always wins over the default.

export type UiMode = 'classic' | 'next'

const KEY = 'cg-ui'

export function getUiMode(): UiMode {
  try {
    const param = new URLSearchParams(window.location.search).get('ui')
    if (param === 'next' || param === 'classic') {
      localStorage.setItem(KEY, param)
      return param
    }
    const stored = localStorage.getItem(KEY)
    if (stored === 'next' || stored === 'classic') return stored
  } catch {
    /* localStorage unavailable — fall through */
  }
  return 'next'
}

export function setUiMode(mode: UiMode): void {
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* ignore */
  }
  const url = new URL(window.location.href)
  url.searchParams.set('ui', mode)
  window.location.href = url.toString()
}
