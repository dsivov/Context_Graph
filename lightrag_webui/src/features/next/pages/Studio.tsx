import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  RefreshCwIcon, FileDiffIcon, HistoryIcon, RotateCcwIcon, CheckIcon,
  ShieldAlertIcon, SparklesIcon, NetworkIcon, Code2Icon
} from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import AiChatPanel from '@/features/next/AiChatPanel'
import FlowCanvas from '@/features/next/FlowCanvas'
import Mermaid from '@/features/next/Mermaid'
import {
  studioArtifacts, studioHistory, studioPropose, studioApply, studioRevert, studioDraft, studioGraph,
  type StudioKind, type StudioArtifactRow, type StudioVersion, type ArtifactDiff,
  type StudioGraphNode, type StudioGraphEdge
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)
const KINDS: StudioKind[] = ['rule', 'ontology', 'flow', 'action']
const pretty = (v: any) => { try { return JSON.stringify(v, null, 2) } catch { return String(v) } }

const TEMPLATE: Record<StudioKind, any> = {
  rule: { dsl: 'rule "discount_cap"\nwhen\n    percent > 0.20\nthen\n    flag("exceeds the discount cap")\nend\n', concepts: {}, enabled: true, fixtures: [] },
  ontology: { name: 'domain', object_types: [], link_types: [] },
  flow: { id: 'intake', on_event: 'request.submitted', nodes: [{ id: 'in', kind: 'event' }], edges: [] },
  action: { name: 'catalog', actions: [] }
}
const defaultId = (k: StudioKind) => (k === 'rule' ? 'policy' : k === 'ontology' ? 'ontology' : k === 'action' ? 'catalog' : 'intake')

// ── draft persistence: keep edits per workspace:kind across switches/reloads ──
const draftMem = new Map<string, string>()
const draftLs = (k: string) => `cg-studio-draft:${k}`
function loadDraft(key: string, fallback: string): string {
  if (draftMem.has(key)) return draftMem.get(key)!
  try { const v = localStorage.getItem(draftLs(key)); if (v != null) { draftMem.set(key, v); return v } } catch { /* ignore */ }
  return fallback
}
function storeDraft(key: string, text: string) {
  draftMem.set(key, text)
  try { localStorage.setItem(draftLs(key), text) } catch { /* ignore */ }
}

