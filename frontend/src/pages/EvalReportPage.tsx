import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ClipboardCheck, GitCompare, OctagonX, WifiOff } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { EvalItemOut, EvalRunDetailOut } from '@/api/evalTypes'
import { evalKeys, useCancelEval, useEval, useEvals, type EvalItemFilter } from '@/api/queries/evals'
import { EvalItemDrawer } from '@/components/eval/EvalItemDrawer'
import { EvalItemsTable } from '@/components/eval/EvalItemsTable'
import { EvalProgressBar, EvalStatusBadge } from '@/components/eval/EvalStatus'
import { GatePanel } from '@/components/eval/GatePanel'
import { MetricCards } from '@/components/eval/MetricCards'
import { PauseNotice, RunControls } from '@/components/eval/RunControls'
import { StatsPanel } from '@/components/eval/StatsPanel'
import { useEvalStream } from '@/components/eval/useEvalStream'
import {
  compareHref,
  describeBudget,
  describeEvaluator,
  isRunActive,
  pendingHumanCount,
  orderForCompare,
  passRateText,
  runDatasetLabel,
  runTitle,
  runVersionLabel,
} from '@/components/eval/evalUtils'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { LoadingBlock } from '@/components/ui/Spinner'
import { formatApiError } from '@/lib/apiError'
import { toast } from '@/stores/toast'

type ItemCls = NonNullable<EvalItemFilter['cls']>

export default function EvalReportPage() {
  const { id } = useParams()
  const run = useEval(id, { pollMs: 5000 })
  if (run.isLoading) return <LoadingBlock label="加载评测…" className="py-24" />
  if (run.error || !run.data) {
    const notFound = run.error instanceof ApiError && run.error.status === 404
    return (
      <div className="p-6 text-sm text-danger">
        {notFound ? '评测不存在' : `无法加载评测：${run.error instanceof ApiError ? run.error.message : '未知错误'}`}
        <Link to="/evals" className="ml-3 text-primary underline">
          返回评测列表
        </Link>
      </div>
    )
  }
  return <ReportView key={id} run={run.data} />
}

