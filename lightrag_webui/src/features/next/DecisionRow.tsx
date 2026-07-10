import { useState } from 'react'
import { ScaleIcon, ChevronRightIcon, ChevronDownIcon } from 'lucide-react'

export type RelationContext = {
  decision_trace?: string; approved_by?: string; approved_via?: string
  policy_ref?: string; valid_from?: string; valid_until?: string
  confidence_score?: number; supporting_sentences?: string[]
  temporal_info?: string; quantitative_data?: string; provenance?: string
}
export type Decision = {
  src_id?: string; tgt_id?: string; relation_type?: string; keywords?: string
  relation_context?: RelationContext
}

export function decisionText(d: Decision): string {
  const rc = d.relation_context || {}
  return (
    rc.decision_trace ||
    [d.src_id, d.relation_type || d.keywords, d.tgt_id].filter(Boolean).join(' → ') ||
    'Decision recorded'
  )
}

/** A clickable decision row that expands to show the full RelationContext. */
export default function DecisionRow({ d }: { d: Decision }) {
  const [open, setOpen] = useState(false)
  const rc = d.relation_context || {}
  const title = decisionText(d)
  const hasDetail =
    (rc.supporting_sentences?.length ?? 0) > 0 ||
    !!rc.provenance || !!rc.temporal_info || !!rc.quantitative_data || !!rc.valid_from

  return (
    <div
      className="row"
      style={{ cursor: 'pointer', alignItems: 'flex-start' }}
      onClick={() => setOpen((o) => !o)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((o) => !o) } }}
    >
      <div className="ic" style={{ background: 'var(--good-dim)', color: 'var(--good)' }}>
        <ScaleIcon className="" />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="main-t">
          {rc.approved_by ? <><b>{rc.approved_by}</b> — {title}</> : title}
        </div>
        <div className="meta">
          {(d.src_id || d.tgt_id) && (
            <span className="chip"><span className="cd" style={{ background: 'var(--accent)' }} />{d.src_id} → {d.tgt_id}</span>
          )}
          {rc.approved_via && <span className="chip">via {rc.approved_via}</span>}
          {rc.policy_ref && <span className="chip">{rc.policy_ref}</span>}
          {rc.valid_until && <span className="chip good"><span className="cd" />valid → {rc.valid_until}</span>}
          {typeof rc.confidence_score === 'number' && <span className="chip accent">conf {rc.confidence_score}</span>}
        </div>

        {open && (
          <div className="box" style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8, cursor: 'default' }}
            onClick={(e) => e.stopPropagation()}>
            {rc.supporting_sentences && rc.supporting_sentences.length > 0 && (
              <Detail label="Evidence">
                {rc.supporting_sentences.map((s, i) => (
                  <div key={i} style={{ borderLeft: '2px solid var(--line2)', paddingLeft: 9, color: 'var(--text2)', margin: '3px 0' }}>“{s}”</div>
                ))}
              </Detail>
            )}
            {rc.quantitative_data && <Detail label="Quantitative">{rc.quantitative_data}</Detail>}
            {rc.temporal_info && <Detail label="Temporal">{rc.temporal_info}</Detail>}
            {(rc.valid_from || rc.valid_until) && (
              <Detail label="Validity">{rc.valid_from || '—'} → {rc.valid_until || 'open'}</Detail>
            )}
            {rc.provenance && <Detail label="Source">{rc.provenance}</Detail>}
            {!hasDetail && <div style={{ color: 'var(--muted)', fontSize: 12 }}>No additional context recorded.</div>}
          </div>
        )}
      </div>

      <div style={{ color: 'var(--muted)', flexShrink: 0, marginTop: 2 }}>
        {open ? <ChevronDownIcon className="" style={{ width: 15, height: 15 }} />
          : <ChevronRightIcon className="" style={{ width: 15, height: 15 }} />}
      </div>
    </div>
  )
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 12.5 }}>
      <span style={{ color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.4px', fontSize: 10.5 }}>{label}</span>
      <div style={{ color: 'var(--text)', marginTop: 2 }}>{children}</div>
    </div>
  )
}
