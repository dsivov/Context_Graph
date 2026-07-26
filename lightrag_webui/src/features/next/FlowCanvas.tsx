import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PlusIcon, Trash2Icon, LinkIcon, XIcon } from 'lucide-react'

/**
 * BPMN-lite visual flow editor. Renders a FlowDefinition (event/task/gateway/
 * state/timer nodes + edges) as draggable boxes with SVG connectors, and emits
 * the edited flow via `onChange`. Node positions are session-local (auto-laid-
 * out by depth from the event node); the emitted flow carries only the schema
 * fields, so the backend contract is untouched.
 */

type Node = { id: string; kind: string; ref?: string | null; config?: Record<string, any> }
type Edge = { src: string; dst: string; when?: string | null }
type Flow = { id: string; on_event: string; nodes: Node[]; edges: Edge[]; version?: number; test_cases?: any[] }

const KINDS = ['event', 'task', 'gateway', 'state', 'timer'] as const
const KIND_COLOR: Record<string, string> = {
  event: 'var(--good)', task: 'var(--accent)', gateway: 'var(--warn)',
  state: 'var(--comm)', timer: 'var(--crit)'
}
const NW = 158, NH = 62, COLW = 210, ROWH = 104

function autoLayout(nodes: Node[], edges: Edge[]): Record<string, { x: number; y: number }> {
  // depth = longest distance from the (first) event node along edges
  const depth: Record<string, number> = {}
  const entry = nodes.find((n) => n.kind === 'event')?.id ?? nodes[0]?.id
  const adj: Record<string, string[]> = {}
  edges.forEach((e) => { (adj[e.src] ||= []).push(e.dst) })
  const visit = (id: string, d: number, seen: Set<string>) => {
    if (seen.has(id)) return
    seen.add(id)
    depth[id] = Math.max(depth[id] ?? 0, d)
    for (const nxt of adj[id] || []) visit(nxt, d + 1, seen)
    seen.delete(id)
  }
  if (entry) visit(entry, 0, new Set())
  nodes.forEach((n) => { if (depth[n.id] === undefined) depth[n.id] = 0 })
  const byCol: Record<number, string[]> = {}
  nodes.forEach((n) => { (byCol[depth[n.id]] ||= []).push(n.id) })
  const pos: Record<string, { x: number; y: number }> = {}
  Object.entries(byCol).forEach(([col, ids]) => {
    ids.forEach((id, i) => { pos[id] = { x: +col * COLW + 30, y: i * ROWH + 24 } })
  })
  return pos
}

