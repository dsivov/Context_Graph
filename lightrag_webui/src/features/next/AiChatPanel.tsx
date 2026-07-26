import { useEffect, useRef, useState } from 'react'
import { SendIcon, SparklesIcon, PanelRightCloseIcon, Trash2Icon } from 'lucide-react'

/**
 * Studio assistant — a persistent right-side docked chat with history, scoped to
 * the whole Studio (not a single component), à la SOPilot's Config assistant.
 * It stays mounted while you edit any artifact, so switching components never
 * loses the conversation. History is kept in-memory (persists across component
 * switches) and mirrored to localStorage keyed by `historyKey` (survives
 * reloads). The full transcript is re-sent each turn (stateless backend).
 */

export type ChatMsg = { role: 'user' | 'assistant'; content: string; data?: any; error?: boolean }

const mem = new Map<string, ChatMsg[]>()
const lsKey = (k: string) => `cg-aichat:${k}`

function getHistory(key: string): ChatMsg[] {
  if (mem.has(key)) return mem.get(key)!
  let init: ChatMsg[] = []
  try { const raw = localStorage.getItem(lsKey(key)); if (raw) init = JSON.parse(raw) } catch { /* ignore */ }
  mem.set(key, init)
  return init
}
function saveHistory(key: string, msgs: ChatMsg[]) {
  mem.set(key, msgs)
  try {
    localStorage.setItem(lsKey(key), JSON.stringify(msgs.map((m) => ({ role: m.role, content: m.content, error: m.error }))))
  } catch { /* ignore quota */ }
}

export default function AiChatPanel({
  open, onCollapse, historyKey, authoringLabel, placeholder, send, onAccept, acceptLabel = 'Load this draft into review'
}: {
  open: boolean
  onCollapse: () => void
  historyKey: string
  authoringLabel?: string
  placeholder?: string
  send: (history: ChatMsg[], input: string) => Promise<{ reply: string; data?: any }>
  onAccept?: (data: any) => void
  acceptLabel?: string
}) {
  const [messages, setMessages] = useState<ChatMsg[]>(() => getHistory(historyKey))
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => { setMessages(getHistory(historyKey)) }, [historyKey])
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [messages, busy, open])

  const update = (msgs: ChatMsg[]) => { setMessages(msgs); saveHistory(historyKey, msgs) }

  const submit = async () => {
    const q = input.trim()
    if (!q || busy) return
    const next: ChatMsg[] = [...messages, { role: 'user', content: q }]
    update(next); setInput(''); setBusy(true)
    try {
      const r = await send(next, q)
      update([...next, { role: 'assistant', content: r.reply || '(no reply)', data: r.data }])
    } catch (e: any) {
      update([...next, { role: 'assistant', error: true, content: e?.response?.data?.detail || e?.message || String(e) }])
    } finally { setBusy(false) }
  }

  if (!open) return null

  return (
    <aside className="aichat-dock">
      <div className="aichat-head">
        <div style={{ minWidth: 0 }}>
          <div className="aichat-title"><SparklesIcon className="" />Studio assistant</div>
          <div className="msub">{authoringLabel ? `Authoring: ${authoringLabel}` : 'remembers this conversation'}</div>
        </div>
        <div style={{ display: 'flex', gap: 2 }}>
          {messages.length > 0 && (
            <button className="iconbtn" title="Clear conversation" onClick={() => update([])}><Trash2Icon className="" /></button>
          )}
          <button className="iconbtn" title="Collapse" onClick={onCollapse}><PanelRightCloseIcon className="" /></button>
        </div>
      </div>

      <div className="chatlog" ref={logRef} style={{ flex: 1, maxHeight: 'none' }}>
        {messages.length === 0 && (
          <div className="chatempty">Ask the assistant to draft a rule or ontology in plain English. It targets the component you have selected, and remembers this conversation across everything you edit.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={'msg ' + m.role + (m.error ? ' err' : '')}>
            {m.content}
            {m.role === 'assistant' && m.data && onAccept && !m.error && (
              <div className="msgtools">
                <button className="btn sm primary" onClick={() => onAccept(m.data)}>{acceptLabel}</button>
              </div>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant"><span className="cgspin" /> Thinking…</div>}
      </div>

      <div className="chatrow">
        <input className="cgqinput" placeholder={placeholder ?? 'Message the assistant…'} value={input}
          disabled={busy} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }} />
        <button className="btn sm primary" disabled={busy || !input.trim()} onClick={submit}><SendIcon className="" /></button>
      </div>
    </aside>
  )
}
