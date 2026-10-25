import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Search } from 'lucide-react'
import type { EvalItemOut } from '@/api/evalTypes'
import { evalKeys, useEvalItems, type EvalItemFilter } from '@/api/queries/evals'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { LoadingBlock } from '@/components/ui/Spinner'
import { cn } from '@/lib/cn'
import { formatCost, formatDuration } from '@/lib/format'
import { useDebouncedValue } from '@/lib/useDebouncedValue'
import { VerdictBadge } from './ItemDetail'
import { formatScore, itemVerdict, scoreTone } from './evalUtils'

const ROW_H = 44
type Cls = NonNullable<EvalItemFilter['cls']>

const CLS_OPTIONS: Array<{ value: Cls; label: string }> = [
  { value: '', label: '全部' },
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '未通过' },
  { value: 'error', label: '失败' },
]

/** 当前（可能是实时更新后的）Item 是否符合筛选；用于实时更新后立刻隐藏不再符合的行 */
export function itemMatchesClass(it: EvalItemOut, cls: Cls): boolean {
  if (!cls) return true
  const v = itemVerdict(it)
  if (cls === 'passed') return v === 'passed'
  if (cls === 'failed') return v === 'not_passed'
  return v === 'failed' || it.scores.some((s) => s.status === 'error')
}

function ScoreCell({ item, evaluatorKey }: { item: EvalItemOut; evaluatorKey: string }) {
  const s = item.scores.find((x) => x.evaluator_key === evaluatorKey)
  if (!s) return <span className="text-muted-foreground">-</span>
  if (s.status === 'error') return <Badge variant="warning" title={s.reason ?? undefined}>出错</Badge>
  return (
    <Badge variant={scoreTone(s.passed)} className="font-mono tabular-nums" title={s.reason ?? undefined}>
      {formatScore(s.score)}
    </Badge>
  )
}

/**
 * 评测用例表：虚拟滚动 + 游标分页（滚动到接近底部时加载下一页）。
 * 运行中随 `eval.item_done`（live）覆盖对应行，并节流刷新列表以保证筛选结果正确。
 */
