import { Link } from 'react-router-dom'
import { ExternalLink, GitCompareArrows, WifiOff } from 'lucide-react'
import type { CompareGroup, CompareMode, CompareSide } from '@/api/compareTypes'
import type { TotalMetrics } from '@/components/session'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/cn'
import { formatCost, formatDuration, formatTokens } from '@/lib/format'
import { MODE_TAG } from './compareUtils'

function sideVersion(s: CompareSide): string {
  return s.version_seq !== null && s.version_seq !== undefined ? `v${s.version_seq}` : s.version_id.slice(-6)
}

export interface SideSummary {
  turns: number
  totals: TotalMetrics
  running: boolean
}

function SidePanel({ label, side, summary, testid }: { label: string; side: CompareSide; summary: SideSummary; testid: string }) {
  const t = summary.totals
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2" data-testid={testid}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-muted-foreground">{label}</span>
        <span className="truncate text-sm font-semibold" data-testid={`${testid}-agent`}>
          {side.agent_name ?? side.version_id}
        </span>
        <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs" title={`版本 ${side.version_id}`} data-testid={`${testid}-version`}>
          {sideVersion(side)}
        </span>
        {summary.running && <Badge variant="info">运行中</Badge>}
        <Link
          to={`/sessions/${side.session_id}`}
          className="ml-auto inline-flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
          title="打开这一侧的会话"
        >
          会话
          <ExternalLink className="h-3 w-3" />
        </Link>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-xs text-muted-foreground" data-testid={`${testid}-totals`}>
        <span>{summary.turns} 轮</span>
        <span title="累计 tokens">{formatTokens(t.totalTokens)} tokens</span>
        <span title="各轮耗时之和">{formatDuration(t.latencyMs || null)}</span>
        <span title="累计成本">{formatCost(t.cost)}</span>
        <span title="LLM 调用 / 工具调用">
          LLM ×{t.llmCalls} · 工具 ×{t.toolCalls}
        </span>
      </div>
    </div>
  )
}

export interface CompareHeaderProps {
  group: CompareGroup
  left: SideSummary
  right: SideSummary
  /** 重放进度 */
  replay?: { done: number; total: number; active: boolean } | null
  alignOn: boolean
  onToggleAlign: (v: boolean) => void
  reconnecting: boolean
}

const SIDE_LABEL: Record<CompareMode, [string, string]> = {
  live: ['左', '右'],
  history: ['左', '右'],
  replay: ['原会话', '重放'],
}

/** 顶部：模式标签、两侧的 agent 与版本、累计指标、重放进度、对齐视图开关 */
export function CompareHeader({ group, left, right, replay, alignOn, onToggleAlign, reconnecting }: CompareHeaderProps) {
  const [ll, rl] = SIDE_LABEL[group.mode]
  return (
    <div className="shrink-0 space-y-2 border-b border-border px-4 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <GitCompareArrows className="h-4 w-4 text-muted-foreground" />
        <h1 className="truncate text-sm font-semibold" data-testid="compare-title">
          {group.title?.trim() || '会话对比'}
        </h1>
        <Badge variant={group.mode === 'live' ? 'info' : group.mode === 'replay' ? 'warning' : 'secondary'} data-testid="compare-mode">
          {MODE_TAG[group.mode]}
        </Badge>
        {replay && (
          <span className="inline-flex items-center gap-2 text-xs" data-testid="replay-progress">
            <span className="font-mono tabular-nums">
              已完成 {replay.done} / {replay.total} 轮
            </span>
            <span className="h-1.5 w-28 overflow-hidden rounded-full bg-muted" aria-hidden="true">
              <span
                className={cn('block h-full rounded-full transition-all', replay.active ? 'bg-primary' : 'bg-success')}
                style={{ width: `${replay.total > 0 ? Math.min(100, (replay.done / replay.total) * 100) : 0}%` }}
              />
            </span>
            {replay.active ? <Badge variant="info">重放中</Badge> : replay.done >= replay.total ? <Badge variant="success">已完成</Badge> : <Badge variant="warning">已停止</Badge>}
          </span>
        )}
        {reconnecting && (
          <span role="status" className="inline-flex items-center gap-1 text-xs text-warning">
            <WifiOff className="h-3.5 w-3.5" />
            实时连接中断，正在重连…
          </span>
        )}
        <label className="ml-auto inline-flex cursor-pointer select-none items-center gap-2 text-xs" data-testid="align-toggle-label">
          <input
            type="checkbox"
            role="switch"
            className="h-4 w-4 accent-primary"
            checked={alignOn}
            onChange={(e) => onToggleAlign(e.target.checked)}
            data-testid="align-toggle"
          />
          对齐视图
        </label>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <SidePanel label={ll} side={group.left} summary={left} testid="side-left" />
        <SidePanel label={rl} side={group.right} summary={right} testid="side-right" />
      </div>
    </div>
  )
}
