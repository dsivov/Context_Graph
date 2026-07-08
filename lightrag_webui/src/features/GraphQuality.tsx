import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Badge from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { useSettingsStore } from '@/stores/settings'
import {
  graphConnectivity,
  dedupScan,
  dedupSweep,
  dedupReview,
  entityMerges,
  entityUnmerge,
  garbageScan,
  quarantineList,
  quarantineRestore,
  quarantineDiscard,
  connectivityRescue,
  pruneIsolates,
  communityBuild,
  communityList,
  communityQuery,
  type ConnectivityReport
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)

function Stat({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div className="rounded-lg border p-3 min-w-[120px]">
      <div className="text-2xl font-bold" style={accent ? { color: accent } : undefined}>{value}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
    </div>
  )
}

export default function GraphQuality() {
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

  // Run an action with busy-state, toast, and an optional follow-up refresh.
  const run = useCallback(async (key: string, fn: () => Promise<any>, ok?: (r: any) => string, after?: () => void) => {
    setBusy(key)
    try {
      const r = await fn()
      toast.success(ok ? ok(r) : 'Done.')
      after?.()
      refreshConn()
      return r
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setBusy(null)
    }
  }, [refreshConn])

  const disabled = (k: string) => busy !== null && busy !== k

  return (
    <div className="mx-auto max-w-4xl p-6 space-y-5">
      <div>
        <h1 className="text-2xl font-bold">Graph Quality</h1>
        <p className="text-sm text-muted-foreground">
          Deduplication, garbage filtering, and connectivity for <b>{workspace || 'default'}</b>.
        </p>
      </div>

      {/* Connectivity dashboard */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Connectivity</CardTitle>
          <Button size="sm" variant="outline" onClick={refreshConn}>Refresh</Button>
        </CardHeader>
        <CardContent>
          {conn ? (
            <div className="flex flex-wrap gap-3">
              <Stat label="nodes" value={conn.total_nodes} />
              <Stat label="edges" value={conn.total_edges} />
              <Stat label="isolates" value={`${conn.isolated_nodes} (${conn.isolated_pct}%)`} accent="#f0a73c" />
              <Stat label="components" value={conn.connected_components} />
              <Stat label="largest comp." value={`${conn.largest_component_pct}%`} accent="#3ecf8e" />
              <Stat label="mean degree" value={conn.degree.mean} />
            </div>
          ) : <div className="text-sm text-muted-foreground">No data.</div>}
        </CardContent>
      </Card>

      {/* Deduplication */}
      <Card>
        <CardHeader><CardTitle>Deduplication</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Merge duplicate entities (case/suffix/embedding), reversibly. Ambiguous pairs queue for the LLM sweep.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={disabled('dscanp')}
              onClick={() => run('dscanp', () => dedupScan(false), (r) => `Preview: would merge ${r.merged}, queue ${r.queued}`)}>
              Preview scan
            </Button>
            <Button size="sm" disabled={disabled('dscan')}
              onClick={() => run('dscan', () => dedupScan(true), (r) => `Merged ${r.merged}, queued ${r.queued}`)}>
              Scan &amp; merge
            </Button>
            <Button size="sm" variant="outline" disabled={disabled('dsweep')}
              onClick={() => run('dsweep', () => dedupSweep(), (r) => `Sweep: merged ${r.merged}, rejected ${r.rejected}`)}>
              Run LLM sweep
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled('drev')}
              onClick={() => run('drev', () => dedupReview(), () => 'Loaded review queue', undefined).then((r) => r && setReview(r))}>
              Review queue
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled('dmerge')}
              onClick={() => run('dmerge', () => entityMerges(), (r) => `${r.merges.length} merge(s)`).then((r) => r && setMerges(r.merges))}>
              Merge audit
            </Button>
          </div>
          {review && (
            <div className="text-sm rounded border p-2">
              <b>{review.summary?.pending_review ?? 0}</b> pending pair(s) ·
              <b> {review.summary?.merges_live ?? 0}</b> live merge(s)
            </div>
          )}
          {merges && (
            <div className="space-y-1 max-h-56 overflow-auto rounded border p-2">
              {merges.length === 0 && <div className="text-sm text-muted-foreground">No merges yet.</div>}
              {merges.map((m) => (
                <div key={m.id} className="flex items-center justify-between text-sm">
                  <span><code>{m.alias}</code> → <code>{m.into}</code> <Badge variant="secondary">{m.method}</Badge></span>
                  <Button size="sm" variant="ghost"
                    onClick={() => run('un' + m.id, () => entityUnmerge(m.id), () => `Unmerged ${m.alias}`,
                      () => setMerges((prev) => (prev || []).filter((x) => x.id !== m.id)))}>
                    Unmerge
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Garbage + quarantine */}
      <Card>
        <CardHeader><CardTitle>Garbage &amp; quarantine</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Remove pronoun/hash/env-var/path junk (restorable). Rejects go to the quarantine.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={disabled('gpre')}
              onClick={() => run('gpre', () => garbageScan(false), (r) => `Preview: ${r.quarantined} garbage node(s)`)}>
              Preview scan
            </Button>
            <Button size="sm" disabled={disabled('gscan')}
              onClick={() => run('gscan', () => garbageScan(true), (r) => `Removed ${r.removed} (quarantined)`)}>
              Scan &amp; remove
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled('qlist')}
              onClick={() => run('qlist', () => quarantineList(), (r) => `${r.summary.count} quarantined`).then((r) => r && setQuarantine(r.items))}>
              Show quarantine
            </Button>
          </div>
          {quarantine && (
            <div className="space-y-1 max-h-64 overflow-auto rounded border p-2">
              {quarantine.length === 0 && <div className="text-sm text-muted-foreground">Quarantine is empty.</div>}
              {quarantine.map((q) => (
                <div key={q.name} className="flex items-center justify-between text-sm gap-2">
                  <span className="truncate"><code>{q.name}</code> <span className="text-muted-foreground">— {q.reason}</span></span>
                  <span className="flex gap-1 shrink-0">
                    <Button size="sm" variant="ghost"
                      onClick={() => run('r' + q.name, () => quarantineRestore(q.name), () => `Restored ${q.name}`,
                        () => setQuarantine((p) => (p || []).filter((x) => x.name !== q.name)))}>Restore</Button>
                    <Button size="sm" variant="ghost"
                      onClick={() => run('d' + q.name, () => quarantineDiscard(q.name), () => `Discarded ${q.name}`,
                        () => setQuarantine((p) => (p || []).filter((x) => x.name !== q.name)))}>Discard</Button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Connectivity repair */}
      <Card>
        <CardHeader><CardTitle>Connectivity repair</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Reconnect isolated nodes with LLM-verified real edges. Prune the ones that can't be connected (opt-in, restorable).
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={disabled('rescue')}
              onClick={() => run('rescue', () => connectivityRescue(true, 20), (r) => `Reconnected ${r.connected}/${r.isolates_scanned}, +${r.edges_added} edges`)}>
              Rescue 20 isolates
            </Button>
            <Button size="sm" variant="outline" disabled={disabled('ppre')}
              onClick={() => run('ppre', () => pruneIsolates(false), (r) => `${r.isolates} degree-0 isolate(s) — preview only`)}>
              Preview prune
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled('prune')}
              onClick={() => { if (confirm('Move all degree-0 isolates to the (restorable) quarantine?')) run('prune', () => pruneIsolates(true), (r) => `Pruned ${r.removed} isolate(s)`) }}>
              Prune isolates
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Communities */}
      <Card>
        <CardHeader><CardTitle>Communities · thematic global</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Detect themes (Louvain), summarise each, and ask holistic questions across them.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={disabled('cbuild')}
              onClick={() => run('cbuild', () => communityBuild(), (r) => `Built ${r.communities} communities`)}>
              Build communities
            </Button>
            <Button size="sm" variant="ghost" disabled={disabled('clist')}
              onClick={() => run('clist', () => communityList(), (r) => `${r.summary.communities} communities`).then((r) => r && setCommunities(r.communities))}>
              List communities
            </Button>
          </div>
          {communities && (
            <div className="space-y-1 max-h-56 overflow-auto rounded border p-2">
              {communities.length === 0 && <div className="text-sm text-muted-foreground">None built yet.</div>}
              {[...communities].sort((a, b) => b.size - a.size).map((c) => (
                <div key={c.id} className="text-sm"><Badge variant="secondary">{c.size}</Badge> {c.title}</div>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <Input placeholder="Ask a thematic question…" value={cq}
              onChange={(e) => setCq(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && cq.trim()) run('cq', () => communityQuery(cq), () => 'Answered').then((r) => r && setCqAnswer(r)) }} />
            <Button size="sm" disabled={disabled('cq') || !cq.trim()}
              onClick={() => run('cq', () => communityQuery(cq), () => 'Answered').then((r) => r && setCqAnswer(r))}>Ask</Button>
          </div>
          {cqAnswer && (
            <div className="rounded border p-3 text-sm space-y-2">
              <div className="text-xs text-muted-foreground">
                Themes: {cqAnswer.communities.map((c) => c.title).join(' · ') || '—'}
              </div>
              <div className="whitespace-pre-wrap">{cqAnswer.response}</div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