export function EvalItemsTable({
  runId,
  evaluatorKeys,
  repeats,
  live,
  liveVersion,
  running,
  onOpen,
  selectedId,
}: {
  runId: string
  evaluatorKeys: string[]
  repeats: number
  live: Record<string, EvalItemOut>
  /** 每收到一次 item_done 就变化 */
  liveVersion: number
  running: boolean
  onOpen: (item: EvalItemOut) => void
  selectedId?: string | null
}) {
  const qc = useQueryClient()
  const [cls, setCls] = useState<Cls>('')
  const [q, setQ] = useState('')
  const dq = useDebouncedValue(q.trim(), 300)
  const query = useEvalItems(runId, { cls, q: dq || undefined })
  const fetched = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data])
  const rows = useMemo(
    () => fetched.map((r) => live[r.id] ?? r).filter((r) => itemMatchesClass(r, cls)),
    // live 对象每次更新都会换引用，liveVersion 仅用于明确依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fetched, live, cls, liveVersion],
  )

  // 运行中：item_done 之后节流（2s）刷新用例列表，使筛选 / 搜索结果与服务端一致
  const lastRefresh = useRef(0)
  useEffect(() => {
    if (!running || liveVersion === 0) return
    const wait = Math.max(0, 2000 - (Date.now() - lastRefresh.current))
    const t = setTimeout(() => {
      lastRefresh.current = Date.now()
      void qc.invalidateQueries({ queryKey: [...evalKeys.run(runId), 'items'] })
    }, wait)
    return () => clearTimeout(t)
  }, [liveVersion, running, runId, qc])

  const parentRef = useRef<HTMLDivElement>(null)
  const virt = useVirtualizer({
    count: rows.length + (query.hasNextPage ? 1 : 0),
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_H,
    overscan: 8,
  })
  const vItems = virt.getVirtualItems()
  const lastIndex = vItems.length ? vItems[vItems.length - 1].index : -1
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  useEffect(() => {
    if (lastIndex >= rows.length - 5 && hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }, [lastIndex, rows.length, hasNextPage, isFetchingNextPage, fetchNextPage])

  // 列宽用内联样式（动态个数的评估器列无法用 Tailwind 类名表达）
  const n = evaluatorKeys.length
  const gridStyle = {
    gridTemplateColumns: `9rem minmax(10rem,1.2fr) minmax(10rem,1.2fr) 5rem${' 5rem'.repeat(n)} 4.5rem 5rem`,
  }
  const minWidth = 43.5 + n * 5 + 0.5 * (5 + n) + 1.5

  return (
    <div className="space-y-2" data-testid="eval-items">
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="结果筛选">
          {CLS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={cls === o.value}
              onClick={() => setCls(o.value)}
              data-testid={`filter-${o.value || 'all'}`}
              className={cn('rounded px-2.5 py-1 text-xs transition-colors', cls === o.value ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent')}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className="relative w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="搜索 case_key / 输入 / 输出…" aria-label="搜索用例" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <span className="ml-auto text-xs text-muted-foreground" data-testid="items-count">
          已加载 {rows.length}
          {hasNextPage ? '+' : ''} 条
        </span>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <div style={{ minWidth: `${minWidth}rem` }}>
          <div style={gridStyle} className="grid gap-2 bg-muted/60 px-3 py-1.5 text-xs font-medium text-muted-foreground">
            <span>case_key</span>
            <span>输入</span>
            <span>输出</span>
            <span>结果</span>
            {evaluatorKeys.map((k) => (
              <span key={k} className="truncate font-mono" title={k}>
                {k}
              </span>
            ))}
            <span className="text-right">耗时</span>
            <span className="text-right">成本</span>
          </div>
          {query.isLoading ? (
            <LoadingBlock />
          ) : rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{cls || dq ? '没有符合条件的用例' : running ? '等待用例执行…' : '没有用例'}</p>
          ) : (
            <div ref={parentRef} className="h-[30rem] overflow-y-auto" data-testid="items-scroller">
              <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
                {vItems.map((vi) => {
                  const it = rows[vi.index]
                  const style = { position: 'absolute' as const, top: 0, left: 0, width: '100%', height: ROW_H, transform: `translateY(${vi.start}px)` }
                  if (!it) {
                    return (
                      <div key="loader" style={style} className="flex items-center justify-center text-xs text-muted-foreground">
                        加载中…
                      </div>
                    )
                  }
                  const pending = it.status === 'pending' || it.status === 'running' || it.status === 'scoring'
                  return (
                    <button
                      type="button"
                      key={it.id}
                      style={{ ...style, ...gridStyle }}
                      onClick={() => onOpen(it)}
                      data-testid="item-row"
                      data-case-key={it.case_key}
                      data-verdict={itemVerdict(it)}
                      aria-current={selectedId === it.id || undefined}
                      className={cn(
                        'grid items-center gap-2 border-t border-border px-3 text-left text-sm hover:bg-muted/50',
                        selectedId === it.id && 'bg-accent/60',
                      )}
                    >
                      <span className="truncate font-mono text-xs" title={it.case_key}>
                        {it.case_key}
                        {(repeats > 1 || it.repeat_idx > 0) && <span className="text-muted-foreground"> #{it.repeat_idx + 1}</span>}
                      </span>
                      <span className="truncate text-xs" title={it.input_preview ?? undefined}>
                        {it.input_preview ?? '-'}
                      </span>
                      <span className="truncate text-xs text-muted-foreground" title={it.output_preview ?? undefined}>
                        {it.output_preview ?? (pending ? '' : '-')}
                      </span>
                      <span>
                        <VerdictBadge item={it} />
                      </span>
                      {evaluatorKeys.map((k) => (
                        <span key={k}>{pending ? <span className="text-muted-foreground">-</span> : <ScoreCell item={it} evaluatorKey={k} />}</span>
                      ))}
                      <span className="text-right font-mono text-xs tabular-nums">{formatDuration(it.latency_ms)}</span>
                      <span className="text-right font-mono text-xs tabular-nums">{formatCost(it.cost)}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
