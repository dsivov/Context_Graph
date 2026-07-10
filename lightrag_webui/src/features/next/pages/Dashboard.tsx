import { useEffect, useState, useCallback } from 'react'
import {
  BoxIcon, Share2Icon, ScaleIcon, TriangleAlertIcon, RefreshCwIcon, PlusIcon, ArrowRightIcon
} from 'lucide-react'
import {
  graphConnectivity, listDecisions, quarantineList, dedupReview,
  type ConnectivityReport
} from '@/api/lightrag'
import { useSettingsStore } from '@/stores/settings'
import DecisionRow, { type Decision } from '@/features/next/DecisionRow'
import Paginator from '@/features/next/Paginator'

const DEC_PAGE = 5

type Props = { onNavigate: (v: 'quality' | 'decisions') => void }

export default function Dashboard({ onNavigate }: Props) {
  const workspace = useSettingsStore.use.workspace()
  const [loading, setLoading] = useState(true)
  const [conn, setConn] = useState<ConnectivityReport | null>(null)
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [decisionsTotal, setDecisionsTotal] = useState<number | null>(null)
  const [quarantined, setQuarantined] = useState<number | null>(null)
  const [dedupPending, setDedupPending] = useState<number | null>(null)
  const [decPage, setDecPage] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setDecPage(0)
    // each source is independent — a 404/503 on one shouldn't blank the whole page
    const [c, d, q, r] = await Promise.allSettled([
      graphConnectivity(), listDecisions({ limit: 6 }), quarantineList(), dedupReview()
    ])
    if (c.status === 'fulfilled') setConn(c.value)
    if (d.status === 'fulfilled') {
      const arr = (d.value?.decisions ?? []) as Decision[]
      setDecisions(arr)
      setDecisionsTotal(typeof d.value?.total_count === 'number' ? d.value.total_count : arr.length)
    }
    if (q.status === 'fulfilled') setQuarantined(q.value?.summary?.count ?? 0)
    if (r.status === 'fulfilled') setDedupPending(r.value?.summary?.pending_review ?? 0)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load, workspace])

  const largest = conn?.largest_component_pct ?? 0
  const isolatedPct = conn?.isolated_pct ?? 0
  const middle = Math.max(0, +(100 - largest - isolatedPct).toFixed(2))

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Overview</div>
          <h1>Graph health &amp; decisions</h1>
          <p>Workspace <b className="mono">{workspace}</b></p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={load}><RefreshCwIcon className="" />Refresh</button>
          <button className="btn primary"><PlusIcon className="" />Ingest</button>
        </div>
      </div>

      {loading && !conn ? (
        <div className="spin" />
      ) : (
        <>
          <div className="kpis">
            <Kpi stripe="accent" icon={<BoxIcon className="" />} label="Entities"
              value={conn ? conn.total_nodes.toLocaleString() : '—'} sub="nodes in the graph" />
            <Kpi stripe="accent" icon={<Share2Icon className="" />} label="Relationships"
              value={conn ? conn.total_edges.toLocaleString() : '—'}
              sub={conn ? `mean degree ${conn.degree.mean}` : ''} />
            <Kpi stripe="good" icon={<ScaleIcon className="" />} label="Decisions on file"
              value={decisionsTotal != null ? decisionsTotal.toLocaleString() : '—'}
              sub="recorded (h, r, t, rc)" />
            <Kpi stripe={isolatedPct > 10 ? 'warn' : 'good'} icon={<TriangleAlertIcon className="" />}
              label="Isolated nodes"
              value={conn ? `${conn.isolated_pct}%` : '—'}
              subNode={conn
                ? <span className={'chip ' + (isolatedPct > 10 ? 'warn' : 'good')}><span className="cd" />{conn.isolated_nodes} nodes</span>
                : undefined} />
          </div>

          <div className="grid2">
            {/* connectivity */}
            <div className="card"><span className="stripe accent" />
              <div className="chead"><h3>Connectivity</h3><span className="sub">component distribution</span></div>
              <div className="cbody">
                {conn ? (
                  <>
                    <div className="bar" title="component distribution">
                      <span style={{ width: `${largest}%`, background: 'var(--good)' }} />
                      <span style={{ width: `${middle}%`, background: 'var(--accent)' }} />
                      <span style={{ width: `${isolatedPct}%`, background: 'var(--warn)' }} />
                    </div>
                    <div className="legend">
                      <span><i style={{ background: 'var(--good)' }} />Largest component {largest}%</span>
                      <span><i style={{ background: 'var(--accent)' }} />Other {middle}%</span>
                      <span><i style={{ background: 'var(--warn)' }} />Isolated {isolatedPct}%</span>
                    </div>
                    <div className="health" style={{ marginTop: 16, borderTop: '1px solid var(--line)', paddingTop: 16 }}>
                      <Stat label="Components" value={conn.connected_components.toLocaleString()} />
                      <Stat label="Largest" value={`${conn.largest_component_size.toLocaleString()}`} suffix=" nodes" />
                      <Stat label="Degree-1 leaves" value={conn.degree.degree1.toLocaleString()} />
                      <Stat label="Max degree" value={conn.degree.max.toLocaleString()} />
                    </div>
                  </>
                ) : <div className="empty">Connectivity report unavailable.</div>}
              </div>
            </div>

            {/* quality */}
            <div className="card"><span className="stripe warn" />
              <div className="chead"><h3>Graph quality</h3><span className="sub">operator actions</span></div>
              <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={{ display: 'flex', gap: 12 }}>
                  <div className="box" style={{ flex: 1 }}>
                    <div className="stat"><div className="lab">Quarantined</div></div>
                    <div style={{ fontSize: 24, fontWeight: 700 }} className="num">{quarantined ?? '—'}</div>
                    <div style={{ fontSize: 12, color: 'var(--text2)' }}>held, restorable</div>
                  </div>
                  <div className="box" style={{ flex: 1 }}>
                    <div className="stat"><div className="lab">Dedup pending</div></div>
                    <div style={{ fontSize: 24, fontWeight: 700 }} className="num">{dedupPending ?? '—'}</div>
                    <div style={{ fontSize: 12, color: 'var(--text2)' }}>gray-band review</div>
                  </div>
                </div>
                <button className="btn sm" style={{ alignSelf: 'flex-start' }} onClick={() => onNavigate('quality')}>
                  Open Graph Quality <ArrowRightIcon className="" />
                </button>
              </div>
            </div>
          </div>

          {/* decisions feed */}
          <div className="card" style={{ marginTop: 14 }}><span className="stripe good" />
            <div className="chead">
              <h3>Recent decisions</h3>
              <span className="sub">the quadruples <span className="mono">(h, r, t, rc)</span> behind your edges</span>
            </div>
            {decisions.length === 0 ? (
              <div className="empty">
                No decisions recorded yet. They appear here as approvals, exceptions, and policy calls are emitted.
              </div>
            ) : (
              <>
                <div className="feed">
                  {decisions.slice(decPage * DEC_PAGE, (decPage + 1) * DEC_PAGE).map((d, i) =>
                    <DecisionRow key={decPage * DEC_PAGE + i} d={d} />)}
                </div>
                <Paginator page={decPage} pageSize={DEC_PAGE} total={decisions.length} onPage={setDecPage} />
              </>
            )}
            <div style={{ padding: '0 17px 14px' }}>
              <button className="btn sm ghost" onClick={() => onNavigate('decisions')}>
                View all decisions <ArrowRightIcon className="" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function Kpi(props: {
  stripe: string; icon: React.ReactNode; label: string; value: string; sub?: string; subNode?: React.ReactNode
}) {
  return (
    <div className="card kpi"><span className={'stripe ' + props.stripe} />
      <div className="k-top">{props.icon}{props.label}</div>
      <div className="k-val num">{props.value}</div>
      <div className="k-sub">{props.subNode ?? props.sub}</div>
    </div>
  )
}

function Stat({ label, value, suffix }: { label: string; value: string; suffix?: string }) {
  return (
    <div className="stat">
      <div className="lab">{label}</div>
      <div className="v num">{value}{suffix && <small style={{ fontSize: 12, color: 'var(--muted)' }}>{suffix}</small>}</div>
    </div>
  )
}

