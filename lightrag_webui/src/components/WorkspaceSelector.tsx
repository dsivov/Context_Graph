import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { useSettingsStore } from '@/stores/settings'
import { listWorkspaces } from '@/api/lightrag'
import Input from '@/components/ui/Input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/Select'
import { BuildingIcon, CheckIcon, XIcon } from 'lucide-react'

// Workspace names: letters, digits, and underscore only (matches the server).
const WS_RE = /^[A-Za-z0-9_]+$/

export default function WorkspaceSelector() {
  const workspace = useSettingsStore.use.workspace()
  const setWorkspace = useSettingsStore.use.setWorkspace()
  const [workspaces, setWorkspaces] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')

  useEffect(() => {
    listWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]))
  }, [])

  const switchTo = (value: string) => {
    setWorkspace(value)
    // Reload so every tab re-fetches for the new workspace.
    window.location.reload()
  }

  const handleChange = (value: string) => {
    if (value === '__new__') {
      setName('')
      setCreating(true)
      return
    }
    switchTo(value)
  }

  const create = () => {
    const ws = name.trim()
    if (!WS_RE.test(ws)) {
      toast.error('Workspace name: letters, digits, and _ only')
      return
    }
    // A CG workspace is created implicitly on first write — so we just select the
    // name and drop the user on Get Started to onboard (which populates it).
    setWorkspace(ws)
    useSettingsStore.getState().setCurrentTab('get-started')
    window.location.reload()
  }

  if (creating) {
    return (
      <div className="flex items-center gap-1">
        <BuildingIcon className="text-muted-foreground size-3.5" />
        <Input
          autoFocus
          value={name}
          placeholder="new_workspace"
          className="h-7 w-[140px] text-xs"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              create()
            } else if (e.key === 'Escape') {
              setCreating(false)
            }
          }}
        />
        <button
          type="button"
          title="Create workspace"
          className="text-muted-foreground hover:text-foreground"
          onClick={create}
        >
          <CheckIcon className="size-3.5" />
        </button>
        <button
          type="button"
          title="Cancel"
          className="text-muted-foreground hover:text-foreground"
          onClick={() => setCreating(false)}
        >
          <XIcon className="size-3.5" />
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <BuildingIcon className="text-muted-foreground size-3.5" />
      <Select value={workspace} onValueChange={handleChange}>
        <SelectTrigger className="h-7 w-[140px] border-none bg-transparent text-xs focus:ring-0 focus:ring-offset-0">
          <SelectValue placeholder="Workspace" />
        </SelectTrigger>
        <SelectContent>
          {workspaces.map((ws) => (
            <SelectItem key={ws} value={ws} className="text-xs">
              {ws}
            </SelectItem>
          ))}
          {workspace && !workspaces.includes(workspace) && (
            <SelectItem value={workspace} className="text-xs">
              {workspace}
            </SelectItem>
          )}
          <SelectSeparator />
          <SelectItem value="__new__" className="text-xs">
            ＋ New workspace…
          </SelectItem>
        </SelectContent>
      </Select>
    </div>
  )
}
