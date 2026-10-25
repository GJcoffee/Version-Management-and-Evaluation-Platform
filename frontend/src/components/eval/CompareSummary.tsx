import { AlertTriangle } from 'lucide-react'
import type { EvalCompareOut, EvalStatsOut } from '@/api/evalTypes'
import { Card, CardContent } from '@/components/ui/Card'
import { cn } from '@/lib/cn'
import { formatCost, formatDuration, formatPercent, formatTokens } from '@/lib/format'
import { TONE_CLASS, deltaTone, formatSigned, formatSignedPercent } from './evalUtils'

export const COMPARE_CLASSES = [
  { key: 'regressed', label: '退化', tone: 'bad' },
  { key: 'improved', label: '改善', tone: 'good' },
  { key: 'unchanged', label: '不变', tone: 'neutral' },
  { key: 'new', label: '新增', tone: 'neutral' },
  { key: 'missing', label: '缺失', tone: 'neutral' },
] as const

export function WarningBar({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null
  return (
    <div role="alert" className="space-y-1 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning" data-testid="compare-warnings">
      {warnings.map((w, i) => (
        <p key={i} className="flex items-start gap-1.5">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{w}</span>
        </p>
      ))}
    </div>
  )
}

const tokensTotal = (s: EvalStatsOut | null | undefined) => (s ? (s.tokens?.prompt ?? 0) + (s.tokens?.completion ?? 0) : null)

function relative(left: number | null | undefined, right: number | null | undefined): string {
  if (left === null || left === undefined || right === null || right === undefined || left === 0) return ''
  return `（${formatSignedPercent((right - left) / left, 1)}）`
}

interface MetricRow {
  key: string
  label: string
  left: string
  right: string
  delta: number | null | undefined
  deltaText: string
  higherIsBetter: boolean
}

/** 结论卡片：各分类计数、平均 Δ、指标差值（延迟 / 成本 / tokens 越低越好，用颜色区分变好变差） */
export function CompareSummary({ data }: { data: EvalCompareOut }) {
  const { summary, left, right } = data
  const md = summary.metric_deltas ?? {}
  const ls = left.stats
  const rs = right.stats
  const rows: MetricRow[] = [
    {
      key: 'pass_rate',
      label: '通过率',
      left: formatPercent(ls?.pass_rate, 1),
      right: formatPercent(rs?.pass_rate, 1),
      delta: md.pass_rate,
      deltaText: md.pass_rate === null || md.pass_rate === undefined ? '-' : `${md.pass_rate > 0 ? '+' : ''}${(md.pass_rate * 100).toFixed(1)}pp`,
      higherIsBetter: true,
    },
    {
      key: 'latency_p50_ms',
      label: '延迟 p50',
      left: formatDuration(ls?.latency_ms?.p50),
      right: formatDuration(rs?.latency_ms?.p50),
      delta: md.latency_p50_ms,
      deltaText: `${md.latency_p50_ms === null || md.latency_p50_ms === undefined ? '-' : `${md.latency_p50_ms > 0 ? '+' : ''}${Math.round(md.latency_p50_ms)}ms`}${relative(ls?.latency_ms?.p50, rs?.latency_ms?.p50)}`,
      higherIsBetter: false,
    },
    {
      key: 'cost',
      label: '成本',
      left: formatCost(ls?.cost),
      right: formatCost(rs?.cost),
      delta: md.cost,
      deltaText: `${formatSigned(md.cost, 4)}${relative(ls?.cost, rs?.cost)}`,
      higherIsBetter: false,
    },
    {
      key: 'tokens_total',
      label: 'Tokens',
      left: formatTokens(tokensTotal(ls)),
      right: formatTokens(tokensTotal(rs)),
      delta: md.tokens_total,
      deltaText: `${md.tokens_total === null || md.tokens_total === undefined ? '-' : `${md.tokens_total > 0 ? '+' : ''}${formatTokens(md.tokens_total)}`}${relative(tokensTotal(ls), tokensTotal(rs))}`,
      higherIsBetter: false,
    },
  ]
  const meanTone = deltaTone(summary.mean_delta, true)
  return (
    <div className="space-y-3" data-testid="compare-summary">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {COMPARE_CLASSES.map((c) => {
          const n = (summary[c.key] as number | undefined) ?? 0
          return (
            <Card key={c.key} data-testid={`count-${c.key}`}>
              <CardContent className="pt-4">
                <div className="text-xs text-muted-foreground">{c.label}</div>
                <div className={cn('mt-1 font-mono text-2xl font-semibold tabular-nums', n > 0 && c.tone === 'good' && 'text-success', n > 0 && c.tone === 'bad' && 'text-danger')}>
                  {n}
                </div>
              </CardContent>
            </Card>
          )
        })}
        <Card data-testid="mean-delta">
          <CardContent className="pt-4">
            <div className="text-xs text-muted-foreground">平均 Δ（分数）</div>
            <div className={cn('mt-1 font-mono text-2xl font-semibold tabular-nums', TONE_CLASS[meanTone])}>{formatSigned(summary.mean_delta, 3)}</div>
          </CardContent>
        </Card>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {rows.map((r) => {
          const tone = deltaTone(r.delta, r.higherIsBetter)
          return (
            <Card key={r.key} data-testid={`delta-${r.key}`} data-tone={tone}>
              <CardContent className="pt-4">
                <div className="text-xs text-muted-foreground">
                  {r.label}
                  <span className="ml-1">{r.higherIsBetter ? '（越高越好）' : '（越低越好）'}</span>
                </div>
                <div className="mt-1 font-mono text-sm tabular-nums">
                  {r.left} <span className="text-muted-foreground">→</span> {r.right}
                </div>
                <div className={cn('mt-0.5 font-mono text-lg font-semibold tabular-nums', TONE_CLASS[tone])}>{r.deltaText}</div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
