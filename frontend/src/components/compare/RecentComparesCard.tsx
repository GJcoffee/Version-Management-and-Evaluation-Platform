import { Link } from 'react-router-dom'
import { GitCompareArrows } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { CompareGroup, CompareSide } from '@/api/compareTypes'
import { useCompares } from '@/api/queries/compare'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { LoadingBlock } from '@/components/ui/Spinner'
import { MODE_TAG } from './compareUtils'

const side = (s: CompareSide) => `${s.agent_name ?? '?'} ${s.version_seq !== null && s.version_seq !== undefined ? `v${s.version_seq}` : s.version_id.slice(-6)}`

function CompareItem({ g }: { g: CompareGroup }) {
  return (
    <li>
      <Link to={`/compare/${g.id}`} className="flex items-center gap-3 px-1 py-2 text-sm hover:bg-accent/60" data-testid="recent-compare">
        <Badge variant={g.mode === 'live' ? 'info' : g.mode === 'replay' ? 'warning' : 'secondary'}>{MODE_TAG[g.mode]}</Badge>
        <span className="min-w-0 flex-1 truncate font-medium">{g.title?.trim() || '会话对比'}</span>
        <span className="hidden shrink-0 font-mono text-xs text-muted-foreground sm:inline">
          {side(g.left)} ↔ {side(g.right)}
        </span>
        <RelativeTime value={g.created_at} className="w-20 shrink-0 text-right text-xs text-muted-foreground" />
      </Link>
    </li>
  )
}

/** 首页的“最近对比”卡片（GET /compare） */
export function RecentComparesCard() {
  const { data, isLoading, error } = useCompares(6)
  const items = data?.items ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitCompareArrows className="h-4 w-4 text-muted-foreground" />
          最近对比
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingBlock className="py-6" />
        ) : error ? (
          <p className="py-4 text-sm text-danger">加载失败：{error instanceof ApiError ? error.message : String(error)}</p>
        ) : items.length === 0 ? (
          <EmptyState
            icon={GitCompareArrows}
            title="还没有对比"
            description="在 Agent 的版本列表勾选两个版本点击“双栏对话”，或在会话页选择“在其他版本上重放”。"
            className="py-6"
          />
        ) : (
          <ul className="divide-y divide-border" data-testid="recent-compares">
            {items.map((g) => (
              <CompareItem key={g.id} g={g} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
