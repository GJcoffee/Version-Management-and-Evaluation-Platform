import { NavLink } from 'react-router-dom'
import { Bot, FlaskConical, Home, Library, Settings, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useHealth } from '@/api/queries/health'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '首页', icon: Home, end: true },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/assets', label: '资产库', icon: Library },
  { to: '/evals', label: '评测', icon: FlaskConical },
  { to: '/settings', label: '设置', icon: Settings },
]

export function Sidebar() {
  const { data, isError } = useHealth()
  const state = isError ? 'down' : data?.ok ? 'ok' : data ? 'warn' : 'unknown'
  const dot = { ok: 'bg-success', warn: 'bg-warning', down: 'bg-danger', unknown: 'bg-muted-foreground/40' }[state]
  const text = { ok: '服务正常', warn: '服务异常', down: '无法连接', unknown: '检测中…' }[state]

  return (
    <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      <div className="flex h-12 items-center gap-2 border-b border-border px-4">
        <div className="flex h-6 w-6 items-center justify-center rounded bg-primary font-mono text-xs font-bold text-primary-foreground">
          E
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-foreground">EvalP</div>
          <div className="text-[10px] text-muted-foreground">版本管理与评测</div>
        </div>
      </div>
      <nav className="flex-1 space-y-0.5 p-2" aria-label="主导航">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors',
                isActive
                  ? 'bg-accent font-medium text-foreground'
                  : 'text-sidebar-foreground hover:bg-accent/60 hover:text-foreground',
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="flex items-center gap-2 border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
        <span className={cn('h-2 w-2 rounded-full', dot)} />
        <span>{text}</span>
        {data?.version && <span className="ml-auto font-mono">v{data.version}</span>}
      </div>
    </aside>
  )
}