export default function FlowCanvas({ initial, onChange }: { initial: any; onChange: (flow: Flow) => void }) {
  const [meta, setMeta] = useState({ id: initial?.id || 'flow', on_event: initial?.on_event || 'event.type' })
  const [nodes, setNodes] = useState<Node[]>(() => (initial?.nodes?.length ? initial.nodes : [{ id: 'in', kind: 'event' }]))
  const [edges, setEdges] = useState<Edge[]>(() => initial?.edges || [])
  const [pos, setPos] = useState<Record<string, { x: number; y: number }>>({})
  const [sel, setSel] = useState<string | null>(null)
  const [connectFrom, setConnectFrom] = useState<string | null>(null)
  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null)
  const surface = useRef<HTMLDivElement>(null)

  // fill in any missing positions (new nodes / first render)
  useEffect(() => {
    setPos((p) => {
      const missing = nodes.some((n) => !p[n.id])
      if (!missing) return p
      const auto = autoLayout(nodes, edges)
      const next = { ...p }
      nodes.forEach((n) => { if (!next[n.id]) next[n.id] = auto[n.id] || { x: 30, y: 24 } })
      return next
    })
  }, [nodes, edges])

  // emit on any structural change
  const preserved = useRef(initial)
  useEffect(() => {
    onChange({ ...preserved.current, id: meta.id, on_event: meta.on_event, nodes, edges })
  }, [meta, nodes, edges, onChange])

  const nodePos = useCallback((id: string) => pos[id] || { x: 30, y: 24 }, [pos])

  // ── dragging ──────────────────────────────────────────────────────────────
  const onNodeMouseDown = (e: React.MouseEvent, id: string) => {
    const p = nodePos(id)
    dragRef.current = { id, dx: e.clientX - p.x, dy: e.clientY - p.y }
    setSel(id)
  }
  useEffect(() => {
    const move = (e: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      setPos((p) => ({ ...p, [d.id]: { x: Math.max(0, e.clientX - d.dx), y: Math.max(0, e.clientY - d.dy) } }))
    }
    const up = () => { dragRef.current = null }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  }, [])

  // ── structural edits ────────────────────────────────────────────────────
  const addNode = (kind: string) => {
    let i = 1; let id = `${kind}${i}`
    const ids = new Set(nodes.map((n) => n.id))
    while (ids.has(id)) { i++; id = `${kind}${i}` }
    setNodes((ns) => [...ns, { id, kind, ref: kind === 'task' || kind === 'gateway' || kind === 'state' || kind === 'timer' ? '' : null, config: {} }])
    setSel(id)
  }
  const deleteNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id))
    setEdges((es) => es.filter((e) => e.src !== id && e.dst !== id))
    if (sel === id) setSel(null)
  }
  const patchNode = (id: string, patch: Partial<Node>) => {
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, ...patch } : n)))
    if (patch.id && patch.id !== id) {
      setEdges((es) => es.map((e) => ({ ...e, src: e.src === id ? patch.id! : e.src, dst: e.dst === id ? patch.id! : e.dst })))
      setPos((p) => { const next = { ...p }; if (next[id]) { next[patch.id!] = next[id]; delete next[id] } return next })
      setSel(patch.id!)
    }
  }
  const clickNode = (id: string) => {
    if (connectFrom && connectFrom !== id) {
      const src = nodes.find((n) => n.id === connectFrom)
      const when = src?.kind === 'gateway' ? (window.prompt('Branch label for this gateway edge (e.g. "exceeds" or "else"):', 'else') || 'else') : null
      setEdges((es) => [...es, { src: connectFrom, dst: id, when }])
      setConnectFrom(null)
    } else {
      setSel(id)
    }
  }

  const selected = nodes.find((n) => n.id === sel) || null
  const layout = useMemo(() => {
    const maxX = Math.max(...nodes.map((n) => nodePos(n.id).x + NW), 400)
    const maxY = Math.max(...nodes.map((n) => nodePos(n.id).y + NH), 260)
    return { w: maxX + 30, h: maxY + 30 }
  }, [nodes, pos, nodePos])

  return (
    <div className="flowcanvas">
      <div className="fc-meta">
        <label className="fieldlabel">Flow id</label>
        <input className="cgqinput" value={meta.id} onChange={(e) => setMeta((m) => ({ ...m, id: e.target.value }))} />
        <label className="fieldlabel">on_event</label>
        <input className="cgqinput" value={meta.on_event} onChange={(e) => setMeta((m) => ({ ...m, on_event: e.target.value }))} />
      </div>

      <div className="fc-toolbar">
        {KINDS.map((k) => (
          <button key={k} className="btn sm ghost" onClick={() => addNode(k)}>
            <PlusIcon className="" /><span style={{ color: KIND_COLOR[k], fontWeight: 700 }}>{k}</span>
          </button>
        ))}
        <button className={'btn sm ' + (connectFrom ? 'primary' : 'ghost')}
          onClick={() => setConnectFrom(connectFrom ? null : (sel || nodes[0]?.id || null))}
          title="Pick a source, then click a target node to connect">
          <LinkIcon className="" />{connectFrom ? `Connecting from “${connectFrom}” — click a target` : 'Connect'}
        </button>
      </div>

      <div className="fc-surface" ref={surface}>
        <div style={{ position: 'relative', width: layout.w, height: layout.h, minWidth: '100%' }}>
          <svg className="fc-edges" width={layout.w} height={layout.h}>
            <defs>
              <marker id="fc-arrow" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
                <path d="M0,0 L7,3 L0,6 Z" fill="var(--muted)" />
              </marker>
            </defs>
            {edges.map((e, i) => {
              const a = nodePos(e.src), b = nodePos(e.dst)
              const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2
              const mx = (x1 + x2) / 2
              return (
                <g key={i}>
                  <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none"
                    stroke="var(--muted)" strokeWidth={1.5} markerEnd="url(#fc-arrow)" />
                  {e.when && (
                    <text x={mx} y={(y1 + y2) / 2 - 4} textAnchor="middle" className="fc-elabel">{e.when}</text>
                  )}
                </g>
              )
            })}
          </svg>

          {nodes.map((n) => {
            const p = nodePos(n.id)
            return (
              <div key={n.id} className={'fc-node' + (sel === n.id ? ' sel' : '') + (connectFrom === n.id ? ' from' : '')}
                style={{ left: p.x, top: p.y, width: NW, height: NH }}
                onMouseDown={(e) => { if ((e.target as HTMLElement).dataset.role !== 'port') onNodeMouseDown(e, n.id) }}
                onClick={() => clickNode(n.id)}>
                <div className="fc-nhead" style={{ background: KIND_COLOR[n.kind] }}>{n.kind}</div>
                <div className="fc-nbody">
                  <div className="fc-nid">{n.id}</div>
                  {n.ref ? <div className="fc-nref">→ {n.ref}</div> : null}
                </div>
                <span className="fc-port" data-role="port" title="Drag a connection from here"
                  onClick={(e) => { e.stopPropagation(); setConnectFrom(n.id) }} />
              </div>
            )
          })}
        </div>
      </div>

      {/* properties + edges */}
      <div className="fc-panels">
        <div className="fc-panel">
          <div className="fc-ptitle">{selected ? `Node · ${selected.id}` : 'No node selected'}</div>
          {selected && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Row label="id"><input className="cgqinput" value={selected.id}
                onChange={(e) => patchNode(selected.id, { id: e.target.value })} /></Row>
              <Row label="kind">
                <select className="cgselect" value={selected.kind} onChange={(e) => patchNode(selected.id, { kind: e.target.value })}>
                  {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </Row>
              <Row label="ref"><input className="cgqinput" placeholder={refHint(selected.kind)}
                value={selected.ref || ''} onChange={(e) => patchNode(selected.id, { ref: e.target.value })} /></Row>
              <Row label="config">
                <textarea className="codearea" rows={3} spellCheck={false}
                  value={JSON.stringify(selected.config || {}, null, 0)}
                  onChange={(e) => { try { patchNode(selected.id, { config: JSON.parse(e.target.value || '{}') }) } catch { /* ignore until valid */ } }} />
              </Row>
              <button className="btn sm ghost" onClick={() => deleteNode(selected.id)}>
                <Trash2Icon className="" />Delete node
              </button>
            </div>
          )}
        </div>

        <div className="fc-panel">
          <div className="fc-ptitle">Edges ({edges.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 150, overflow: 'auto' }}>
            {edges.length === 0 && <div className="empty" style={{ padding: 8 }}>No edges — use Connect.</div>}
            {edges.map((e, i) => (
              <div key={i} className="fc-erow">
                <code className="mono" style={{ fontSize: 11 }}>{e.src} → {e.dst}</code>
                <input className="cgqinput" style={{ width: 90, padding: '2px 6px', fontSize: 11 }} placeholder="when"
                  value={e.when || ''} onChange={(ev) => setEdges((es) => es.map((x, j) => j === i ? { ...x, when: ev.target.value || null } : x))} />
                <button className="iconbtn" onClick={() => setEdges((es) => es.filter((_, j) => j !== i))}><XIcon className="" /></button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function refHint(kind: string) {
  return kind === 'task' ? 'action name' : kind === 'gateway' ? 'rule name'
    : kind === 'state' ? 'target state' : kind === 'timer' ? 'e.g. 30s / 5m' : '(none)'
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '58px 1fr', alignItems: 'center', gap: 8 }}>
      <span className="fieldlabel">{label}</span>{children}
    </div>
  )
}
