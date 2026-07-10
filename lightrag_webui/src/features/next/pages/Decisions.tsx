import { useEffect, useState, useCallback, useMemo } from 'react'
import { RefreshCwIcon, SearchIcon } from 'lucide-react'
import { listDecisions } from '@/api/lightrag'
import { useSettingsStore } from '@/stores/settings'
import DecisionRow, { type Decision, decisionText } from '@/features/next/DecisionRow'
import Paginator from '@/features/next/Paginator'

const PAGE_SIZE = 25

export default function Decisions() {
  const workspace = useSettingsStore.use.workspace()
  const [loading, setLoading] = useState(true)
  const [rows, setRows] = useState<Decision[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listDecisions({ limit: 500 })
      setRows((res?.decisions ?? []) as Decision[])
      setTotal(typeof res?.total_count === 'number' ? res.total_count : (res?.decisions?.length ?? 0))
    } catch {
      setRows([]); setTotal(0)
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load, workspace])

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase()
    if (!t) return rows
    return rows.filter((d) => {
      const rc = d.relation_context || {}
      const hay = [
        d.src_id, d.tgt_id, decisionText(d), rc.approved_by, rc.approved_via,
        rc.policy_ref, rc.provenance
      ].filter(Boolean).join(' ').toLowerCase()
      return hay.includes(t)
    })
  }, [rows, q])

  // reset to first page whenever the filter or workspace changes
  useEffect(() => { setPage(0) }, [q, workspace])
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Overview · Decisions</div>
          <h1>Decision ledger</h1>
          <p>Every approval, exception, and policy call recorded as a quadruple <span className="mono">(h, r, t, rc)</span>. Click any row for its full context.</p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={load}><RefreshCwIcon className="" />Refresh</button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="cbody" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <SearchIcon className="" style={{ width: 16, height: 16, color: 'var(--muted)', flexShrink: 0 }} />
          <input
            className="cgqinput"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by approver, channel, policy, entity…"
            style={{ border: 0, background: 'transparent' }}
          />
          <span className="chip" style={{ flexShrink: 0 }}>
            {q ? `${filtered.length} of ` : ''}{total ?? rows.length} loaded
          </span>
        </div>
      </div>

      <div className="card"><span className="stripe good" />
        {loading ? (
          <div className="spin" />
        ) : filtered.length === 0 ? (
          <div className="empty">
            {rows.length === 0
              ? 'No decisions recorded in this workspace yet.'
              : 'No decisions match that filter.'}
          </div>
        ) : (
          <>
            <div className="feed">
              {paged.map((d, i) => <DecisionRow key={page * PAGE_SIZE + i} d={d} />)}
            </div>
            <Paginator page={page} pageSize={PAGE_SIZE} total={filtered.length} onPage={setPage} />
          </>
        )}
      </div>
    </div>
  )
}