export default function Studio() {
  const workspace = useSettingsStore.use.workspace()
  const [busy, setBusy] = useState<string | null>(null)
  const [artifacts, setArtifacts] = useState<StudioArtifactRow[] | null>(null)

  const [kind, setKind] = useState<StudioKind>('rule')
  const [artifactId, setArtifactId] = useState('policy')
  const [draftText, setDraftText] = useState(() => loadDraft('default:rule', pretty(TEMPLATE.rule)))
  const [diff, setDiff] = useState<ArtifactDiff | null>(null)
  const [approver, setApprover] = useState('')
  const [reason, setReason] = useState('')

  const [selected, setSelected] = useState<{ kind: string; id: string } | null>(null)
  const [history, setHistory] = useState<StudioVersion[] | null>(null)

  const [chatOpen, setChatOpen] = useState(false)
  const [flowMode, setFlowMode] = useState<'canvas' | 'json'>('canvas')
  const [canvasNonce, setCanvasNonce] = useState(0)

  const [graph, setGraph] = useState<{ nodes: StudioGraphNode[]; edges: StudioGraphEdge[] } | null>(null)

  const editDraft = useCallback((text: string) => {
    setDraftText(text)
    storeDraft(`${workspace}:${kind}`, text)
  }, [workspace, kind])

  const refreshArtifacts = useCallback(async () => {
    try { setArtifacts((await studioArtifacts()).artifacts) }
    catch (e) { setArtifacts(null); toast.error(`Studio: ${errMsg(e)}`) }
    try { setGraph(await studioGraph()) } catch { setGraph(null) }
  }, [])

  // On workspace change: reload lists + the persisted draft for the current kind.
  useEffect(() => {
    refreshArtifacts()
    setDiff(null); setSelected(null); setHistory(null)
    setDraftText(loadDraft(`${workspace}:${kind}`, pretty(TEMPLATE[kind])))
    setCanvasNonce((n) => n + 1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshArtifacts, workspace])

  const onKindChange = (k: StudioKind) => {
    if (k === kind) return
    setKind(k)
    setArtifactId(defaultId(k))
    setDraftText(loadDraft(`${workspace}:${k}`, pretty(TEMPLATE[k])))
    setDiff(null)
    setCanvasNonce((n) => n + 1)
  }

  const propose = useCallback(async () => {
    let draft: any
    try { draft = JSON.parse(draftText) }
    catch (e) { toast.error(`Draft is not valid JSON: ${errMsg(e)}`); return }
    setBusy('propose')
    const tid = toast.loading('Proposing…')
    try {
      const { diff } = await studioPropose({ kind, artifact_id: artifactId, draft })
      setDiff(diff)
      toast.success(diff.behaviour_changed ? 'Behavioural change — sign-off required' : 'Cosmetic change — lightweight', { id: tid })
    } catch (e) { toast.error(errMsg(e), { id: tid }) }
    finally { setBusy(null) }
  }, [draftText, kind, artifactId])

  const apply = useCallback(async () => {
    if (!diff) return
    if (diff.behaviour_changed && (!approver.trim() || !reason.trim())) {
      toast.error('This change alters behaviour — an approver and a reason are required.'); return
    }
    setBusy('apply')
    const tid = toast.loading('Applying…')
    try {
      const r = await studioApply(diff, approver.trim() ? { approver: approver.trim(), reason: reason.trim() } : undefined)
      toast.success(`Applied ${r.kind}:${r.artifact_id} v${r.version} — signed by ${r.sign_off.approver}`, { id: tid })
      setDiff(null); setApprover(''); setReason('')
      refreshArtifacts()
      if (selected && selected.kind === r.kind && selected.id === r.artifact_id) openHistory(r.kind, r.artifact_id)
    } catch (e) { toast.error(errMsg(e), { id: tid }) }
    finally { setBusy(null) }
  }, [diff, approver, reason, refreshArtifacts, selected])

  const openHistory = useCallback(async (k: string, id: string) => {
    setSelected({ kind: k, id }); setHistory(null)
    try { setHistory((await studioHistory(k, id)).history) } catch (e) { toast.error(errMsg(e)) }
  }, [])

  const revert = useCallback(async (v: StudioVersion) => {
    const who = window.prompt(`Revert ${v.kind}:${v.artifact_id} to v${v.version}. Approver:`, approver || '')
    if (!who) return
    const why = window.prompt('Reason for the revert:', `roll back to v${v.version}`)
    if (!why) return
    setBusy('revert')
    const tid = toast.loading('Reverting…')
    try {
      const r = await studioRevert(v.kind as StudioKind, v.artifact_id, v.version, who, why)
      toast.success(`Reverted → new v${r.version}`, { id: tid })
      refreshArtifacts(); openHistory(v.kind, v.artifact_id)
    } catch (e) { toast.error(errMsg(e), { id: tid }) }
    finally { setBusy(null) }
  }, [approver, refreshArtifacts, openHistory])

  // Flow canvas re-initialises from the persisted draft only when we bump the nonce
  // (kind/workspace switch or entering canvas mode) — never on its own edits.
  const flowInitial = useMemo(() => { try { return JSON.parse(draftText) } catch { return TEMPLATE.flow } }, [canvasNonce]) // eslint-disable-line react-hooks/exhaustive-deps

  const mermaid = useMemo(() => graph ? toMermaid(graph.nodes, graph.edges) : '', [graph])

  const before = diff?.delta?.before
  const after = diff?.delta?.after
  const needsSignoff = !!diff?.behaviour_changed

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Governance · Studio</div>
          <h1>Author &amp; sign governed changes</h1>
          <p>One gesture for every artifact: propose a change, see what it alters, then sign it off — every version is kept and revertible.</p>
        </div>
        <div className="actions">
          {busy && <span className="chip accent" style={{ alignSelf: 'center' }}><span className="cgspin" />Working…</span>}
          {!chatOpen && (
            <button className="btn ghost" onClick={() => setChatOpen(true)}><SparklesIcon className="" />Assistant</button>
          )}
          <button className="btn ghost" onClick={refreshArtifacts} disabled={busy !== null}><RefreshCwIcon className="" />Refresh</button>
        </div>
      </div>

      <div className="studio-shell">
        <div className="studio-work">
          <div className="grid-cards" style={{ gridTemplateColumns: 'minmax(0,1.35fr) minmax(0,1fr)' }}>
            {/* Author + diff review */}
            <div className="card"><span className="stripe accent" />
              <div className="chead"><h3>Propose a change</h3><span className="sub">{kind} · {artifactId || '—'}</span></div>
              <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <div className="segmented">
                    {KINDS.map((k) => <button key={k} className={'seg' + (kind === k ? ' on' : '')} onClick={() => onKindChange(k)}>{k}</button>)}
                  </div>
                  <input className="cgqinput" style={{ flex: 1, minWidth: 130 }} placeholder="artifact id"
                    value={artifactId} onChange={(e) => setArtifactId(e.target.value)} />
                </div>

                {kind === 'flow' ? (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <label className="fieldlabel">Flow — {flowMode === 'canvas' ? 'visual editor' : 'raw JSON'}</label>
                      <button className="btn sm ghost" onClick={() => { if (flowMode === 'json') setCanvasNonce((n) => n + 1); setFlowMode(flowMode === 'canvas' ? 'json' : 'canvas') }}>
                        <Code2Icon className="" />{flowMode === 'canvas' ? 'Edit JSON' : 'Visual editor'}
                      </button>
                    </div>
                    {flowMode === 'canvas'
                      ? <FlowCanvas key={`${workspace}:${canvasNonce}`} initial={flowInitial} onChange={(f) => editDraft(pretty(f))} />
                      : <textarea className="codearea" spellCheck={false} rows={14} value={draftText} onChange={(e) => editDraft(e.target.value)} />}
                  </>
                ) : (
                  <>
                    <label className="fieldlabel">Draft ({kind}) — edit the JSON, then propose</label>
                    <textarea className="codearea" spellCheck={false} rows={12} value={draftText} onChange={(e) => editDraft(e.target.value)} />
                  </>
                )}

                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn sm primary" disabled={busy !== null || !artifactId.trim()} onClick={propose}>
                    <FileDiffIcon className="" />Propose
                  </button>
                  <button className="btn sm ghost" onClick={() => setChatOpen(true)}><SparklesIcon className="" />Author with AI</button>
                </div>
              </div>

              {diff && (
                <div className="cbody" style={{ paddingTop: 0 }}>
                  <div className="divider" />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <span className={'chip ' + (needsSignoff ? 'warn' : 'good')}>
                      {needsSignoff ? <ShieldAlertIcon className="" /> : <CheckIcon className="" />}
                      {needsSignoff ? 'Behavioural — sign-off required' : 'Cosmetic — lightweight'}
                    </span>
                    <span className="sub">v{diff.from_version ?? '∅'} → v{diff.to_version}</span>
                  </div>
                  <div className="diffgrid">
                    <DiffPane title="Before" text={before == null ? '(new artifact)' : pretty(before)} />
                    <DiffPane title="After" text={pretty(after)} accent />
                  </div>
                  {needsSignoff && (
                    <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                      <input className="cgqinput" style={{ flex: '1 1 160px' }} placeholder="Approver (who signs)" value={approver} onChange={(e) => setApprover(e.target.value)} />
                      <input className="cgqinput" style={{ flex: '2 1 220px' }} placeholder="Reason for the change" value={reason} onChange={(e) => setReason(e.target.value)} />
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                    <button className="btn sm primary" disabled={busy !== null} onClick={apply}><CheckIcon className="" />{needsSignoff ? 'Sign off & apply' : 'Apply'}</button>
                    <button className="btn sm ghost" disabled={busy !== null} onClick={() => setDiff(null)}>Discard</button>
                  </div>
                </div>
              )}
            </div>

            {/* Artifacts + history */}
            <div className="card"><span className="stripe comm" />
              <div className="chead"><h3>Tracked artifacts</h3>{artifacts && <span className="sub">{artifacts.length}</span>}</div>
              <div className="cbody" style={{ paddingTop: 0 }}>
                <div className="box" style={{ maxHeight: 180, overflow: 'auto' }}>
                  {!artifacts && <div className="empty" style={{ padding: 12 }}>Loading…</div>}
                  {artifacts && artifacts.length === 0 && <div className="empty" style={{ padding: 12 }}>No artifacts authored yet.</div>}
                  {artifacts?.map((a) => (
                    <div key={a.kind + ':' + a.artifact_id} style={rowStyle}>
                      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        <span className="chip">{a.kind}</span> <code className="mono">{a.artifact_id}</code> <span className="sub">v{a.version} · {a.revisions} rev</span>
                      </span>
                      <button className="btn sm ghost" onClick={() => openHistory(a.kind, a.artifact_id)}><HistoryIcon className="" />History</button>
                    </div>
                  ))}
                </div>

                {selected && (
                  <>
                    <div className="divider" />
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                      <strong style={{ fontSize: 13 }}>{selected.kind}:{selected.id} — ledger</strong>
                      <button className="btn sm ghost" onClick={() => { setSelected(null); setHistory(null) }}>Close</button>
                    </div>
                    <div className="box" style={{ maxHeight: 240, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
                      {!history && <div className="empty" style={{ padding: 12 }}>Loading…</div>}
                      {history && history.length === 0 && <div className="empty" style={{ padding: 12 }}>No versions.</div>}
                      {history && [...history].reverse().map((v) => (
                        <div key={v.version} className="ledrow">
                          <div style={{ minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span className="chip accent">v{v.version}</span>
                              <span className={'dot ' + (v.behaviour_changed ? 'warn' : 'good')} />
                              <span className="sub">{v.origin}</span>
                            </div>
                            {v.sign_off && <div className="sub" style={{ marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis' }}>{v.sign_off.approver} — “{v.sign_off.reason}”</div>}
                          </div>
                          <button className="btn sm ghost" disabled={busy !== null} onClick={() => revert(v)}><RotateCcwIcon className="" />Revert</button>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Component relationship map */}
          <div className="card" style={{ marginTop: 14 }}><span className="stripe crit" />
            <div className="chead"><h3><NetworkIcon className="" style={{ verticalAlign: '-3px', marginRight: 6 }} />Component map</h3>
              <span className="sub">{graph ? `${graph.nodes.length} components · ${graph.edges.length} links` : '—'}</span>
            </div>
            <div className="cbody">
              {!graph || graph.nodes.length === 0
                ? <div className="empty" style={{ padding: 16 }}>No components yet — author a flow, rule, action or ontology to see how they wire together.</div>
                : <div className="mermaid-wrap"><Mermaid chart={mermaid} /></div>}
              {graph && graph.nodes.length > 0 && (
                <div className="maplegend">
                  <span><i style={{ background: '#6366f1' }} />flow</span>
                  <span><i style={{ background: '#0ea5e9' }} />action</span>
                  <span><i style={{ background: '#f59e0b' }} />rule</span>
                  <span><i style={{ background: '#10b981' }} />object type</span>
                </div>
              )}
            </div>
          </div>
        </div>

        <AiChatPanel
          open={chatOpen}
          onCollapse={() => setChatOpen(false)}
          historyKey={`${workspace}:studio`}
          authoringLabel={`${kind} · ${artifactId || '—'}`}
          placeholder={kind === 'rule' ? 'e.g. flag any discount above 20%…' : 'Describe the change…'}
          send={async (hist, input) => {
            const r = await studioDraft({
              kind, artifact_id: artifactId, instruction: input,
              history: hist.slice(0, -1).map((m) => ({ role: m.role, content: m.content }))
            })
            return { reply: r.reply, data: r.diff }
          }}
          onAccept={(d: ArtifactDiff) => {
            setKind(d.kind); setArtifactId(d.artifact_id)
            setDraftText(pretty(d.delta?.after)); storeDraft(`${workspace}:${d.kind}`, pretty(d.delta?.after))
            setDiff(d); setCanvasNonce((n) => n + 1)
            toast.success(d.behaviour_changed ? 'Draft loaded — behavioural, sign-off required' : 'Draft loaded — cosmetic')
          }}
        />
      </div>
    </div>
  )
}

const rowStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, fontSize: 13, padding: '5px 2px' }

function DiffPane({ title, text, accent }: { title: string; text: string; accent?: boolean }) {
  return (
    <div className="diffpane">
      <div className={'diffhead' + (accent ? ' accent' : '')}>{title}</div>
      <pre className="diffbody">{text}</pre>
    </div>
  )
}

// Build a mermaid graph from the component nodes/edges.
function toMermaid(nodes: StudioGraphNode[], edges: StudioGraphEdge[]): string {
  const key: Record<string, string> = {}
  nodes.forEach((n, i) => { key[n.id] = 'n' + i })
  const esc = (s: string) => (s || '').replace(/"/g, "'")
  const shape = (n: StudioGraphNode) => {
    const l = `"${esc(n.label)}"`
    if (n.kind === 'action') return `(${l})`
    if (n.kind === 'rule') return `{{${l}}}`
    if (n.kind === 'object') return `([${l}])`
    return `[${l}]`
  }
  const lines = [
    'graph LR',
    'classDef flow fill:#6366f1,stroke:#4f46e5,color:#fff;',
    'classDef action fill:#0ea5e9,stroke:#0284c7,color:#fff;',
    'classDef rule fill:#f59e0b,stroke:#b45309,color:#111;',
    'classDef object fill:#10b981,stroke:#059669,color:#fff;'
  ]
  nodes.forEach((n) => lines.push(`${key[n.id]}${shape(n)}:::${n.kind}`))
  edges.forEach((e) => { if (key[e.src] && key[e.dst]) lines.push(`${key[e.src]} -->|${esc(e.rel)}| ${key[e.dst]}`) })
  return lines.join('\n')
}
