import type { EvalStatsOut } from '@/api/evalTypes'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { cn } from '@/lib/cn'
import { formatCost, formatPercent } from '@/lib/format'
import { passRateTone, sortedPassAtK, sortedTagRows } from './evalUtils'

const BAR_CLASS = { success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger', muted: 'bg-muted-foreground/40' } as const

/**
 * 报告里 MetricCards 之外的统计：pass@k、不稳定用例（flaky）、成本明细，以及按标签分组的通过率。
 * 没有对应数据的部分不显示（例如 repeats < k 时没有 pass@k）。
 */
export function StatsPanel({
  stats,
  onShowFlaky,
  onPickTag,
  activeTag,
}: {
  stats: EvalStatsOut
  /** 点击「查看不稳定用例」 */
  onShowFlaky?: () => void
  /** 点击标签行：按该标签筛选用例表 */
  onPickTag?: (tag: string) => void
  activeTag?: string
}) {
  const passAt = sortedPassAtK(stats.pass_at_k)
  const tags = sortedTagRows(stats.by_tag)
  const flaky = stats.flaky_cases
  const hasCostExtra = (stats.cost_wasted ?? 0) > 0 || (stats.cost_new !== undefined && stats.cost_new !== stats.cost)
  if (passAt.length === 0 && flaky === undefined && tags.length === 0 && !hasCostExtra) return null
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]" data-testid="stats-panel">
      <div className="space-y-3">
        {(passAt.length > 0 || flaky !== undefined) && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>稳定性</CardTitle>
                <CardDescription>pass@k：每个用例重复 n 次中，随机取 k 次至少通过一次的概率，再对用例取平均。</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {passAt.length > 0 ? (
                <div className="flex flex-wrap gap-2" data-testid="pass-at-k">
                  {passAt.map(([k, v]) => (
                    <div key={k} className="rounded-md border border-border px-3 py-1.5" data-testid={`pass-at-${k}`}>
                      <div className="text-xs text-muted-foreground">pass@{k}</div>
                      <div className="font-mono text-lg font-semibold tabular-nums">{formatPercent(v, 1)}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">重复次数不足，没有 pass@k（需要 repeats ≥ k）。</p>
              )}
              {flaky !== undefined && (
                <div className="flex items-center gap-3 text-sm" data-testid="flaky-summary" data-flaky={flaky}>
                  <span>
                    不稳定用例 <span className={cn('font-mono text-lg font-semibold tabular-nums', flaky > 0 && 'text-warning')}>{flaky}</span>
                    <span className="ml-1 text-xs text-muted-foreground">
                      {stats.cases > 0 ? `（占 ${formatPercent(flaky / stats.cases, 1)}）` : ''}
                    </span>
                  </span>
                  {flaky > 0 && onShowFlaky && (
                    <button type="button" className="text-xs text-primary hover:underline" onClick={onShowFlaky} data-testid="show-flaky">
                      查看不稳定用例
                    </button>
                  )}
                </div>
              )}
              <p className="text-xs text-muted-foreground">不稳定：同一用例多次重复里有通过也有未通过（0 &lt; 用例通过率 &lt; 1）。</p>
              {hasCostExtra && (
                <p className="text-xs text-muted-foreground" data-testid="cost-extra">
                  新增成本 {formatCost(stats.cost_new)}（不含缓存命中）
                  {(stats.cost_wasted ?? 0) > 0 && <span> · 失败尝试浪费 {formatCost(stats.cost_wasted)}</span>}
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </div>
      {tags.length > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>按标签分组</CardTitle>
              <CardDescription>各标签下用例的平均通过率，最弱的在前；点击标签可筛选下方用例表。</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1" data-testid="by-tag">
              {tags.map((t) => (
                <li key={t.tag}>
                  <button
                    type="button"
                    disabled={!onPickTag}
                    onClick={() => onPickTag?.(t.tag)}
                    aria-pressed={activeTag === t.tag}
                    data-testid={`tag-row-${t.tag}`}
                    className={cn(
                      'grid w-full grid-cols-[8rem_1fr_4rem_4rem] items-center gap-3 rounded px-2 py-1 text-left text-xs',
                      onPickTag && 'hover:bg-accent',
                      activeTag === t.tag && 'bg-accent',
                    )}
                  >
                    <span className="truncate font-mono" title={t.tag}>
                      {t.tag}
                    </span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                      <span className={cn('block h-full rounded-full', BAR_CLASS[passRateTone(t.passRate)])} style={{ width: `${(t.passRate ?? 0) * 100}%` }} />
                    </span>
                    <span className="text-right font-mono tabular-nums">{formatPercent(t.passRate, 1)}</span>
                    <span className="text-right text-muted-foreground">{t.cases} 个用例</span>
                  </button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
