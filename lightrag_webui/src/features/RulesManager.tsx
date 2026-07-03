import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import Button from '@/components/ui/Button'
import Textarea from '@/components/ui/Textarea'
import Badge from '@/components/ui/Badge'
import Separator from '@/components/ui/Separator'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { useSettingsStore } from '@/stores/settings'
import {
  getRules,
  setRules,
  deleteRules,
  toggleRules,
  generateRules,
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
  { APPROVAL: ['approved', 'authorized', 'granted approval', 'go ahead'] },
  null,
  2
)

export default function RulesManager() {
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
      const s = await getRules()
      setSummary(s)
    } catch (e) {
      setSummary(null)
      toast.error(`Failed to load rules: ${errMsg(e)}`)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh, workspace])

  const parseConcepts = (): Record<string, string[]> => {
    const trimmed = conceptsText.trim()
    if (!trimmed) return {}
    return JSON.parse(trimmed)
  }

  const onSave = async () => {
    let concepts: Record<string, string[]>
    try {
      concepts = parseConcepts()
    } catch {
      toast.error('Concepts must be valid JSON (name → array of phrases).')
      return
    }
    setBusy(true)
    try {
      const s = await setRules(dsl, concepts, true)
      setSummary(s)
      toast.success(`Rules saved (v${s.version}).`)
    } catch (e) {
      toast.error(`Save failed: ${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
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
    try {
      setSummary(await toggleRules(!summary.enabled))
    } catch (e) {
      toast.error(`Toggle failed: ${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const onDelete = async () => {
    if (!confirm('Delete the rules policy for this workspace?')) return
    setBusy(true)
    try {
      await deleteRules()
      await refresh()
      toast.success('Rules policy deleted.')
    } catch (e) {
      toast.error(`Delete failed: ${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const onGenerate = async (save: boolean) => {
    if (!policy.trim()) {
      toast.error('Describe the policy in plain English first.')
      return
    }
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
          <h2 className="text-xl font-bold">Business Rules</h2>
          <p className="text-sm text-muted-foreground">
            Governance gate for workspace <span className="font-mono">{workspace || 'default'}</span>.
            Rules run before each decision is written to the graph.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {summary?.exists ? (
            <Badge className={summary.enabled ? 'bg-emerald-500' : 'bg-zinc-400'}>
              {summary.enabled ? 'ENABLED' : 'disabled'} · v{summary.version}
            </Badge>
          ) : (
            <Badge className="bg-zinc-400">no policy</Badge>
          )}
        </div>
      </div>

      {/* Current policy status */}
      {summary?.exists && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Active policy</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {summary.rules.map((r) => (
                <Badge key={r.name} variant="outline">
                  {r.name} · p{r.priority}
                </Badge>
              ))}
            </div>
            <div className="flex flex-wrap gap-1">
              {summary.concepts.map((c) => (
                <Badge key={c} className="bg-sky-500">{c}</Badge>
              ))}
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={onEdit} disabled={busy}>
                Edit
              </Button>
              <Button size="sm" variant="outline" onClick={onToggle} disabled={busy}>
                {summary.enabled ? 'Disable gate' : 'Enable gate'}
              </Button>
              <Button size="sm" variant="destructive" onClick={onDelete} disabled={busy}>
                Delete policy
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Generate from natural language */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Generate from plain English</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Textarea
            value={policy}
            onChange={(e) => setPolicy(e.target.value)}
            placeholder="e.g. Any discount over 15% approved via Slack must be flagged for Finance review."
            className="min-h-20"
          />
          <div className="flex gap-2">
            <Button onClick={() => onGenerate(false)} disabled={busy}>Generate (review)</Button>
            <Button variant="outline" onClick={() => onGenerate(true)} disabled={busy}>
              Generate &amp; apply
            </Button>
          </div>
          {genResult && (
            <div className="rounded-md border p-3 text-sm">
              <div className="mb-1 font-medium">
                {genResult.valid ? '✓ valid' : '✗ invalid'} · {genResult.attempts} attempt(s)
                {genResult.explanation ? ` — ${genResult.explanation}` : ''}
              </div>
              {genResult.dry_run?.length > 0 && (
                <ul className="ml-4 list-disc">
                  {genResult.dry_run.map((d: any, i: number) => (
                    <li key={i} className={d.ok ? 'text-emerald-600' : 'text-red-600'}>
                      {d.name}: expected {d.expect}, got {d.outcome}
                    </li>
                  ))}
                </ul>
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
            {summary?.exists ? 'Policy editor · editing current' : 'Policy editor'}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <label className="text-sm font-medium">Rule DSL</label>
          <Textarea
            value={dsl}
            onChange={(e) => setDsl(e.target.value)}
            className="min-h-48 font-mono text-xs"
            spellCheck={false}
          />
          <label className="text-sm font-medium">Concept catalog (JSON: name → phrases)</label>
          <Textarea
            value={conceptsText}
            onChange={(e) => setConceptsText(e.target.value)}
            className="min-h-32 font-mono text-xs"
            spellCheck={false}
          />
          <div>
            <Button onClick={onSave} disabled={busy}>Save &amp; enable</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
