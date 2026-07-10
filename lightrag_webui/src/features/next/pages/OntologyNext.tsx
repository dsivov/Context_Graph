import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { RefreshCwIcon } from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import {
  getOntology, setOntology, deleteOntology, generateOntology,
  type OntologySummary, type OntologyDoc
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)

const SAMPLE: OntologyDoc = {
  name: 'sales',
  object_types: [
    { name: 'Person', description: 'a human', properties: [{ name: 'email', kind: 'string' }] },
    {
      name: 'Order', description: 'a deal',
      properties: [
        { name: 'value', kind: 'money', required: true },
        { name: 'status', kind: 'enum', enum_values: ['open', 'won', 'lost'] }
      ]
    }
  ],
  link_types: [
    { name: 'approved', source_types: ['Person'], target_types: ['Order'], cardinality: '1:N' }
  ]
}

export default function OntologyNext() {
  const workspace = useSettingsStore.use.workspace()
  const [summary, setSummary] = useState<OntologySummary | null>(null)
  const [docText, setDocText] = useState(JSON.stringify(SAMPLE, null, 2))
  const [description, setDescription] = useState('')
  const [extend, setExtend] = useState(true)
  const [busy, setBusy] = useState(false)
  const [genResult, setGenResult] = useState<any>(null)
  const editorRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await getOntology()
      setSummary(s)
      if (s.exists && s.ontology) setDocText(JSON.stringify(s.ontology, null, 2))
    } catch (e) {
      setSummary(null)
      toast.error(`Failed to load ontology: ${errMsg(e)}`)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh, workspace])

  const onSave = async () => {
    let doc: OntologyDoc
    try { doc = JSON.parse(docText) } catch { toast.error('Ontology must be valid JSON.'); return }
    setBusy(true)
    try {
      const s = await setOntology(doc)
      setSummary(s)
      toast.success(`Ontology saved (v${s.version}).`)
    } catch (e) { toast.error(`Save failed: ${errMsg(e)}`) } finally { setBusy(false) }
  }

  const onEdit = () => {
    if (summary?.exists && summary.ontology) setDocText(JSON.stringify(summary.ontology, null, 2))
    setGenResult(null)
    editorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    toast.info('Loaded the current schema into the editor below — edit, then Save.')
  }

  const onDelete = async () => {
    if (!confirm('Delete the ontology for this workspace?')) return
    setBusy(true)
    try {
      await deleteOntology()
      await refresh()
      toast.success('Ontology deleted.')
    } catch (e) { toast.error(`Delete failed: ${errMsg(e)}`) } finally { setBusy(false) }
  }

  const onGenerate = async (save: boolean) => {
    if (!description.trim()) { toast.error('Describe the domain first.'); return }
    setBusy(true)
    setGenResult(null)
    try {
      const r = await generateOntology({ description, extend, save, max_repairs: 1 })
      setGenResult(r)
      if (r.valid) {
        setDocText(JSON.stringify(r.ontology, null, 2))
        toast.success(save && r.saved ? 'Generated and saved.' : 'Generated — review below, then Save.')
        if (r.saved) await refresh()
      } else {
        toast.error(`Could not generate a valid ontology after ${r.attempts} attempt(s).`)
      }
    } catch (e) { toast.error(`Generation failed: ${errMsg(e)}`) } finally { setBusy(false) }
  }

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Governance · Ontology</div>
          <h1>Typed schema</h1>
          <p>Object types, link types, and the properties extraction is validated against — for <b className="mono">{workspace}</b>.</p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={refresh}><RefreshCwIcon className="" />Refresh</button>
          {summary?.exists
            ? <span className="chip good" style={{ alignSelf: 'center' }}><span className="cd" />{summary.name} · v{summary.version}</span>
            : <span className="chip" style={{ alignSelf: 'center' }}>no ontology</span>}
        </div>
      </div>

      {/* Schema overview */}
      {summary?.exists && (
        <div className="card" style={{ marginBottom: 14 }}><span className="stripe accent" />
          <div className="chead"><h3>Schema overview</h3></div>
          <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <div className="tl" style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.4px', marginBottom: 6 }}>Object types</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {summary.object_types.map((o) => (
                  <span key={o.name} className="chip accent" title={o.properties.map((p) => `${p.name}:${p.kind}`).join(', ')}>
                    {o.name} ({o.properties.length})
                  </span>
                ))}
              </div>
            </div>
            <div>
              <div className="tl" style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.4px', marginBottom: 6 }}>Link types</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {summary.link_types.map((l) => (
                  <div key={l.name} className="mono" style={{ fontSize: 12 }}>
                    <b>{l.name}</b>: {l.source_types.join('|') || 'any'} → {l.target_types.join('|') || 'any'}{' '}
                    <span style={{ color: 'var(--muted)' }}>[{l.cardinality}]</span>
                  </div>
                ))}
              </div>
            </div>
            {summary.lint.length > 0 && (
              <div className="box" style={{ borderColor: 'var(--warn)', color: 'var(--warn)', fontSize: 12.5 }}>
                <b>Lint:</b> {summary.lint.join('; ')}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn sm" onClick={onEdit} disabled={busy}>Edit</button>
              <button className="btn sm warn" onClick={onDelete} disabled={busy}>Delete ontology</button>
            </div>
          </div>
        </div>
      )}

      {/* Generate from description */}
      <div className="card" style={{ marginBottom: 14 }}><span className="stripe comm" />
        <div className="chead"><h3>Generate from a description</h3><span className="sub">LLM-assisted</span></div>
        <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <textarea
            className="cgarea"
            style={{ minHeight: 88 }}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. A school district. Schools employ teachers. Teachers teach courses to students. Students enroll in courses and receive grades."
          />
          <label className="checkrow">
            <input type="checkbox" checked={extend} onChange={(e) => setExtend(e.target.checked)} />
            Extend the existing ontology (if any)
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn sm primary" onClick={() => onGenerate(false)} disabled={busy}>Generate (review)</button>
            <button className="btn sm" onClick={() => onGenerate(true)} disabled={busy}>Generate &amp; save</button>
          </div>
          {genResult && (
            <div className="box" style={{ fontSize: 13 }}>
              <div style={{ fontWeight: 600, marginBottom: 3 }}>
                {genResult.valid ? '✓ valid' : '✗ invalid'} · {genResult.attempts} attempt(s)
                {genResult.explanation ? ` — ${genResult.explanation}` : ''}
              </div>
              {genResult.dry_run && (
                <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                  sample check: {genResult.dry_run.conforming}/{genResult.dry_run.total} conform
                  {genResult.dry_run.violations ? `, ${genResult.dry_run.violations} flagged` : ''}
                </div>
              )}
              {genResult.errors?.length > 0 && (
                <div style={{ color: 'var(--crit)' }}>{genResult.errors.join('; ')}</div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Editor */}
      <div className="card" ref={editorRef}><span className="stripe" />
        <div className="chead"><h3>{summary?.exists ? 'Ontology editor · editing current' : 'Ontology editor'}</h3><span className="sub">JSON</span></div>
        <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <textarea
            className="cgarea mono"
            style={{ minHeight: 340 }}
            value={docText}
            onChange={(e) => setDocText(e.target.value)}
            spellCheck={false}
          />
          <div><button className="btn primary" onClick={onSave} disabled={busy}>Save</button></div>
        </div>
      </div>
    </div>
  )
}
