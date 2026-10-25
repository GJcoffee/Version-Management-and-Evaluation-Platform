import { Link } from 'react-router-dom'
import type { Session } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { formatCost, formatTokens } from '@/lib/format'
import { STATUS_LABEL, sessionTitle, sessionTokens, versionLabel } from './sessionUtils'

export function SessionStatusBadge({ status }: { status: Session['status'] }) {
  if (status === 'idle') return null
  return <Badge variant={status === 'running' ? 'info' : 'danger'}>{STATUS_LABEL[status]}</Badge>
}

/** 紧凑的会话列表（首页卡片）：标题、agent 与版本、更新时间 */
export function RecentSessionList({ sessions }: { sessions: Session[] }) {
  return (
    <ul className="divide-y divide-border" data-testid="recent-sessions">
      {sessions.map((s) => (
        <li key={s.id}>
          <Link to={`/sessions/${s.id}`} className="flex items-center gap-3 px-1 py-2 text-sm hover:bg-accent/60">
            <span className="min-w-0 flex-1 truncate font-medium">{sessionTitle(s)}</span>
            <SessionStatusBadge status={s.status} />
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {s.agent_name ?? s.agent_id} {versionLabel(s)}
            </span>
            <span className="hidden shrink-0 font-mono text-xs text-muted-foreground sm:inline">
              {s.stats.turns ?? 0} 轮 · {formatTokens(sessionTokens(s))} · {formatCost(s.stats.cost ?? 0)}
            </span>
            <RelativeTime value={s.updated_at} className="w-20 shrink-0 text-right text-xs text-muted-foreground" />
          </Link>
        </li>
      ))}
    </ul>
  )
}
