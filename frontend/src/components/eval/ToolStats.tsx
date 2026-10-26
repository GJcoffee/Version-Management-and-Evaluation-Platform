import type { EvalMetricDelta, EvalStatsOut, EvalToolDelta } from '@/api/evalTypes'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { cn } from '@/lib/cn'
import { formatDuration, formatPercent } from '@/lib/format'
import { TONE_CLASS, deltaTone, formatSigned } from './evalUtils'

const fmtPerCase = (n: number | null | undefined) => (n === null || n === undefined ? '-' : n.toFixed(2))

/** 报告页：按工具的技术指标。旧报告（没有 tools）和没有工具调用的评测不显示。 */
export function ToolStatsTable({ stats }: { stats: EvalStatsOut }) {
  const rows = Object.entries(stats.tools ?? {})
  if (rows.length === 0) return null
  return (
    <Card data-testid="tool-stats">
      <CardHeader>
        <div>
          <CardTitle>按工具指标</CardTitle>
          <CardDescription>来自 tool.call / tool.result 事件；每用例指每个已结束的执行；耗时只统计带 latency_ms 的调用。</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>工具</TableHead>
              <TableHead className="text-right">调用次数</TableHead>
              <TableHead className="text-right">平均每用例</TableHead>
              <TableHead className="text-right">耗时 p50</TableHead>
              <TableHead className="text-right">耗时 p95</TableHead>
              <TableHead className="text-right">失败率</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(([name, t]) => (
              <TableRow key={name} data-testid={`tool-row-${name}`}>
                <TableCell className="font-mono text-xs">{name}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{t.calls}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{fmtPerCase(t.calls_per_case)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{formatDuration(t.latency_ms.p50)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{formatDuration(t.latency_ms.p95)}</TableCell>
                <TableCell className={cn('text-right font-mono tabular-nums', t.errors > 0 && 'text-danger')}>
                  {formatPercent(t.error_rate, 1)}
                  {t.errors > 0 && <span className="ml-1 text-xs text-muted-foreground">（{t.errors} 次）</span>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

/** 一格：左 → 右 与变化量；higherIsBetter 为 null 表示只描述变化、不判断好坏 */
function DeltaCell({
  m,
  format,
  formatDelta,
  higherIsBetter,
  testId,
}: {
  m: EvalMetricDelta
  format: (n: number | null) => string
  formatDelta: (n: number | null) => string
  higherIsBetter: boolean | null
  testId: string
}) {
  const tone = higherIsBetter === null ? 'neutral' : deltaTone(m.delta, higherIsBetter)
  return (
    <TableCell className="text-right font-mono text-xs tabular-nums" data-testid={testId} data-tone={tone}>
      <div>
        {format(m.left)} <span className="text-muted-foreground">→</span> {format(m.right)}
      </div>
      <div className={cn('font-semibold', TONE_CLASS[tone])}>{formatDelta(m.delta)}</div>
    </TableCell>
  )
}

const fmtMs = (n: number | null) => formatDuration(n)
const fmtMsDelta = (n: number | null) => (n === null ? '-' : `${n > 0 ? '+' : ''}${Math.round(n)}ms`)
const fmtRate = (n: number | null) => formatPercent(n, 1)
const fmtRateDelta = (n: number | null) => (n === null ? '-' : `${n > 0 ? '+' : ''}${(n * 100).toFixed(1)}pp`)

/** 对比页：每个工具在两次评测间的指标变化（延迟、失败率越低越好；调用次数只描述变化） */
export function ToolCompareTable({ tools }: { tools: Record<string, EvalToolDelta> | undefined }) {
  const rows = Object.entries(tools ?? {})
  if (rows.length === 0) return null
  return (
    <Card data-testid="tool-compare">
      <CardHeader>
        <div>
          <CardTitle>按工具指标变化</CardTitle>
          <CardDescription>左 → 右与变化量。耗时、失败率越低越好；只出现在一侧的工具，另一侧调用次数记 0。</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>工具</TableHead>
              <TableHead className="text-right">调用次数</TableHead>
              <TableHead className="text-right">平均每用例</TableHead>
              <TableHead className="text-right">耗时 p50</TableHead>
              <TableHead className="text-right">耗时 p95</TableHead>
              <TableHead className="text-right">失败率</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(([name, t]) => (
              <TableRow key={name} data-testid={`tool-delta-${name}`}>
                <TableCell className="font-mono text-xs">{name}</TableCell>
                <DeltaCell testId="calls" m={t.calls} format={(n) => String(n ?? '-')} formatDelta={(n) => formatSigned(n, 0)} higherIsBetter={null} />
                <DeltaCell testId="calls-per-case" m={t.calls_per_case} format={fmtPerCase} formatDelta={(n) => formatSigned(n, 2)} higherIsBetter={null} />
                <DeltaCell testId="p50" m={t.latency_ms.p50} format={fmtMs} formatDelta={fmtMsDelta} higherIsBetter={false} />
                <DeltaCell testId="p95" m={t.latency_ms.p95} format={fmtMs} formatDelta={fmtMsDelta} higherIsBetter={false} />
                <DeltaCell testId="error-rate" m={t.error_rate} format={fmtRate} formatDelta={fmtRateDelta} higherIsBetter={false} />
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
