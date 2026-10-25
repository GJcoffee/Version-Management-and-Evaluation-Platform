import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeftRight, GitCompare } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { EvalCompareCase, EvalRunOut } from '@/api/evalTypes'
import { useEval, useEvalCompare, useEvals } from '@/api/queries/evals'
import { CompareCaseDrawer } from '@/components/eval/CompareCaseDrawer'
import { COMPARE_CLASSES, CompareSummary, WarningBar } from '@/components/eval/CompareSummary'
import { EvalStatusBadge } from '@/components/eval/EvalStatus'
import {
  TONE_CLASS,
  deltaTone,
  formatScore,
  formatSigned,
  passRateText,
  runDatasetLabel,
  runTitle,
  runVersionLabel,
} from '@/components/eval/evalUtils'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { cn } from '@/lib/cn'
import { formatCost } from '@/lib/format'

function RunSelect({ label, value, onChange, runs, testId }: { label: string; value: string; onChange: (id: string) => void; runs: EvalRunOut[]; testId: string }) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-medium">{label}</label>
      <Select aria-label={label} data-testid={testId} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">选择评测…</option>
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {runTitle(r)} · {runVersionLabel(r)} · {runDatasetLabel(r)}
          </option>
        ))}
      </Select>
    </div>
  )
}

function RunInfo({ id, label }: { id: string | undefined; label: string }) {
  const q = useEval(id)
  const r = q.data
  return (
    <Card data-testid={`run-info-${label}`}>
      <CardContent className="space-y-1 pt-4 text-xs">
        <div className="text-muted-foreground">{label}</div>
        {!id ? (
          <p className="text-muted-foreground">未选择</p>
        ) : !r ? (
          <p className="text-muted-foreground">{q.error instanceof ApiError ? q.error.message : '加载中…'}</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Link to={`/evals/${r.id}`} className="text-sm font-medium text-primary hover:underline">
                {runTitle(r)}
              </Link>
              <EvalStatusBadge status={r.status} />
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-muted-foreground">
              <span>
                {r.agent_name} <span className="font-mono text-foreground">{runVersionLabel(r)}</span>
              </span>
              <span>
                数据集 <span className="font-mono text-foreground">{runDatasetLabel(r)}</span>
              </span>
              <span>
                通过率 <span className="font-mono text-foreground">{passRateText(r)}</span>
              </span>
              <span>
                成本 <span className="font-mono text-foreground">{r.stats ? formatCost(r.stats.cost) : '-'}</span>
              </span>
              <RelativeTime value={r.created_at} />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export default function EvalComparePage() {
  const [sp, setSp] = useSearchParams()
  const left = sp.get('left') ?? ''
  const right = sp.get('right') ?? ''
  const [cls, setCls] = useState<string>('regressed')
  const [opened, setOpened] = useState<EvalCompareCase | null>(null)
  const runsQ = useEvals({ limit: 100 })
  const runs = runsQ.data?.pages.flatMap((p) => p.items) ?? []
  const leftRun = useEval(left || undefined).data
  const rightRun = useEval(right || undefined).data
  // 当前选中的 run 即使不在最近 100 个里也要出现在选项中
  const options = (selected: EvalRunOut | undefined) => (selected && !runs.some((r) => r.id === selected.id) ? [selected, ...runs] : runs)

  const set = (k: 'left' | 'right', v: string) => {
    const next = new URLSearchParams(sp)
    if (v) next.set(k, v)
    else next.delete(k)
    setSp(next, { replace: true })
  }
  const swap = () => setSp({ left: right, right: left }, { replace: true })

  const ready = !!left && !!right && left !== right
  const cmp = useEvalCompare(left, right, cls)
  const first = cmp.data?.pages[0]
  const rows = cmp.data?.pages.flatMap((p) => p.cases.items) ?? []

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-6" data-testid="eval-compare">
      <div className="flex items-center gap-2">
        <GitCompare className="h-5 w-5 text-muted-foreground" />
        <h1 className="text-lg font-semibold">评测对比</h1>
      </div>

      <div className="grid items-end gap-3 md:grid-cols-[1fr_auto_1fr]">
        <RunSelect label="左（基线）" value={left} onChange={(v) => set('left', v)} runs={options(leftRun)} testId="select-left" />
        <Button variant="outline" size="icon" aria-label="交换左右" onClick={swap} disabled={!left && !right}>
          <ArrowLeftRight className="h-4 w-4" />
        </Button>
        <RunSelect label="右（对比）" value={right} onChange={(v) => set('right', v)} runs={options(rightRun)} testId="select-right" />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <RunInfo id={left || undefined} label="左（基线）" />
        <RunInfo id={right || undefined} label="右（对比）" />
      </div>

      {!ready ? (
        <EmptyState
          icon={GitCompare}
          title={left && left === right ? '请选择两个不同的评测' : '选择两个评测开始对比'}
          description="建议选择同一个数据集上、不同版本的评测；左侧作为基线。"
        />
      ) : cmp.isLoading ? (
        <LoadingBlock label="对比计算中…" className="py-16" />
      ) : cmp.error || !first ? (
        <p className="py-6 text-sm text-danger">无法对比：{cmp.error instanceof ApiError ? cmp.error.message : '未知错误'}</p>
      ) : (
        <>
          <WarningBar warnings={first.warnings ?? []} />
          <CompareSummary data={first} />

          <Tabs value={cls} onValueChange={setCls}>
            <TabsList>
              {COMPARE_CLASSES.map((c) => (
                <TabsTrigger
                  key={c.key}
                  value={c.key}
                  badge={<span className="font-mono text-xs text-muted-foreground">{(first.summary[c.key] as number | undefined) ?? 0}</span>}
                >
                  {c.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          {rows.length === 0 ? (
            <EmptyState title="这一类没有用例" className="py-10" />
          ) : (
            <Table data-testid="compare-cases">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>case_key</TableHead>
                  <TableHead className="text-right">左分数</TableHead>
                  <TableHead className="text-right">右分数</TableHead>
                  <TableHead className="text-right">Δ</TableHead>
                  <TableHead>tags</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((c) => {
                  const tone = deltaTone(c.delta, true)
                  return (
                    <TableRow
                      key={c.case_key}
                      className="cursor-pointer"
                      onClick={() => setOpened(c)}
                      data-testid="compare-row"
                      data-case-key={c.case_key}
                      data-class={c.class}
                    >
                      <TableCell className="font-mono text-xs">{c.case_key}</TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">{formatScore(c.left?.score)}</TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">{formatScore(c.right?.score)}</TableCell>
                      <TableCell className={cn('text-right font-mono text-xs font-semibold tabular-nums', TONE_CLASS[tone])}>{formatSigned(c.delta, 2)}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {c.tags.map((t) => (
                            <Badge key={t} variant="secondary" className="font-mono">
                              {t}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
          {cmp.hasNextPage && (
            <div className="flex justify-center">
              <Button variant="outline" size="sm" loading={cmp.isFetchingNextPage} onClick={() => void cmp.fetchNextPage()}>
                加载更多
              </Button>
            </div>
          )}
        </>
      )}

      <CompareCaseDrawer item={opened} left={leftRun ?? null} right={rightRun ?? null} onClose={() => setOpened(null)} />
    </div>
  )
}
