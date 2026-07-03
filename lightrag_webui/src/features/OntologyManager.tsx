import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import Button from '@/components/ui/Button'
import Textarea from '@/components/ui/Textarea'
import Badge from '@/components/ui/Badge'
import Separator from '@/components/ui/Separator'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { useSettingsStore } from '@/stores/settings'
import {
  getOntology,
  setOntology,
  deleteOntology,
  generateOntology,
  type OntologySummary,
  type OntologyDoc
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)

const SAMPLE: OntologyDoc = {
  name: 'sales',
  object_types: [
    { name: 'Person', description: 'a human', properties: [{ name: 'email', kind: 'string' }] },
    {
      name: 'Order',
      description: 'a deal',
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

export default function OntologyManager() {
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

  useEffect(() => {
    refresh()
  }, [refresh, workspace])

  const onSave = async () => {
    let doc: OntologyDoc
    try {
      doc = JSON.parse(docText)
    } catch {
      toast.error('Ontology must be valid JSON.')
      return
    }
    setBusy(true)
    try {
      const s = await setOntology(doc)
      setSummary(s)
      toast.success(`Ontology saved (v${s.version}).`)
    } catch (e) {
      toast.error(`Save failed: ${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const onEdit = () => {
    if (summary?.exists && summary.ontology) {
      setDocText(JSON.stringify(summary.ontology, null, 2))
    }
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
    } catch (e) {
      toast.error(`Delete failed: ${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const onGenerate = async (save: boolean) => {
    if (!description.trim()) {
      toast.error('Describe the domain first.')
      return
    }
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
    } catch (e) {
      toast.error(`Generation failed: ${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Ontology</h2>
          <p className="text-sm text-muted-foreground">
            Typed schema for workspace <span className="font-mono">{workspace || 'default'}</span> —
            object types, link types, and the properties extraction is validated against.
          </p>
        </div>
        {summary?.exists ? (
          <Badge className="bg-emerald-500">
            {summary.name} · v{summary.version}
          </Badge>
        ) : (
          <Badge className="bg-zinc-400">no ontology</Badge>
        )}
      </div>

      {/* Overview */}
      {summary?.exists && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Schema overview</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div>
              <div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Object types</div>
              <div className="flex flex-wrap gap-2">
                {summary.object_types.map((o) => (
                  <Badge key={o.name} variant="secondary" title={o.properties.map((p) => `${p.name}:${p.kind}`).join(', ')}>
                    {o.name} ({o.properties.length})
                  </Badge>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Link types</div>
              <div className="flex flex-col gap-1 text-sm">
                {summary.link_types.map((l) => (
                  <div key={l.name} className="font-mono text-xs">
                    <span className="font-semibold">{l.name}</span>: {l.source_types.join('|') || 'any'} →{' '}
                    {l.target_types.join('|') || 'any'} <span className="text-muted-foreground">[{l.cardinality}]</span>
                  </div>
                ))}
              </div>
            </div>
            {summary.lint.length > 0 && (
              <div className="rounded-md border border-amber-400 bg-amber-50 p-2 text-xs text-amber-800">
                <b>Lint:</b> {summary.lint.join('; ')}
              </div>
            )}
            <div className="flex gap-2">
              <Button size="sm" onClick={onEdit} disabled={busy}>
                Edit
              </Button>
              <Button size="sm" variant="destructive" onClick={onDelete} disabled={busy}>
                Delete ontology
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Generate from description */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Generate from a description</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. A school district. Schools employ teachers. Teachers teach courses to students. Students enroll in courses and receive grades."
            className="min-h-20"
          />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={extend} onChange={(e) => setExtend(e.target.checked)} />
            Extend the existing ontology (if any)
          </label>
          <div className="flex gap-2">
            <Button onClick={() => onGenerate(false)} disabled={busy}>Generate (review)</Button>
            <Button variant="outline" onClick={() => onGenerate(true)} disabled={busy}>
              Generate &amp; save
            </Button>
          </div>
          {genResult && (
            <div className="rounded-md border p-3 text-sm">
              <div className="mb-1 font-medium">
                {genResult.valid ? '✓ valid' : '✗ invalid'} · {genResult.attempts} attempt(s)
                {genResult.explanation ? ` — ${genResult.explanation}` : ''}
              </div>
              {genResult.dry_run && (
                <div className="text-xs text-muted-foreground">
                  sample check: {genResult.dry_run.conforming}/{genResult.dry_run.total} conform
                  {genResult.dry_run.violations ? `, ${genResult.dry_run.violations} flagged` : ''}
                </div>
              )}
              {genResult.errors?.length > 0 && (
                <div className="text-red-600">{genResult.errors.join('; ')}</div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Separator />

      {/* Editor */}
      <Card ref={editorRef}>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            {summary?.exists ? 'Ontology editor (JSON) · editing current' : 'Ontology editor (JSON)'}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Textarea
            value={docText}
            onChange={(e) => setDocText(e.target.value)}
            className="min-h-96 font-mono text-xs"
            spellCheck={false}
          />
          <div>
            <Button onClick={onSave} disabled={busy}>Save</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
