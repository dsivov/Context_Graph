import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { useSettingsStore } from '@/stores/settings'

/** Render a mermaid definition to inline SVG. Re-renders on `chart`/theme change. */
export default function Mermaid({ chart, className }: { chart: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const theme = useSettingsStore.use.theme()
  const isDark =
    theme === 'dark' ||
    (theme === 'system' && typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)

  useEffect(() => {
    let cancelled = false
    const id = 'mmd-' + Math.random().toString(36).slice(2)
    setError(null)
    try {
      mermaid.initialize({ startOnLoad: false, theme: isDark ? 'dark' : 'default', securityLevel: 'loose', suppressErrorRendering: true })
      mermaid.render(id, chart)
        .then(({ svg }) => { if (!cancelled && ref.current) ref.current.innerHTML = svg })
        .catch((e) => { if (!cancelled) setError(String(e?.message || e)) })
    } catch (e: any) {
      setError(String(e?.message || e))
    }
    return () => { cancelled = true }
  }, [chart, isDark])

  if (error) return <div className="empty" style={{ padding: 12 }}>Diagram error: {error}</div>
  return <div ref={ref} className={className} style={{ overflow: 'auto' }} />
}