function ReportView({ run }: { run: EvalRunDetailOut }) {
  const qc = useQueryClient()
  // 进入页面时是否在进行中，决定是否订阅 SSE（结束后保持连接状态，避免状态被重置）
  const [subscribe, setSubscribe] = useState(() => isRunActive(run.status))
  const stream = useEvalStream(run.id, subscribe)
  const live = stream.state
  const status = live.status ?? run.status
  const progress = live.progress ?? run.progress
  const stats = live.stats ?? run.stats
  const active = isRunActive(status)
  const pauseReason = status === 'paused' ? (live.pauseReason ?? run.pause_reason ?? progress.pause_reason ?? null) : null
  // 已结束的评测被「重跑失败项 / 重新评分」重新拉起后，需要重新订阅实时进度
  const reactivate = () => {
    setSubscribe(true)
    stream.reopen()
  }
  const [itemCls, setItemCls] = useState<ItemCls>('')
  const [itemTag, setItemTag] = useState('')
  const hasHuman = run.evaluators.some((e) => e.type === 'human')
  const pendingHuman = pendingHumanCount(stats)

  // 到达终态后刷新详情（finished_at、stats）和用例列表
  const terminal = live.status !== null && !isRunActive(live.status)
  useEffect(() => {
    if (!terminal) return
    void qc.invalidateQueries({ queryKey: evalKeys.run(run.id) })
    void qc.invalidateQueries({ queryKey: [...evalKeys.all, 'list'] })
  }, [terminal, qc, run.id])

  const cancel = useCancelEval()
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [opened, setOpened] = useState<EvalItemOut | null>(null)
  const openedLive = opened ? (live.items[opened.id] ?? opened) : null

  const evaluatorKeys = useMemo(() => run.evaluators.map((e) => e.key), [run.evaluators])
  const others = useEvals({ batch_id: run.batch_id }, { autoRefresh: 3000 })
  const batchRuns = (others.data?.pages.flatMap((p) => p.items) ?? []).filter((r) => r.id !== run.id)

  const doCancel = async () => {
    try {
      await cancel.mutateAsync(run.id)
      toast.success('已取消评测')
    } catch (e) {
      toast.error('取消失败', { description: formatApiError(e).message })
      throw e
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-6" data-testid="eval-report" data-status={status}>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold" data-testid="eval-title">
            {runTitle(run)}
          </h1>
          <EvalStatusBadge status={status} />
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {hasHuman && status !== 'queued' && (
              <Link
                to={`/evals/${run.id}/annotate`}
                data-testid="open-annotate"
                className="inline-flex h-7 items-center gap-1 rounded-md border border-input bg-background px-2.5 text-xs font-medium hover:bg-accent"
              >
                <ClipboardCheck className="h-3.5 w-3.5" />
                人工评审{pendingHuman > 0 ? `（待评 ${pendingHuman}）` : ''}
              </Link>
            )}
            <RunControls
              runId={run.id}
              status={status}
              pauseReason={pauseReason}
              progress={progress}
              failedCount={progress.failed ?? stats?.failed ?? 0}
              budget={run.budget}
              evaluators={run.evaluators}
              onReactivated={reactivate}
            />
            {active && (
              <Button size="sm" variant="outline" onClick={() => setConfirmCancel(true)} data-testid="cancel-eval">
                <OctagonX className="h-3.5 w-3.5" />
                取消评测
              </Button>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>
            Agent{' '}
            <Link to={`/agents/${run.agent_id}`} className="text-primary hover:underline">
              {run.agent_name ?? run.agent_id}
            </Link>{' '}
            <Link to={`/agents/${run.agent_id}?tab=versions`} className="rounded bg-muted px-1.5 py-0.5 font-mono text-foreground hover:bg-accent">
              {runVersionLabel(run)}
            </Link>
          </span>
          <span>
            数据集 <span className="font-mono text-foreground">{runDatasetLabel(run)}</span>
            {run.case_filter?.tags && run.case_filter.tags.length > 0 && <span className="font-mono">（标签 {run.case_filter.tags.join(', ')}）</span>}
          </span>
          <span>
            重复 <span className="font-mono text-foreground">{run.repeats}</span> · 并发 <span className="font-mono text-foreground">{run.concurrency}</span>
          </span>
          {describeBudget(run.budget) && (
            <span data-testid="run-budget">
              预算 <span className="font-mono text-foreground">{describeBudget(run.budget)}</span>
            </span>
          )}
          <span>
            创建 <RelativeTime value={run.created_at} />
          </span>
          {run.finished_at && (
            <span>
              结束 <RelativeTime value={run.finished_at} />
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">评估器</span>
          {run.evaluators.map((e) => (
            <Badge key={e.key} variant="outline" className="font-mono">
              {describeEvaluator(e)}
            </Badge>
          ))}
        </div>
      </div>

      <PauseNotice status={status} reason={pauseReason} budget={run.budget} progress={progress} />
      {run.error && <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">{run.error}</p>}
      {subscribe && stream.status === 'reconnecting' && (
        <div role="status" className="flex items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5 text-xs text-warning">
          <WifiOff className="h-3.5 w-3.5" />
          实时连接中断，正在重连…
        </div>
      )}

      {(active || (progress.total > 0 && !stats)) && (
        <Card>
          <CardContent className="space-y-2 pt-4">
            <EvalProgressBar progress={progress} />
            <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-muted-foreground" data-testid="progress-detail">
              <span>已完成 {progress.done}</span>
              <span>执行中 {(progress.running ?? 0) + (progress.scoring ?? 0)}</span>
              <span>排队 {progress.pending ?? 0}</span>
              <span>失败 {progress.failed}</span>
              <span>缓存命中 {progress.cached ?? 0}</span>
            </div>
          </CardContent>
        </Card>
      )}

      {batchRuns.length > 0 && (
        <Card data-testid="batch-runs">
          <CardHeader>
            <CardTitle>同批次的其他 run</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {batchRuns.map((r) => {
                const [left, right] = orderForCompare(run, r)
                return (
                  <li key={r.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                    <Link to={`/evals/${r.id}`} className="font-medium text-primary hover:underline">
                      {runTitle(r)}
                    </Link>
                    <span className="font-mono text-xs">{runVersionLabel(r)}</span>
                    <EvalStatusBadge status={r.status} />
                    <span className="font-mono text-xs text-muted-foreground">通过率 {passRateText(r)}</span>
                    <Link
                      to={compareHref(left.id, right.id)}
                      data-testid={`compare-with-${r.id}`}
                      className="ml-auto inline-flex h-7 items-center gap-1 rounded-md border border-input bg-background px-2.5 text-xs font-medium hover:bg-accent"
                    >
                      <GitCompare className="h-3.5 w-3.5" />
                      与本 run 对比
                    </Link>
                  </li>
                )
              })}
            </ul>
          </CardContent>
        </Card>
      )}

      {hasHuman && pendingHuman > 0 && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning" data-testid="pending-human">
          有 {pendingHuman} 条评分在等待人工评审，打分前不计入通过判定与汇总指标。
          <Link to={`/evals/${run.id}/annotate`} className="ml-1 font-medium underline">
            去评审
          </Link>
        </p>
      )}

      {stats ? (
        <>
          <MetricCards stats={stats} />
          <StatsPanel
            stats={stats}
            activeTag={itemTag}
            onShowFlaky={() => setItemCls('flaky')}
            onPickTag={(t) => setItemTag((cur) => (cur === t ? '' : t))}
          />
        </>
      ) : (
        <p className="text-xs text-muted-foreground">评测结束后显示通过率、评估器均值、延迟、tokens 和成本等汇总指标。</p>
      )}

      <EvalItemsTable
        runId={run.id}
        evaluatorKeys={evaluatorKeys}
        repeats={run.repeats}
        live={live.items}
        liveVersion={live.itemDoneCount}
        running={active}
        onOpen={setOpened}
        selectedId={opened?.id}
        hasHuman={hasHuman}
        cls={itemCls}
        onClsChange={setItemCls}
        tag={itemTag}
        onTagChange={setItemTag}
      />

      <GatePanel run={run} />

      <EvalItemDrawer runId={run.id} item={openedLive} onClose={() => setOpened(null)} />

      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        danger
        title="取消这次评测？"
        description="未开始的用例会被取消，正在执行的会被中断，已完成的结果保留。"
        confirmText="取消评测"
        cancelText="继续运行"
        onConfirm={doCancel}
      />
    </div>
  )
}
