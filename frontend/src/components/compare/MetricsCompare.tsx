import { cn } from '@/lib/cn'
import { formatCost, formatDuration, formatTokens } from '@/lib/format'
import type { TurnState } from '@/components/session'
import { METRICS, metricDelta, metricValue, type MetricDef, type MetricKey, type Trend } from './compareUtils'

function fmt(key: MetricKey, v: number | null): string {
  if (v === null) return '-'
  switch (key) {
    case 'latency':
    case 'ttft':
      return formatDuration(v)
    case 'tokens':
      return formatTokens(v)
    case 'cost':
      return formatCost(v)
    default:
      return `×${v}`
  }
}

function fmtDelta(key: MetricKey, d: number): string {
  const sign = d > 0 ? '+' : d < 0 ? '-' : '±'
  const abs = Math.abs(d)
  switch (key) {
    case 'latency':
    case 'ttft':
      return `${sign}${formatDuration(abs)}`
    case 'tokens':
      return `${sign}${formatTokens(abs)}`
    case 'cost':
      return `${sign}${formatCost(abs)}`
    default:
      return `${sign}${abs}`
  }
}

const TREND_CLASS: Record<Trend, string> = {
  better: 'bg-success/15 text-success',
  worse: 'bg-danger/15 text-danger',
  neutral: 'bg-primary/10 text-primary',
  same: 'text-muted-foreground',
}

function MetricCell({ def, left, right, settled }: { def: MetricDef; left: TurnState | undefined; right: TurnState | undefined; settled: boolean }) {
  const l = metricValue(left, def.key)
  const r = metricValue(right, def.key)
  const d = settled ? metricDelta(l, r, def.lowerIsBetter) : null
  return (
    <div className="min-w-0" data-metric={def.key} data-trend={d?.trend}>
      <div className="text-[11px] text-muted-foreground">{def.label}</div>
      <div className="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 font-mono text-xs tabular-nums">
        <span data-side="left">{fmt(def.key, l)}</span>
        <span className="text-muted-foreground">→</span>
        <span data-side="right">{fmt(def.key, r)}</span>
        {d && (
          <span className={cn('rounded px-1 py-0.5 text-[11px]', TREND_CLASS[d.trend])} data-testid="metric-delta">
            {fmtDelta(def.key, d.delta)}
            {d.ratio !== null && d.trend !== 'same' && def.lowerIsBetter ? ` (${d.ratio > 0 ? '+' : ''}${(d.ratio * 100).toFixed(0)}%)` : ''}
          </span>
        )}
      </div>
    </div>
  )
}

/** 行底部的两侧指标对照：左 → 右，差值着色（耗时 / TTFT / tokens / 成本：右侧更低为绿、更高为红） */
export function MetricsCompare({ left, right }: { left: TurnState | undefined; right: TurnState | undefined }) {
  if (!left && !right) return null
  const settled = !!left?.ended && !!right?.ended
  return (
    <div
      className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md bg-muted/40 px-3 py-2 sm:grid-cols-3 lg:grid-cols-6"
      data-testid="metrics-compare"
      data-settled={settled || undefined}
    >
      {METRICS.map((def) => (
        <MetricCell key={def.key} def={def} left={left} right={right} settled={settled} />
      ))}
    </div>
  )
}
