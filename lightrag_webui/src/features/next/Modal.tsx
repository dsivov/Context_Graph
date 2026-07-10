import { useEffect } from 'react'
import { XIcon } from 'lucide-react'

/** Lightweight .cgnext modal: backdrop click + Esc to close. */
export default function Modal({
  title, subtitle, onClose, children, width = 720
}: {
  title: React.ReactNode
  subtitle?: React.ReactNode
  onClose: () => void
  children: React.ReactNode
  width?: number
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="cgmodal-overlay" onClick={onClose}>
      <div className="cgmodal" style={{ maxWidth: width }} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="mhead">
          <div style={{ minWidth: 0 }}>
            <h3 className="mtitle">{title}</h3>
            {subtitle && <div className="msub">{subtitle}</div>}
          </div>
          <button className="iconbtn" onClick={onClose} aria-label="Close"><XIcon className="" /></button>
        </div>
        <div className="mbody">{children}</div>
      </div>
    </div>
  )
}
