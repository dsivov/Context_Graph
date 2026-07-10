import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { RefreshCwIcon } from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import Modal from '@/features/next/Modal'
import {
  graphConnectivity, dedupScan, dedupSweep, dedupReview, entityMerges, entityUnmerge,
  garbageScan, quarantineList, quarantineRestore, quarantineDiscard,
  connectivityRescue, pruneIsolates, communityBuild, communityList, communityQuery,
  type ConnectivityReport
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)

export default function GraphQualityNext() {
  const workspace = useSettingsStore.use.workspace()
  const [busy, setBusy] = useState<string | null>(null)
  const [conn, setConn] = useState<ConnectivityReport | null>(null)

  const [merges, setMerges] = useState<any[] | null>(null)
  const [review, setReview] = useState<any | null>(null)
  const [quarantine, setQuarantine] = useState<any[] | null>(null)
  const [communities, setCommunities] = useState<any[] | null>(null)
  const [cq, setCq] = useState('')
  const [cqAnswer, setCqAnswer] = useState<{ response: string; communities: any[] } | null>(null)

  const refreshConn = useCallback(async () => {
    try {
      setConn(await graphConnectivity())
    } catch (e) {
      setConn(null)
      toast.error(`Connectivity: ${errMsg(e)}`)
    }
  }, [])

  useEffect(() => {
    refreshConn()
    setMerges(null); setReview(null); setQuarantine(null); setCommunities(null); setCqAnswer(null)
  }, [refreshConn, workspace])

  const run = useCallback(
    async (key: string, fn: () => Promise<any>, ok?: (r: any) => string, after?: () => void) => {
      setBusy(key)
      const tid = toast.loading('Processing…')   // spinning indicator until the op resolves
      try {
        const r = await fn()
        toast.success(ok ? ok(r) : 'Done.', { id: tid })
        after?.()
        refreshConn()
        return r
      } catch (e) {
        toast.error(errMsg(e), { id: tid })
      } finally {
        setBusy(null)
      }
    },
    [refreshConn]
  )

  // Lock every action while one runs — a "Working…" header chip + a spinning
  // loading toast signal progress, so no button fires twice or looks idle.
  const off = (_k?: string) => busy !== null
  const isolatedHigh = (conn?.isolated_pct ?? 0) > 10

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Governance · Graph Quality</div>
          <h1>Keep the graph clean &amp; connected</h1>
          <p>Preview any action first — removals move to a restorable quarantine, never hard-deleted.</p>
        </div>
        <div className="actions">
          {busy && (
            <span className="chip accent" style={{ alignSelf: 'center' }}>
              <span className="cgspin" />Working…
            </span>
          )}
          <button className="btn ghost" onClick={refreshConn} disabled={busy !== null}>
            <RefreshCwIcon className="" />Refresh
          </button>
        </div>
      </div>

      {/* connectivity tiles */}
      {conn ? (
        <div className="tiles">
          <Tile label="Entities" value={conn.total_nodes.toLocaleString()} />
          <Tile label="Edges" value={conn.total_edges.toLocaleString()} />
          <Tile label="Isolated" value={`${conn.isolated_nodes}`} suffix={` ${conn.isolated_pct}%`}
            color={isolatedHigh ? 'var(--warn)' : undefined} />
          <Tile label="Components" value={conn.connected_components.toLocaleString()} />
          <Tile label="Largest" value={`${conn.largest_component_pct}`} suffix="%" color="var(--good)" />
          <Tile label="Mean degree" value={`${conn.degree.mean}`} />
        </div>
      ) : (
        <div className="card" style={{ marginBottom: 16 }}><div className="empty">Connectivity report unavailable.</div></div>
      )}

      <div className="grid-cards">
        {/* Deduplication */}
        <div className="card"><span className="stripe accent" />
          <div className="chead"><h3>Deduplication</h3>
            {review && <span className="sub">{review.summary?.pending_review ?? 0} pending</span>}
          </div>
          <div>
            <Op title="Scan for duplicates" desc="Case / suffix / embedding, reversibly. Ambiguous pairs queue.">
              <button className="btn sm" disabled={off('dscanp')}
                onClick={() => run('dscanp', () => dedupScan(false), (r) => `Preview: would merge ${r.merged}, queue ${r.queued}`)}>Preview</button>
              <button className="btn sm primary" disabled={off('dscan')}
                onClick={() => run('dscan', () => dedupScan(true), (r) => `Merged ${r.merged}, queued ${r.queued}`)}>Scan &amp; merge</button>
            </Op>
            <Op title="Run LLM sweep" desc="Adjudicate the gray-band review queue.">
              <button className="btn sm" disabled={off('dsweep')}
                onClick={() => run('dsweep', () => dedupSweep(), (r) => `Sweep: merged ${r.merged}, rejected ${r.rejected}`)}>Run sweep</button>
            </Op>
            <Op title="Review &amp; audit" desc="Pending pairs and reversible merges.">
              <button className="btn sm ghost" disabled={off('drev')}
                onClick={() => run('drev', () => dedupReview()).then((r) => r && setReview(r))}>Review queue</button>
              <button className="btn sm ghost" disabled={off('dmerge')}
                onClick={() => run('dmerge', () => entityMerges(), (r) => `${r.merges.length} merge(s)`).then((r) => r && setMerges(r.merges))}>Merge audit</button>
            </Op>
          </div>
          {merges && (
            <div className="cbody" style={{ paddingTop: 0 }}>
              <div className="box" style={{ maxHeight: 220, overflow: 'auto' }}>
                {merges.length === 0 && <div className="empty" style={{ padding: 12 }}>No merges yet.</div>}
                {merges.map((m) => (
                  <div key={m.id} style={rowStyle}>
                    <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      <code className="mono">{m.alias}</code> → <code className="mono">{m.into}</code>{' '}
                      <span className="chip">{m.method}</span>
                    </span>
                    <button className="btn sm ghost"
                      onClick={() => run('un' + m.id, () => entityUnmerge(m.id), () => `Unmerged ${m.alias}`,
                        () => setMerges((p) => (p || []).filter((x) => x.id !== m.id)))}>Unmerge</button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Garbage & quarantine */}
        <div className="card"><span className="stripe warn" />
          <div className="chead"><h3>Garbage &amp; quarantine</h3>
            {quarantine && <span className="sub">{quarantine.length} held</span>}
          </div>
          <div>
            <Op title="Scan for garbage" desc="Git hashes, env-vars, bare numbers, pronouns.">
              <button className="btn sm" disabled={off('gpre')}
                onClick={() => run('gpre', () => garbageScan(false), (r) => `Preview: ${r.quarantined} garbage node(s)`)}>Preview</button>
              <button className="btn sm warn" disabled={off('gscan')}
                onClick={() => run('gscan', () => garbageScan(true), (r) => `Removed ${r.removed} (quarantined)`)}>Scan &amp; remove</button>
            </Op>
            <Op title="Quarantine" desc="Everything removed is restorable.">
              <button className="btn sm ghost" disabled={off('qlist')}
                onClick={() => run('qlist', () => quarantineList(), (r) => `${r.summary.count} quarantined`).then((r) => r && setQuarantine(r.items))}>Show quarantine</button>
            </Op>
          </div>
          {quarantine && (
            <div className="cbody" style={{ paddingTop: 0 }}>
              <div className="box" style={{ maxHeight: 240, overflow: 'auto' }}>
                {quarantine.length === 0 && <div className="empty" style={{ padding: 12 }}>Quarantine is empty.</div>}
                {quarantine.map((q) => (
                  <div key={q.name} style={rowStyle}>
                    <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      <code className="mono">{q.name}</code> <span style={{ color: 'var(--muted)' }}>— {q.reason}</span>
                    </span>
                    <span style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                      <button className="btn sm ghost"
                        onClick={() => run('r' + q.name, () => quarantineRestore(q.name), () => `Restored ${q.name}`,
                          () => setQuarantine((p) => (p || []).filter((x) => x.name !== q.name)))}>Restore</button>
                      <button className="btn sm ghost"
                        onClick={() => run('d' + q.name, () => quarantineDiscard(q.name), () => `Discarded ${q.name}`,
                          () => setQuarantine((p) => (p || []).filter((x) => x.name !== q.name)))}>Discard</button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Connectivity repair */}
        <div className="card"><span className="stripe crit" />
          <div className="chead"><h3>Connectivity repair</h3>
            {conn && <span className="sub">{conn.isolated_nodes} isolates</span>}
          </div>
          <div>
            <Op title="Rescue isolates" desc="Embedding proposes, the LLM confirms real edges.">
              <button className="btn sm primary" disabled={off('rescue')}
                onClick={() => run('rescue', () => connectivityRescue(true, 20), (r) => `Reconnected ${r.connected}/${r.isolates_scanned}, +${r.edges_added} edges`)}>Rescue 20</button>
            </Op>
            <Op title="Prune isolates" desc="Degree-0 only → restorable quarantine.">
              <button className="btn sm" disabled={off('ppre')}
                onClick={() => run('ppre', () => pruneIsolates(false), (r) => `${r.isolates} degree-0 isolate(s) — preview only`)}>Preview</button>
              <button className="btn sm warn" disabled={off('prune')}
                onClick={() => { if (confirm('Move all degree-0 isolates to the (restorable) quarantine?')) run('prune', () => pruneIsolates(true), (r) => `Pruned ${r.removed} isolate(s)`) }}>Prune</button>
            </Op>
          </div>
        </div>

        {/* Communities */}
        <div className="card"><span className="stripe comm" />
          <div className="chead"><h3>Communities</h3>
            {communities && <span className="sub">{communities.length} built</span>}
          </div>
          <div>
            <Op title="Build communities" desc="Louvain + LLM summaries for thematic global mode.">
              <button className="btn sm" disabled={off('cbuild')}
                onClick={() => run('cbuild', () => communityBuild(), (r) => `Built ${r.communities} communities`)}>Rebuild</button>
              <button className="btn sm ghost" disabled={off('clist')}
                onClick={() => run('clist', () => communityList(), (r) => `${r.summary.communities} communities`).then((r) => r && setCommunities(r.communities))}>List</button>
            </Op>
          </div>
          <div className="cbody" style={{ paddingTop: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {communities && (
              <div className="box" style={{ maxHeight: 180, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {communities.length === 0 && <div className="empty" style={{ padding: 4 }}>None built yet.</div>}
                {[...communities].sort((a, b) => b.size - a.size).map((c) => (
                  <div key={c.id} style={{ fontSize: 13 }}><span className="chip comm"><span className="cd" />{c.size}</span> {c.title}</div>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="cgqinput" placeholder="Ask a thematic question…" value={cq}
                onChange={(e) => setCq(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && cq.trim()) run('cq', () => communityQuery(cq), () => 'Answered').then((r) => r && setCqAnswer(r)) }} />
              <button className="btn sm primary" disabled={off('cq') || !cq.trim()}
                onClick={() => run('cq', () => communityQuery(cq), () => 'Answered').then((r) => r && setCqAnswer(r))}>Ask</button>
            </div>
            {cqAnswer && (
              <Modal
                title="Thematic answer"
                subtitle={`Themes: ${cqAnswer.communities.map((c) => c.title).join(' · ') || '—'}`}
                onClose={() => setCqAnswer(null)}
              >
                <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.65 }}>{cqAnswer.response}</div>
              </Modal>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  gap: 8, fontSize: 13, padding: '5px 2px'
}

function Tile({ label, value, suffix, color }: { label: string; value: string; suffix?: string; color?: string }) {
  return (
    <div className="tile">
      <div className="tl">{label}</div>
      <div className="tv num" style={color ? { color } : undefined}>{value}{suffix && <small>{suffix}</small>}</div>
    </div>
  )
}

function Op({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <div className="oprow">
      <div className="desc"><div className="t">{title}</div><div className="d">{desc}</div></div>
      <div className="btns">{children}</div>
    </div>
  )
}
