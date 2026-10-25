import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessagesSquare, Plus, Search } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useAgent } from '@/api/queries/agents'
import { useInfiniteSessions } from '@/api/queries/sessions'
import { useVersions } from '@/api/queries/versions'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Input } from '@/components/ui/Input'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { formatCost, formatTokens } from '@/lib/format'
import { useDebouncedValue } from '@/lib/useDebouncedValue'
import { NewSessionDialog } from '../session/NewSessionDialog'
import { SessionStatusBadge } from '../session/SessionList'
import { MODE_LABEL, sessionTitle, sessionTokens, versionLabel } from '../session/sessionUtils'

/** 会话标签页：该 Agent 的会话列表（可搜索、按版本筛选）和“新建对话” */
export function SessionsTab({ agentId }: { agentId: string }) {
  const navigate = useNavigate()
  const agent = useAgent(agentId)
  const versions = useVersions(agentId, 100)
  const versionList = useMemo(() => versions.data?.pages.flatMap((p) => p.items) ?? [], [versions.data])
  const [q, setQ] = useState('')
  const [versionId, setVersionId] = useState('')
  const [creating, setCreating] = useState(false)
  const dq = useDebouncedValue(q.trim(), 300)

  const sessions = useInfiniteSessions({ agent_id: agentId, version_id: versionId || undefined, q: dq || undefined })
  const items = sessions.data?.pages.flatMap((p) => p.items) ?? []
  const filtering = !!dq || !!versionId

  return (
    <div className="space-y-3" data-testid="sessions-tab">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-8"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索会话标题…"
            aria-label="搜索会话"
          />
        </div>
        <Select
          className="w-44"
          value={versionId}
          onChange={(e) => setVersionId(e.target.value)}
          aria-label="按版本筛选"
          data-testid="version-filter"
        >
          <option value="">全部版本</option>
          {versionList.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.seq}
              {v.tags.length > 0 ? `（${v.tags.join(', ')}）` : ''}
            </option>
          ))}
        </Select>
        <Button size="sm" className="ml-auto" onClick={() => setCreating(true)} disabled={!agent.data} data-testid="new-session">
          <Plus className="h-3.5 w-3.5" />
          新建对话
        </Button>
      </div>

      {sessions.isLoading ? (
        <LoadingBlock />
      ) : sessions.error ? (
        <p className="py-6 text-sm text-danger">
          加载失败：{sessions.error instanceof ApiError ? sessions.error.message : String(sessions.error)}
        </p>
      ) : items.length === 0 ? (
        <EmptyState
          icon={MessagesSquare}
          title={filtering ? '没有符合条件的会话' : '还没有会话'}
          description={filtering ? '换个关键字或版本试试。' : '点击“新建对话”，或在版本列表中点击某个版本的“对话”按钮。'}
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>标题</TableHead>
              <TableHead>版本</TableHead>
              <TableHead>模式</TableHead>
              <TableHead className="text-right">轮数</TableHead>
              <TableHead className="text-right">Tokens</TableHead>
              <TableHead className="text-right">成本</TableHead>
              <TableHead>更新时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((s) => (
              <TableRow
                key={s.id}
                className="cursor-pointer"
                tabIndex={0}
                data-testid="session-row"
                onClick={() => navigate(`/sessions/${s.id}`)}
                onKeyDown={(e) => e.key === 'Enter' && navigate(`/sessions/${s.id}`)}
              >
                <TableCell className="max-w-72">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{sessionTitle(s)}</span>
                    <SessionStatusBadge status={s.status} />
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs">{versionLabel(s)}</TableCell>
                <TableCell>
                  <Badge variant="outline">{MODE_LABEL[s.mode]}</Badge>
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{s.stats.turns ?? 0}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{formatTokens(sessionTokens(s))}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{formatCost(s.stats.cost ?? 0)}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <RelativeTime value={s.updated_at} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {sessions.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => void sessions.fetchNextPage()} loading={sessions.isFetchingNextPage}>
            加载更多
          </Button>
        </div>
      )}

      {creating && agent.data && <NewSessionDialog agent={agent.data} versions={versionList} onClose={() => setCreating(false)} />}
    </div>
  )
}
