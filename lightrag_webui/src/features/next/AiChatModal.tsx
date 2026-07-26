import { useEffect, useRef, useState } from 'react'
import { SendIcon } from 'lucide-react'
import Modal from '@/features/next/Modal'

/**
 * Reusable AI chat, modal, with persistent conversation history — the single
 * pattern every AI interaction in the Studio uses (à la SOPilot's Config
 * assistant). History is keyed by `historyKey`, kept in-memory across open/close
 * and mirrored to localStorage (text only) so it survives reloads. The full
 * conversation is re-sent on each turn via `send` (the backend is stateless).
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
  // Persist text only — proposed payloads (`data`) stay in-session.
  try {
    localStorage.setItem(lsKey(key), JSON.stringify(
      msgs.map((m) => ({ role: m.role, content: m.content, error: m.error }))))
  } catch { /* ignore quota */ }
}

export default function AiChatModal({
  open, onClose, title, subtitle, historyKey, placeholder, intro,
  send, onAccept, acceptLabel = 'Use this draft'
}: {
  open: boolean
  onClose: () => void
  title: React.ReactNode
  subtitle?: React.ReactNode
  historyKey: string
  placeholder?: string
  intro?: React.ReactNode
  send: (history: ChatMsg[], input: string) => Promise<{ reply: string; data?: any }>
  onAccept?: (data: any) => void
  acceptLabel?: string
}) {
  const [messages, setMessages] = useState<ChatMsg[]>(() => getHistory(historyKey))
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => { if (open) setMessages(getHistory(historyKey)) }, [open, historyKey])
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [messages, busy])

  const update = (msgs: ChatMsg[]) => { setMessages(msgs); saveHistory(historyKey, msgs) }

  const submit = async () => {
    const q = input.trim()
    if (!q || busy) return
    const next: ChatMsg[] = [...messages, { role: 'user', content: q }]
    update(next)
    setInput('')
    setBusy(true)
    try {
      const r = await send(next, q)
      update([...next, { role: 'assistant', content: r.reply || '(no reply)', data: r.data }])
    } catch (e: any) {
      update([...next, {
        role: 'assistant', error: true,
        content: e?.response?.data?.detail || e?.message || String(e)
      }])
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return (
    <Modal title={title} subtitle={subtitle ?? 'Remembers this conversation'} width={620} onClose={onClose}>
      <div className="chatlog" ref={logRef}>
        {messages.length === 0 && (
          <div className="chatempty">{intro ?? 'Describe the change you want in plain English, then refine it turn by turn.'}</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={'msg ' + m.role + (m.error ? ' err' : '')}>
            {m.content}
            {m.role === 'assistant' && m.data && onAccept && !m.error && (
              <div className="msgtools">
                <button className="btn sm primary" onClick={() => { onAccept(m.data); onClose() }}>{acceptLabel}</button>
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
        <button className="btn sm primary" disabled={busy || !input.trim()} onClick={submit}>
          <SendIcon className="" />Send
        </button>
        {messages.length > 0 && (
          <button className="btn sm ghost" disabled={busy} onClick={() => update([])}>Clear</button>
        )}
      </div>
    </Modal>
  )
}
