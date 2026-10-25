import type { ReactNode } from 'react'
import type { EvalStatsOut } from '@/api/evalTypes'
import { Card, CardContent } from '@/components/ui/Card'
import { formatCost, formatDuration, formatPercent, formatTokens } from '@/lib/format'
import { formatScore } from './evalUtils'

function Metric({ label, value, hint, testId }: { label: string; value: ReactNode; hint?: ReactNode; testId?: string }) {
  return (
    <Card data-testid={testId}>
      <CardContent className="pt-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 font-mono text-2xl font-semibold tabular-nums">{value}</div>
        {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  )
}

/** 指标卡片：通过率、各评估器均值、p50/p95 延迟、tokens、成本、错误率 */
export function MetricCards({ stats }: { stats: EvalStatsOut }) {
  const evs = Object.entries(stats.evaluators ?? {})
  const tokens = (stats.tokens?.prompt ?? 0) + (stats.tokens?.completion ?? 0)
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="metric-cards">
      <Metric label="通过率" value={formatPercent(stats.pass_rate, 1)} hint={`${stats.cases} 个用例 · ${stats.items} 次执行`} testId="metric-pass-rate" />
      {evs.map(([key, e]) => (
        <Metric
          key={key}
          label={`评估器 ${key} 均值`}
          value={formatScore(e.mean)}
          hint={`通过率 ${formatPercent(e.pass_rate ?? null, 1)}${e.errors ? ` · 出错 ${e.errors}` : ''}`}
          testId={`metric-ev-${key}`}
        />
      ))}
      <Metric
        label="延迟 p50 / p95"
        value={
          <span className="text-xl">
            {formatDuration(stats.latency_ms?.p50)} / {formatDuration(stats.latency_ms?.p95)}
          </span>
        }
        testId="metric-latency"
      />
      <Metric
        label="Tokens"
        value={formatTokens(tokens)}
        hint={`输入 ${formatTokens(stats.tokens?.prompt)} · 输出 ${formatTokens(stats.tokens?.completion)}`}
        testId="metric-tokens"
      />
      <Metric label="成本" value={formatCost(stats.cost)} hint={stats.cached ? `其中缓存命中 ${stats.cached} 次不重复计费` : undefined} testId="metric-cost" />
      <Metric label="错误率" value={formatPercent(stats.error_rate, 1)} hint={`失败 ${stats.failed} / ${stats.items}`} testId="metric-error-rate" />
    </div>
  )
}
