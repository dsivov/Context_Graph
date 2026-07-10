import { useState } from 'react'
import { toast } from 'sonner'
import { SearchIcon, FileTextIcon } from 'lucide-react'
import { queryDataChunks, type RetrievedChunk } from '@/api/lightrag'
import { useSettingsStore } from '@/stores/settings'

const MODES = ['mix', 'local', 'global', 'hybrid', 'naive']

export default function ChunkInspector() {
  const workspace = useSettingsStore.use.workspace()
  const [q, setQ] = useState('')
  const [mode, setMode] = useState('mix')
  const [topK, setTopK] = useState(10)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ chunks: RetrievedChunk[]; entities: number; relations: number } | null>(null)

  const run = async () => {
    if (!q.trim()) { toast.error('Enter a query first.'); return }
    setBusy(true)
    try {
      const d = await queryDataChunks(q, mode, { chunk_top_k: topK, top_k: Math.max(20, topK * 2) })
      setResult({
        chunks: d.chunks || [],
        entities: (d.entities || []).length,
        relations: (d.relationships || []).length
      })
      toast.success(`${(d.chunks || []).length} chunk(s) retrieved`)
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Knowledge · Chunks</div>
          <h1>Chunk inspector</h1>
          <p>See the actual source text chunks a query retrieves from <b className="mono">{workspace}</b> — for evaluation and debugging.</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}><span className="stripe accent" />
        <div className="cbody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <textarea
            className="cgarea"
            style={{ minHeight: 68 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask a question — the chunks retrieved to answer it appear below. (⌘/Ctrl+Enter to run)"
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run() }}
          />
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ fontSize: 12.5, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 7 }}>
              Mode
              <select className="cgqinput" style={{ flex: 'none', width: 120 }} value={mode} onChange={(e) => setMode(e.target.value)}>
                {MODES.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12.5, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 7 }}>
              Chunks
              <input
                className="cgqinput" style={{ flex: 'none', width: 72 }} type="number" min={1} max={50} value={topK}
                onChange={(e) => setTopK(Math.max(1, Math.min(50, parseInt(e.target.value) || 10)))}
              />
            </label>
            <button className="btn primary" style={{ marginLeft: 'auto' }} onClick={run} disabled={busy}>
              <SearchIcon className="" />{busy ? 'Retrieving…' : 'Retrieve chunks'}
            </button>
          </div>
        </div>
      </div>

      {busy && <div className="spin" />}

      {result && !busy && (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <span className="chip accent"><span className="cd" />{result.chunks.length} chunks</span>
            <span className="chip"><span className="cd" style={{ background: 'var(--good)' }} />{result.entities} entities</span>
            <span className="chip"><span className="cd" style={{ background: 'var(--comm)' }} />{result.relations} relations</span>
          </div>
          {result.chunks.length === 0 ? (
            <div className="card"><div className="empty">No chunks retrieved for this query.</div></div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {result.chunks.map((c, i) => <ChunkCard key={c.chunk_id || i} c={c} i={i} />)}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function ChunkCard({ c, i }: { c: RetrievedChunk; i: number }) {
  const [open, setOpen] = useState(i < 3)
  const text = c.content || ''
  const long = text.length > 420
  return (
    <div className="card"><span className="stripe accent" />
      <div className="chead">
        <FileTextIcon className="" style={{ width: 15, height: 15, color: 'var(--muted)', flexShrink: 0 }} />
        <h3 style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {c.file_path || 'chunk'}
        </h3>
        <span className="sub mono" title={c.chunk_id}>{(c.chunk_id || '').slice(0, 18)}</span>
      </div>
      <div className="cbody">
        <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.6, color: 'var(--text2)' }}>
          {open || !long ? text : text.slice(0, 420) + '…'}
        </div>
        {long && (
          <button className="btn sm ghost" style={{ marginTop: 9 }} onClick={() => setOpen((o) => !o)}>
            {open ? 'Show less' : 'Show full chunk'}
          </button>
        )}
      </div>
    </div>
  )
}
