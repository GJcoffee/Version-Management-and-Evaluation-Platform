import { useState } from 'react'
import { Pause, Play, RefreshCcw, RotateCcw } from 'lucide-react'
import type { EvalBudget, EvalProgressOut, EvalRunStatusOut, EvaluatorOut } from '@/api/evalTypes'
import { usePauseEval, useResumeEval, useRetryFailed } from '@/api/queries/evals'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { formatApiError } from '@/lib/apiError'
import { formatCost } from '@/lib/format'
import { toast } from '@/stores/toast'
import { RescoreDialog } from './RescoreDialog'
import { describeBudget, parseBudgetInput, pauseNotice, runActions } from './evalUtils'

/** 暂停提示条：手动暂停与因超预算自动暂停分别说明 */
export function PauseNotice({
  status,
  reason,
  budget,
  progress,
}: {
  status: EvalRunStatusOut
  reason: string | null | undefined
  budget: EvalBudget | undefined
  progress: EvalProgressOut | null | undefined
}) {
  const text = pauseNotice(status, reason, budget, progress?.cost_new)
  if (!text) return null
  const isBudget = reason === 'budget'
  return (
    <div
      role="status"
      data-testid="pause-notice"
      data-reason={isBudget ? 'budget' : 'manual'}
      className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning"
    >
      {text}
    </div>
  )
}

/** 因预算暂停后继续：先调高预算（留空表示不改）。后端在仍然超出预算时会拒绝继续 */
function ResumeBudgetDialog({
  budget,
  spent,
  loading,
  onClose,
  onSubmit,
}: {
  budget: EvalBudget | undefined
  spent: number | undefined
  loading: boolean
  onClose: () => void
  onSubmit: (b: EvalBudget) => void
}) {
  const [cost, setCost] = useState(budget?.max_cost != null ? String(budget.max_cost) : '')
  const [tokens, setTokens] = useState(budget?.max_tokens != null ? String(budget.max_tokens) : '')
  const [showErrors, setShowErrors] = useState(false)
  const c = parseBudgetInput(cost, false)
  const t = parseBudgetInput(tokens, true)
  const submit = () => {
    setShowErrors(true)
    if (c === 'invalid' || t === 'invalid') return
    const b: EvalBudget = {}
    if (c !== undefined) b.max_cost = c
    if (t !== undefined) b.max_tokens = t
    onSubmit(b)
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="调高预算并继续"
      description={`当前预算 ${describeBudget(budget) ?? '未设置'}${spent !== undefined ? `，本次已新增成本 ${formatCost(spent)}` : ''}。新的上限必须高于已用量，否则无法继续。`}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} loading={loading} data-testid="confirm-resume">
            继续评测
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <Field label="成本上限（元）" hint="留空 = 不修改" error={showErrors && c === 'invalid' ? '请输入不小于 0 的数字' : null}>
          <Input type="number" min={0} step="any" aria-label="成本上限" value={cost} onChange={(e) => setCost(e.target.value)} />
        </Field>
        <Field label="tokens 上限" hint="留空 = 不修改" error={showErrors && t === 'invalid' ? '请输入不小于 0 的整数' : null}>
          <Input type="number" min={0} aria-label="tokens 上限" value={tokens} onChange={(e) => setTokens(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  )
}

/**
 * 运行控制按钮：暂停 / 继续、重跑失败项、仅重新评分。
 * `onReactivated`：让已结束的评测重新运行（重跑失败项、重新评分）后调用，用于重新订阅实时进度。
 */
export function RunControls({
  runId,
  status,
  pauseReason,
  progress,
  failedCount,
  budget,
  evaluators,
  onReactivated,
}: {
  runId: string
  status: EvalRunStatusOut
  pauseReason: string | null | undefined
  progress: EvalProgressOut | null | undefined
  failedCount: number
  budget: EvalBudget | undefined
  evaluators: EvaluatorOut[]
  onReactivated?: () => void
}) {
  const pause = usePauseEval()
  const resume = useResumeEval()
  const retry = useRetryFailed()
  const [resumeOpen, setResumeOpen] = useState(false)
  const [rescoreOpen, setRescoreOpen] = useState(false)
  const act = runActions(status, failedCount)

  const doPause = async () => {
    try {
      await pause.mutateAsync({ id: runId })
      toast.success('已暂停评测')
    } catch (e) {
      toast.error('暂停失败', { description: formatApiError(e).message })
    }
  }
  const doResume = async (b?: EvalBudget) => {
    try {
      await resume.mutateAsync({ id: runId, budget: b })
      setResumeOpen(false)
      toast.success('已继续评测')
    } catch (e) {
      toast.error('无法继续', { description: formatApiError(e).message })
    }
  }
  const doRetry = async () => {
    try {
      const r = await retry.mutateAsync({ id: runId })
      if (r.retried > 0) {
        toast.success(`已重新排队 ${r.retried} 个失败项`)
        onReactivated?.()
      } else {
        toast.warning('没有需要重跑的失败项')
      }
    } catch (e) {
      toast.error('重跑失败项出错', { description: formatApiError(e).message })
    }
  }

  return (
    <>
      {act.canPause && (
        <Button size="sm" variant="outline" onClick={() => void doPause()} loading={pause.isPending} data-testid="pause-eval">
          <Pause className="h-3.5 w-3.5" />
          暂停
        </Button>
      )}
      {act.canResume && (
        <Button
          size="sm"
          onClick={() => (pauseReason === 'budget' ? setResumeOpen(true) : void doResume())}
          loading={resume.isPending && !resumeOpen}
          data-testid="resume-eval"
        >
          <Play className="h-3.5 w-3.5" />
          {pauseReason === 'budget' ? '调高预算并继续' : '继续'}
        </Button>
      )}
      {act.canRetryFailed && (
        <Button size="sm" variant="outline" onClick={() => void doRetry()} loading={retry.isPending} data-testid="retry-failed">
          <RotateCcw className="h-3.5 w-3.5" />
          重跑失败项（{failedCount}）
        </Button>
      )}
      {act.canRescore && (
        <Button size="sm" variant="outline" onClick={() => setRescoreOpen(true)} data-testid="open-rescore">
          <RefreshCcw className="h-3.5 w-3.5" />
          重新评分
        </Button>
      )}
      {resumeOpen && (
        <ResumeBudgetDialog
          budget={budget}
          spent={progress?.cost_new}
          loading={resume.isPending}
          onClose={() => setResumeOpen(false)}
          onSubmit={(b) => void doResume(b)}
        />
      )}
      {rescoreOpen && <RescoreDialog runId={runId} evaluators={evaluators} onClose={() => setRescoreOpen(false)} onStarted={() => onReactivated?.()} />}
    </>
  )
}
