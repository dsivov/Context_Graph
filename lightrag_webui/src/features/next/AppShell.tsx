import { useState, useMemo } from 'react'
import {
  LayoutDashboardIcon, ScaleIcon, FilesIcon, NetworkIcon, SearchIcon,
  GavelIcon, BoxesIcon, SparklesIcon, Code2Icon, RocketIcon,
  BellIcon, SunIcon, MoonIcon, PanelLeftIcon
} from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import { setUiMode } from '@/lib/uiMode'
import WorkspaceSelector from '@/components/WorkspaceSelector'
import './next.css'

import Dashboard from '@/features/next/pages/Dashboard'
import Decisions from '@/features/next/pages/Decisions'
import GraphQualityNext from '@/features/next/pages/GraphQualityNext'
import OntologyNext from '@/features/next/pages/OntologyNext'
import RulesNext from '@/features/next/pages/RulesNext'
import DocumentManager from '@/features/DocumentManager'
import GraphViewer from '@/features/GraphViewer'
import RetrievalTesting from '@/features/RetrievalTesting'
import GetStarted from '@/features/GetStarted'
import ApiSite from '@/features/ApiSite'

type ViewId =
  | 'dashboard' | 'decisions' | 'documents' | 'graph' | 'retrieval'
  | 'rules' | 'ontology' | 'quality' | 'getstarted' | 'api'

type NavItem = {
  id: ViewId
  label: string
  icon: React.ComponentType<{ className?: string }>
  group: string
  flush?: boolean            // full-bleed feature (no padding, no page scroll)
  badge?: { text: string; warn?: boolean }
}

const NAV: NavItem[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboardIcon, group: 'Overview' },
  { id: 'decisions', label: 'Decisions', icon: ScaleIcon, group: 'Overview' },
  { id: 'documents', label: 'Documents', icon: FilesIcon, group: 'Knowledge', flush: true },
  { id: 'graph', label: 'Knowledge Graph', icon: NetworkIcon, group: 'Knowledge', flush: true },
  { id: 'retrieval', label: 'Retrieval', icon: SearchIcon, group: 'Knowledge', flush: true },
  { id: 'rules', label: 'Rules', icon: GavelIcon, group: 'Governance' },
  { id: 'ontology', label: 'Ontology', icon: BoxesIcon, group: 'Governance' },
  { id: 'quality', label: 'Graph Quality', icon: SparklesIcon, group: 'Governance' },
  { id: 'getstarted', label: 'Get Started', icon: RocketIcon, group: 'Setup' },
  { id: 'api', label: 'API', icon: Code2Icon, group: 'Setup' }
]

function ThemeToggle() {
  const theme = useSettingsStore.use.theme()
  const setTheme = useSettingsStore.use.setTheme()
  const isDark =
    theme === 'dark' ||
    (theme === 'system' && typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)
  return (
    <button
      className="iconbtn"
      title={isDark ? 'Switch to light' : 'Switch to dark'}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
    >
      {isDark ? <SunIcon className="" /> : <MoonIcon className="" />}
    </button>
  )
}

// Embedded classic features gate their data-loading on the settings-store
// currentTab (e.g. DocumentManager only fetches when currentTab === 'documents').
// Mirror the shell's view into currentTab so those features load correctly.
const TAB_OF: Partial<Record<ViewId, string>> = {
  documents: 'documents', graph: 'knowledge-graph', retrieval: 'retrieval',
  rules: 'rules', ontology: 'ontology', getstarted: 'get-started', api: 'api'
}

export default function AppShell() {
  const [view, setView] = useState<ViewId>('dashboard')
  const [search, setSearch] = useState('')
  const workspace = useSettingsStore.use.workspace()
  const setCurrentTab = useSettingsStore.use.setCurrentTab()

  const go = (v: ViewId) => {
    setView(v)
    const tab = TAB_OF[v]
    if (tab) setCurrentTab(tab as never)
  }

  const groups = useMemo(() => {
    const order = ['Overview', 'Knowledge', 'Governance', 'Setup']
    return order.map((g) => ({ group: g, items: NAV.filter((n) => n.group === g) }))
  }, [])

  const active = NAV.find((n) => n.id === view)!

  const content = () => {
    switch (view) {
      case 'dashboard': return <Dashboard onNavigate={go} />
      case 'decisions': return <Decisions />
      case 'documents': return <DocumentManager />
      case 'graph': return <GraphViewer />
      case 'retrieval': return <RetrievalTesting />
      case 'rules': return <RulesNext />
      case 'ontology': return <OntologyNext />
      case 'quality': return <GraphQualityNext />
      case 'getstarted': return <GetStarted />
      case 'api': return <ApiSite />
      default: return null
    }
  }

  return (
    <div className="cgnext">
      <div className="app">
        {/* sidebar */}
        <aside className="side">
          <div className="brand">
            <svg className="mark" viewBox="0 0 32 32" fill="none" aria-hidden="true">
              <circle cx="8" cy="9" r="3.4" fill="var(--accent)" />
              <circle cx="24" cy="7" r="3" fill="var(--comm)" />
              <circle cx="23" cy="24" r="3.4" fill="var(--good)" />
              <circle cx="9" cy="23" r="2.6" fill="var(--warn)" />
              <path d="M8 9L24 7M24 7L23 24M23 24L9 23M9 23L8 9M8 9L23 24" stroke="var(--line2)" strokeWidth="1.4" />
            </svg>
            <div className="name">Context Graph<small>Governed knowledge</small></div>
          </div>
          <nav className="nav">
            {groups.map(({ group, items }) => (
              <div key={group}>
                <div className="grp">{group}</div>
                {items.map((n) => {
                  const Icon = n.icon
                  return (
                    <button
                      key={n.id}
                      className={'navitem' + (view === n.id ? ' active' : '')}
                      onClick={() => go(n.id)}
                    >
                      <Icon className="" />
                      {n.label}
                      {n.badge && (
                        <span className={'badge' + (n.badge.warn ? ' warn' : '')}>{n.badge.text}</span>
                      )}
                    </button>
                  )
                })}
              </div>
            ))}
          </nav>
          <div className="foot">
            <div className="avatar">CG</div>
            <div className="who" style={{ flex: 1 }}>
              Context Graph<small>Workspace · {workspace}</small>
            </div>
            <button className="iconbtn" title="Back to classic UI" onClick={() => setUiMode('classic')}>
              <PanelLeftIcon className="" />
            </button>
          </div>
        </aside>

        {/* main */}
        <div className="cgmain">
          <div className="topbar">
            <div className="wswrap"><WorkspaceSelector /></div>
            <form
              className="search"
              onSubmit={(e) => { e.preventDefault(); if (search.trim()) go('retrieval') }}
            >
              <SearchIcon className="" />
              <input
                className="searchinput"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search — press Enter to open Retrieval…"
              />
            </form>
            <div className="top-actions">
              <button className="iconbtn" title="Notifications"><BellIcon className="" /></button>
              <ThemeToggle />
            </div>
          </div>

          <div className={'content' + (active.flush ? ' flush' : '')}>
            {content()}
          </div>
        </div>
      </div>
    </div>
  )
}
