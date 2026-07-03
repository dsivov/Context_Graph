import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Textarea from '@/components/ui/Textarea'
import Badge from '@/components/ui/Badge'
import Checkbox from '@/components/ui/Checkbox'
import Separator from '@/components/ui/Separator'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { useSettingsStore } from '@/stores/settings'
import {
  onboardChat,
  onboardApply,
  type OnboardChatMessage,
  type OnboardProposal,
  type OnboardApplyResponse
} from '@/api/lightrag'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)

export default function GetStarted() {
  const workspace = useSettingsStore.use.workspace()
  const [messages, setMessages] = useState<OnboardChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [repoPresent, setRepoPresent] = useState(false)
  const [proposal, setProposal] = useState<OnboardProposal | null>(null)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<OnboardApplyResponse | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    })
  }

  // Run one interview turn. `history` is the transcript to send.
  const turn = useCallback(
    async (history: OnboardChatMessage[]) => {
      setBusy(true)
      try {
        const res = await onboardChat(history, repoPresent)
        setMessages([...history, { role: 'assistant', content: res.assistant }])
        if (res.ready && res.proposal) setProposal(res.proposal)
        scrollToBottom()
      } catch (e) {
        toast.error(`Interview failed: ${errMsg(e)}`)
      } finally {
        setBusy(false)
      }
    },
    [repoPresent]
  )

  const restart = useCallback(() => {
    setMessages([])
    setProposal(null)
    setResult(null)
    setInput('')
    turn([]) // empty transcript → the interviewer asks its first question
  }, [turn])

  // Kick off (and reset) the interview when the workspace changes.
  useEffect(() => {
    restart()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace])

  const send = useCallback(() => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    const next = [...messages, { role: 'user', content: text }]
    setMessages(next)
    scrollToBottom()
    turn(next)
  }, [input, busy, messages, turn])

  const patch = (p: Partial<OnboardProposal>) =>
    setProposal((prev) => (prev ? { ...prev, ...p } : prev))

  const install = useCallback(async () => {
    if (!proposal) return
    setApplying(true)
    try {
      const res = await onboardApply(proposal)
      setResult(res)
      toast.success('Workspace onboarded — bootstrap ready for the agent.')
    } catch (e) {
      toast.error(`Install failed: ${errMsg(e)}`)
    } finally {
      setApplying(false)
    }
  }, [proposal])

  const copy = (text: string, label: string) => {
    navigator.clipboard?.writeText(text).then(
      () => toast.success(`${label} copied`),
      () => toast.error('Copy failed')
    )
  }

  return (
    <div className="grid h-full grid-cols-1 gap-4 overflow-auto p-4 lg:grid-cols-2">
      {/* ── Interview ─────────────────────────────────────────────── */}
      <Card className="flex min-h-0 flex-col">
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <div>
            <CardTitle>Get Started · {workspace || 'default'}</CardTitle>
            <p className="text-muted-foreground mt-1 text-sm">
              A short interview to set up this workspace — its ontology, methodology rules, and a first change request.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={restart} disabled={busy}>
            Restart
          </Button>
        </CardHeader>
        <CardContent className="flex min-h-0 flex-1 flex-col gap-3">
          <div ref={scrollRef} className="min-h-[240px] flex-1 space-y-3 overflow-auto rounded-md border p-3">
            {messages.length === 0 && busy && (
              <p className="text-muted-foreground text-sm">Starting the interview…</p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                <div
                  className={
                    'max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ' +
                    (m.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-muted')
                  }
                >
                  {m.content}
                </div>
              </div>
            ))}
            {busy && messages.length > 0 && (
              <div className="text-muted-foreground text-sm">thinking…</div>
            )}
          </div>

          <div className="flex items-center gap-2">
            <label className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <Checkbox
                checked={repoPresent}
                onCheckedChange={(v) => setRepoPresent(v === true)}
              />
              existing repo
            </label>
            <Input
              value={input}
              placeholder="Type your answer…"
              disabled={busy}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
            />
            <Button onClick={send} disabled={busy || !input.trim()}>
              Send
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ── Proposal / result ─────────────────────────────────────── */}
      <Card className="flex min-h-0 flex-col">
        <CardHeader>
          <CardTitle>{result ? 'Onboarded ✓' : proposal ? 'Review & install' : 'Proposal'}</CardTitle>
          <p className="text-muted-foreground mt-1 text-sm">
            {result
              ? 'Config installed. Hand these entry points to the dev agent.'
              : proposal
                ? 'Edit anything below, then install. Nothing is written until you click Install.'
                : 'The proposal appears here once the interview has gathered enough.'}
          </p>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 space-y-4 overflow-auto">
          {!proposal && !result && (
            <p className="text-muted-foreground text-sm">
              Answer the questions on the left. When the interviewer has enough, it drafts a
              workspace setup here for your review.
            </p>
          )}

          {proposal && !result && (
            <>
              <Field label="Onboarding brief">
                <Textarea
                  value={proposal.brief}
                  rows={3}
                  onChange={(e) => patch({ brief: e.target.value })}
                />
              </Field>
              <Field label="Ontology description (authors the object types)">
                <Textarea
                  value={proposal.description}
                  rows={4}
                  onChange={(e) => patch({ description: e.target.value })}
                />
              </Field>
              <Field label="Methodology policy (authors the rules — optional)">
                <Textarea
                  value={proposal.policy || ''}
                  rows={3}
                  onChange={(e) => patch({ policy: e.target.value })}
                />
              </Field>
              <Field label="Roles (comma-separated — empty for single-agent)">
                <Input
                  value={proposal.roles.join(', ')}
                  onChange={(e) =>
                    patch({ roles: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })
                  }
                />
              </Field>

              <div className="flex flex-wrap gap-1.5">
                <span className="text-muted-foreground mr-1 text-xs">Object types:</span>
                {proposal.object_types_preview.map((t) => (
                  <Badge key={t} variant="secondary">
                    {t}
                  </Badge>
                ))}
              </div>
              {proposal.rules_preview.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  <span className="text-muted-foreground mr-1 text-xs">Guardrails:</span>
                  {proposal.rules_preview.map((r) => (
                    <Badge key={r} variant="outline">
                      {r}
                    </Badge>
                  ))}
                </div>
              )}

              <Separator />
              <div className="space-y-2">
                <div className="text-sm font-medium">First change request (the agent's starting point)</div>
                <Input
                  value={proposal.first_cr?.title || ''}
                  placeholder="CR title"
                  onChange={(e) =>
                    patch({
                      first_cr: {
                        id: proposal.first_cr?.id || 'cr-initial',
                        title: e.target.value,
                        description: proposal.first_cr?.description || ''
                      }
                    })
                  }
                />
                <Textarea
                  value={proposal.first_cr?.description || ''}
                  rows={2}
                  placeholder="What the first piece of work is"
                  onChange={(e) =>
                    patch({
                      first_cr: {
                        id: proposal.first_cr?.id || 'cr-initial',
                        title: proposal.first_cr?.title || '',
                        description: e.target.value
                      }
                    })
                  }
                />
              </div>

              <Button className="w-full" onClick={install} disabled={applying}>
                {applying ? 'Installing…' : 'Review & Install'}
              </Button>
            </>
          )}

          {result && (
            <>
              <div className="space-y-1 text-sm">
                <div>
                  <b>Ontology:</b> {result.ontology.object_types.join(', ') || '—'}{' '}
                  {result.ontology.saved ? '✓' : '(not saved)'}
                </div>
                <div>
                  <b>Rules:</b> {result.rules?.saved ? 'installed ✓' : '—'}
                </div>
                <div>
                  <b>Roles:</b> {result.roles_seeded.join(', ') || 'single-agent'}
                </div>
                <div>
                  <b>First CR:</b> {result.first_cr ? `${result.first_cr.title} (${result.first_cr.id})` : '—'}
                </div>
                <div>
                  <b>Brief ingested:</b> {result.brief_id ? 'yes' : 'no'}
                </div>
              </div>

              <Separator />
              <BlockWithCopy
                title="1 · .mcp.json (wire the agent)"
                text={JSON.stringify(result.bootstrap.mcp_config, null, 2)}
                onCopy={copy}
              />
              <BlockWithCopy
                title="2 · Backfill existing code (optional)"
                text={result.bootstrap.backfill.cmd}
                onCopy={copy}
              />
              <div className="text-sm">
                <b>3 · Playbook:</b>{' '}
                <a
                  className="text-primary underline"
                  href={result.bootstrap.playbook_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {result.bootstrap.playbook_url}
                </a>
              </div>
              <ol className="text-muted-foreground list-decimal space-y-1 pl-5 text-sm">
                {result.bootstrap.next_steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              <Button variant="outline" className="w-full" onClick={restart}>
                Onboard another
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-muted-foreground text-xs font-medium">{label}</div>
      {children}
    </div>
  )
}

function BlockWithCopy({
  title,
  text,
  onCopy
}: {
  title: string
  text: string
  onCopy: (t: string, label: string) => void
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">{title}</div>
        <Button variant="ghost" size="sm" onClick={() => onCopy(text, title)}>
          Copy
        </Button>
      </div>
      <pre className="bg-muted overflow-auto rounded-md p-2 text-xs">{text}</pre>
    </div>
  )
}
