import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { RefreshCwIcon } from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import {
  getRules, setRules, deleteRules, toggleRules, generateRules,
  type RulesSummary
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)

const SAMPLE_DSL = `rule "large discount needs finance review"  priority 10
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.15
    and approved_via == "slack"
then
    flag("Discount >15% approved over Slack — route to Finance for review")
end`

const SAMPLE_CONCEPTS = JSON.stringify(
  { APPROVAL: ['approved', 'authorized', 'granted approval', 'go ahead'] }, null, 2
)

export default function RulesNext() {
  const workspace = useSettingsStore.use.workspace()
  const [summary, setSummary] = useState<RulesSummary | null>(null)
  const [dsl, setDsl] = useState(SAMPLE_DSL)
  const [conceptsText, setConceptsText] = useState(SAMPLE_CONCEPTS)
  const [policy, setPolicy] = useState('')
  const [busy, setBusy] = useState(false)
  const [genResult, setGenResult] = useState<any>(null)
  const editorRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      setSummary(await getRules())
    } catch (e) {
      setSummary(null)
      toast.error(`Failed to load rules: ${errMsg(e)}`)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh, workspace])

  const parseConcepts = (): Record<string, string[]> => {
    const trimmed = conceptsText.trim()
    if (!trimmed) return {}
    return JSON.parse(trimmed)
  }

  const onSave = async () => {
    let concepts: Record<string, string[]>
    try { concepts = parseConcepts() } catch {
      toast.error('Concepts must be valid JSON (name → array of phrases).'); return
    }
    setBusy(true)
    try {
      const s = await setRules(dsl, concepts, true)
      setSummary(s)
      toast.success(`Rules saved (v${s.version}).`)
    } catch (e) { toast.error(`Save failed: ${errMsg(e)}`) } finally { setBusy(false) }
  }

  const onEdit = () => {
    if (!summary?.exists) return
    setDsl(summary.dsl || '')
    setConceptsText(JSON.stringify(summary.concepts_map || {}, null, 2))
    setGenResult(null)
    editorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    toast.info('Loaded the current policy into the editor below — edit, then Save.')
  }

  const onToggle = async () => {
    if (!summary?.exists) return
    setBusy(true)
    try { setSummary(await toggleRules(!summary.enabled)) }
    catch (e) { toast.error(`Toggle failed: ${errMsg(e)}`) } finally { setBusy(false) }
  }

  const onDelete = async () => {
    if (!confirm('Delete the rules policy for this workspace?')) return
    setBusy(true)
    try {
      await deleteRules()
      await refresh()
      toast.success('Rules policy deleted.')
    } catch (e) { toast.error(`Delete failed: ${errMsg(e)}`) } finally { setBusy(false) }
  }

  const onGenerate = async (save: boolean) => {
    if (!policy.trim()) { toast.error('Describe the policy in plain English first.'); return }
    setBusy(true)
    setGenResult(null)
    try {
      const r = await generateRules({ policy, save, max_repairs: 1 })
      setGenResult(r)
      if (r.valid) {
        setDsl(r.dsl)
        setConceptsText(JSON.stringify(r.concepts, null, 2))
        toast.success(save && r.saved ? 'Generated and applied.' : 'Generated — review below, then Save.')
        if (r.saved) await refresh()
      } else {
        toast.error(`Could not generate a valid policy after ${r.attempts} attempt(s).`)
      }
    } catch (e) { toast.error(`Generation failed: ${errMsg(e)}`) } finally { setBusy(false) }
  }

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Governance · Business Rules</div>
          <h1>The governance gate</h1>
          <p>Rules run before each decision is written to the graph — for <b className="mono">{workspace}</b>.</p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={refresh}><RefreshCwIcon className="" />Refresh</button>
          {summary?.exists
            ? <span className={'chip ' + (summary.enabled ? 'good' : '')} style={{ alignSelf: 'center' }}>
                {summary.enabled && <span className="cd" />}{summary.enabled ? 'enabled' : 'disabled'} · v{summary.version}
              </span>
            : <span className="chip" style={{ alignSelf: 'center' }}>no policy</span>}
        </div>
      </div>

      {/* Active policy */}
      {summary?.exists && (
        <div className="card" style={{ marginBottom: 14 }}><span className={'stripe ' + (summary.enabled ? 'good' : 'warn')} />
          <div className="chead"><h3>Active policy</h3>
            <span className="sub">{summary.enabled ? 'gate on' : 'gate off'}</span>
          </div>
          <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {summary.rules.map((r) => (
                <span key={r.name} className="chip">{r.name} · p{r.priority}</span>
              ))}
            </div>
            {summary.concepts.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {summary.concepts.map((c) => <span key={c} className="chip accent">{c}</span>)}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn sm" onClick={onEdit} disabled={busy}>Edit</button>
              <button className="btn sm" onClick={onToggle} disabled={busy}>{summary.enabled ? 'Disable gate' : 'Enable gate'}</button>
              <button className="btn sm warn" onClick={onDelete} disabled={busy}>Delete policy</button>
            </div>
          </div>
        </div>
      )}

      {/* Generate from plain English */}
      <div className="card" style={{ marginBottom: 14 }}><span className="stripe comm" />
        <div className="chead"><h3>Generate from plain English</h3><span className="sub">LLM-assisted</span></div>
        <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <textarea
            className="cgarea"
            style={{ minHeight: 88 }}
            value={policy}
            onChange={(e) => setPolicy(e.target.value)}
            placeholder="e.g. Any discount over 15% approved via Slack must be flagged for Finance review."
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn sm primary" onClick={() => onGenerate(false)} disabled={busy}>Generate (review)</button>
            <button className="btn sm" onClick={() => onGenerate(true)} disabled={busy}>Generate &amp; apply</button>
          </div>
          {genResult && (
            <div className="box" style={{ fontSize: 13 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                {genResult.valid ? '✓ valid' : '✗ invalid'} · {genResult.attempts} attempt(s)
                {genResult.explanation ? ` — ${genResult.explanation}` : ''}
              </div>
              {genResult.dry_run?.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {genResult.dry_run.map((d: any, i: number) => (
                    <li key={i} style={{ color: d.ok ? 'var(--good)' : 'var(--crit)' }}>
                      {d.name}: expected {d.expect}, got {d.outcome}
                    </li>
                  ))}
                </ul>
              )}
              {genResult.errors?.length > 0 && <div style={{ color: 'var(--crit)' }}>{genResult.errors.join('; ')}</div>}
            </div>
          )}
        </div>
      </div>

      {/* Editor */}
      <div className="card" ref={editorRef}><span className="stripe" />
        <div className="chead"><h3>{summary?.exists ? 'Policy editor · editing current' : 'Policy editor'}</h3></div>
        <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text2)' }}>Rule DSL</label>
          <textarea className="cgarea mono" style={{ minHeight: 180 }} value={dsl}
            onChange={(e) => setDsl(e.target.value)} spellCheck={false} />
          <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text2)' }}>Concept catalog (JSON: name → phrases)</label>
          <textarea className="cgarea mono" style={{ minHeight: 120 }} value={conceptsText}
            onChange={(e) => setConceptsText(e.target.value)} spellCheck={false} />
          <div><button className="btn primary" onClick={onSave} disabled={busy}>Save &amp; enable</button></div>
        </div>
      </div>
    </div>
  )
}
