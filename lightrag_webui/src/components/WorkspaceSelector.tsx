import { useEffect, useState } from 'react'
import { useSettingsStore } from '@/stores/settings'
import { listWorkspaces } from '@/api/lightrag'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/Select'
import { BuildingIcon } from 'lucide-react'

export default function WorkspaceSelector() {
  const workspace = useSettingsStore.use.workspace()
  const setWorkspace = useSettingsStore.use.setWorkspace()
  const [workspaces, setWorkspaces] = useState<string[]>([])

  useEffect(() => {
    listWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]))
  }, [])

  const handleChange = (value: string) => {
    if (value === '__custom__') return
    setWorkspace(value)
    // Reload the page to fetch data for the new workspace
    window.location.reload()
  }

  return (
    <div className="flex items-center gap-1">
      <BuildingIcon className="size-3.5 text-muted-foreground" />
      <Select value={workspace} onValueChange={handleChange}>
        <SelectTrigger className="h-7 w-[140px] text-xs border-none bg-transparent focus:ring-0 focus:ring-offset-0">
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
        </SelectContent>
      </Select>
    </div>
  )
}
