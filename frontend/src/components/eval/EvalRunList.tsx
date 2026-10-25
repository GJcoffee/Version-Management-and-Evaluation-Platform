import { useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { FlaskConical, GitCompare } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { EvalRunOut } from '@/api/evalTypes'
import { useEvals } from '@/api/queries/evals'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { formatCost } from '@/lib/format'
import { EvalProgressBar, EvalStatusBadge } from './EvalStatus'
import {
  EVAL_STATUS_FILTERS,
  compareHref,
  isRunActive,
  orderForCompare,
  passRateText,
  runDatasetLabel,
  runTitle,
  runVersionLabel,
} from './evalUtils'

/**
 * 评测列表（EvalsPage 与 Agent 的评测标签页共用）：状态筛选、勾选两个 run 对比、运行中显示进度条。
 * 有进行中的评测时每 3 秒轮询一次。
 */
export function EvalRunList({ agentId, actions, emptyAction }: { agentId?: string; actions?: ReactNode; emptyAction?: ReactNode }) {
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const list = useEvals({ agent_id: agentId, status: status || undefined }, { autoRefresh: 3000 })
  const runs = list.data?.pages.flatMap((p) => p.items) ?? []

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= 2 ? [cur[1], id] : [...cur, id]))

  const compare = () => {
    const [a, b] = selected.map((id) => runs.find((r) => r.id === id)).filter((r): r is EvalRunOut => !!r)
    if (!a || !b) return
    const [left, right] = orderForCompare(a, b)
    navigate(compareHref(left.id, right.id))
  }

  const showAgent = !agentId

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Select aria-label="状态筛选" className="w-36" value={status} onChange={(e) => setStatus(e.target.value)}>
          {EVAL_STATUS_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </Select>
        <span className="text-xs text-muted-foreground">勾选两个评测可对比（已选 {selected.length}/2）</span>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={selected.length !== 2} onClick={compare} data-testid="compare-selected">
            <GitCompare className="h-3.5 w-3.5" />
            对比所选
          </Button>
          {actions}
        </div>
      </div>

      {list.isLoading ? (
        <LoadingBlock />
      ) : list.error ? (
        <p className="py-6 text-sm text-danger">加载失败：{list.error instanceof ApiError ? list.error.message : String(list.error)}</p>
      ) : runs.length === 0 ? (
        <EmptyState
          icon={FlaskConical}
          title={status ? '没有符合条件的评测' : '还没有评测'}
          description="选择版本和数据集发起一次评测，结果会显示在这里。"
          action={status ? undefined : emptyAction}
        />
      ) : (
        <Table data-testid="eval-run-table">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-8" />
              <TableHead>名称</TableHead>
              {showAgent && <TableHead>Agent</TableHead>}
              <TableHead>版本</TableHead>
              <TableHead>数据集</TableHead>
              <TableHead className="min-w-44">状态</TableHead>
              <TableHead className="text-right">通过率</TableHead>
              <TableHead className="text-right">成本</TableHead>
              <TableHead>时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((r) => (
              <TableRow key={r.id} data-testid={`eval-row-${r.id}`}>
                <TableCell>
                  <input
                    type="checkbox"
                    aria-label={`选择 ${runTitle(r)}`}
                    className="h-4 w-4 accent-primary"
                    checked={selected.includes(r.id)}
                    onChange={() => toggle(r.id)}
                  />
                </TableCell>
                <TableCell className="max-w-64">
                  <Link to={`/evals/${r.id}`} className="block truncate font-medium text-primary hover:underline" title={runTitle(r)}>
                    {runTitle(r)}
                  </Link>
                </TableCell>
                {showAgent && (
                  <TableCell>
                    <Link to={`/agents/${r.agent_id}`} className="text-xs hover:underline">
                      {r.agent_name ?? r.agent_id}
                    </Link>
                  </TableCell>
                )}
                <TableCell className="font-mono text-xs">{runVersionLabel(r)}</TableCell>
                <TableCell className="font-mono text-xs">{runDatasetLabel(r)}</TableCell>
                <TableCell>
                  {isRunActive(r.status) ? (
                    <div className="flex flex-col gap-1">
                      <EvalStatusBadge status={r.status} className="w-fit" />
                      <EvalProgressBar progress={r.progress} />
                    </div>
                  ) : (
                    <EvalStatusBadge status={r.status} />
                  )}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{passRateText(r)}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{r.stats ? formatCost(r.stats.cost) : '-'}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <RelativeTime value={r.created_at} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {list.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
            加载更多
          </Button>
        </div>
      )}
    </div>
  )
}
