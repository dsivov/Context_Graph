import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  RefreshCwIcon, FileDiffIcon, HistoryIcon, RotateCcwIcon, CheckIcon,
  ShieldAlertIcon, WandSparklesIcon
} from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import AiChatModal from '@/features/next/AiChatModal'
import {
  studioArtifacts, studioHistory, studioPropose, studioApply, studioRevert, studioDraft,
  type StudioKind, type StudioArtifactRow, type StudioVersion, type ArtifactDiff
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)
const KINDS: StudioKind[] = ['rule', 'ontology', 'flow', 'action']

const pretty = (v: any) => { try { return JSON.stringify(v, null, 2) } catch { return String(v) } }

// A starter draft per kind so the JSON editor is never a blank wall.
const TEMPLATE: Record<StudioKind, any> = {
  rule: {
    dsl: 'rule "discount_cap"\nwhen\n    percent > 0.20\nthen\n    flag("exceeds the discount cap")\nend\n',
    concepts: {}, enabled: true, fixtures: []
  },
  ontology: { name: 'domain', object_types: [], link_types: [] },
  flow: { id: 'intake', on_event: 'request.submitted', nodes: [{ id: 'in', kind: 'event' }], edges: [] },
  action: { name: 'catalog', actions: [] }
}

export default function Studio() {
  const workspace = useSettingsStore.use.workspace()
  const [busy, setBusy] = useState<string | null>(null)
  const [artifacts, setArtifacts] = useState<StudioArtifactRow[] | null>(null)

  // authoring
  const [kind, setKind] = useState<StudioKind>('rule')
  const [artifactId, setArtifactId] = useState('policy')
  const [draftText, setDraftText] = useState(pretty(TEMPLATE.rule))
  const [diff, setDiff] = useState<ArtifactDiff | null>(null)

  // sign-off
  const [approver, setApprover] = useState('')
  const [reason, setReason] = useState('')

  // history panel
  const [selected, setSelected] = useState<{ kind: string; id: string } | null>(null)
  const [history, setHistory] = useState<StudioVersion[] | null>(null)

  // AI authoring chat
  const [aiOpen, setAiOpen] = useState(false)
  const aiCapable = kind === 'rule' || kind === 'ontology'

  const refreshArtifacts = useCallback(async () => {
    try { setArtifacts((await studioArtifacts()).artifacts) }
    catch (e) { setArtifacts(null); toast.error(`Studio: ${errMsg(e)}`) }
  }, [])

  useEffect(() => {
    refreshArtifacts()
    setDiff(null); setSelected(null); setHistory(null)
  }, [refreshArtifacts, workspace])

  const onKindChange = (k: StudioKind) => {
    setKind(k)
    setDraftText(pretty(TEMPLATE[k]))
    setArtifactId(k === 'rule' ? 'policy' : k === 'ontology' ? 'ontology' : k === 'action' ? 'catalog' : 'intake')
    setDiff(null)
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
      toast.error('This change alters behaviour — an approver and a reason are required.')
      return
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
    setSelected({ kind: k, id })
    setHistory(null)
    try { setHistory((await studioHistory(k, id)).history) }
    catch (e) { toast.error(errMsg(e)) }
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
          <button className="btn ghost" onClick={refreshArtifacts} disabled={busy !== null}>
            <RefreshCwIcon className="" />Refresh
          </button>
        </div>
      </div>

      <div className="grid-cards" style={{ gridTemplateColumns: 'minmax(0,1.3fr) minmax(0,1fr)' }}>
        {/* Author + diff review */}
        <div className="card"><span className="stripe accent" />
          <div className="chead"><h3>Propose a change</h3>
            <span className="sub">{kind} · {artifactId || '—'}</span>
          </div>

          <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <div className="segmented">
                {KINDS.map((k) => (
                  <button key={k} className={'seg' + (kind === k ? ' on' : '')} onClick={() => onKindChange(k)}>{k}</button>
                ))}
              </div>
              <input className="cgqinput" style={{ flex: 1, minWidth: 140 }} placeholder="artifact id"
                value={artifactId} onChange={(e) => setArtifactId(e.target.value)} />
            </div>

            <label className="fieldlabel">Draft ({kind}) — edit the JSON, then propose</label>
            <textarea className="codearea" spellCheck={false} value={draftText}
              onChange={(e) => setDraftText(e.target.value)} rows={12} />

            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn sm primary" disabled={busy !== null || !artifactId.trim()} onClick={propose}>
                <FileDiffIcon className="" />Propose
              </button>
              <button className="btn sm ghost" title={aiCapable ? 'Draft this change by chatting with the assistant' : 'AI authoring is available for rules and ontology'}
                disabled={busy !== null || !aiCapable} onClick={() => setAiOpen(true)}>
                <WandSparklesIcon className="" />Author with AI
              </button>
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
                  <input className="cgqinput" style={{ flex: '1 1 160px' }} placeholder="Approver (who signs)"
                    value={approver} onChange={(e) => setApprover(e.target.value)} />
                  <input className="cgqinput" style={{ flex: '2 1 220px' }} placeholder="Reason for the change"
                    value={reason} onChange={(e) => setReason(e.target.value)} />
                </div>
              )}
              <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                <button className="btn sm primary" disabled={busy !== null} onClick={apply}>
                  <CheckIcon className="" />{needsSignoff ? 'Sign off & apply' : 'Apply'}
                </button>
                <button className="btn sm ghost" disabled={busy !== null} onClick={() => setDiff(null)}>Discard</button>
              </div>
            </div>
          )}
        </div>

        {/* Artifacts + history */}
        <div className="card"><span className="stripe comm" />
          <div className="chead"><h3>Tracked artifacts</h3>
            {artifacts && <span className="sub">{artifacts.length}</span>}
          </div>
          <div className="cbody" style={{ paddingTop: 0 }}>
            <div className="box" style={{ maxHeight: 190, overflow: 'auto' }}>
              {!artifacts && <div className="empty" style={{ padding: 12 }}>Loading…</div>}
              {artifacts && artifacts.length === 0 && <div className="empty" style={{ padding: 12 }}>No artifacts authored yet.</div>}
              {artifacts?.map((a) => (
                <div key={a.kind + ':' + a.artifact_id} style={rowStyle}>
                  <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    <span className="chip">{a.kind}</span> <code className="mono">{a.artifact_id}</code>{' '}
                    <span className="sub">v{a.version} · {a.revisions} rev</span>
                  </span>
                  <button className="btn sm ghost" onClick={() => openHistory(a.kind, a.artifact_id)}>
                    <HistoryIcon className="" />History
                  </button>
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
                <div className="box" style={{ maxHeight: 260, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
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
                        {v.sign_off && (
                          <div className="sub" style={{ marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {v.sign_off.approver} — “{v.sign_off.reason}”
                          </div>
                        )}
                      </div>
                      <button className="btn sm ghost" title="Re-apply this version as a new signed revision"
                        disabled={busy !== null} onClick={() => revert(v)}>
                        <RotateCcwIcon className="" />Revert
                      </button>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <AiChatModal
        open={aiOpen}
        onClose={() => setAiOpen(false)}
        title={`Author a ${kind} with AI`}
        subtitle={`${artifactId || '—'} · remembers this conversation`}
        historyKey={`${workspace}:${kind}:${artifactId}`}
        placeholder={kind === 'rule' ? 'e.g. flag any discount above 20%…' : 'Describe the domain or types…'}
        intro={`Describe the ${kind} change in plain English. The assistant drafts it, validates it, and you review the diff before signing off.`}
        send={async (hist, input) => {
          const r = await studioDraft({
            kind, artifact_id: artifactId, instruction: input,
            history: hist.slice(0, -1).map((m) => ({ role: m.role, content: m.content }))
          })
          return { reply: r.reply, data: r.diff }
        }}
        onAccept={(d: ArtifactDiff) => {
          setDiff(d)
          setDraftText(pretty(d.delta?.after))
          toast.success(d.behaviour_changed ? 'Draft loaded — behavioural, sign-off required' : 'Draft loaded — cosmetic')
        }}
        acceptLabel="Load this draft into review"
      />
    </div>
  )
}

const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  gap: 8, fontSize: 13, padding: '5px 2px'
}

function DiffPane({ title, text, accent }: { title: string; text: string; accent?: boolean }) {
  return (
    <div className="diffpane">
      <div className={'diffhead' + (accent ? ' accent' : '')}>{title}</div>
      <pre className="diffbody">{text}</pre>
    </div>
  )
}
