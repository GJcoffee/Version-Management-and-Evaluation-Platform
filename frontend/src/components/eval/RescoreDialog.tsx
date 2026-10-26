import { useMemo, useState } from 'react'
import { useRescoreEval } from '@/api/queries/evals'
import type { EvalRescoreResult, EvaluatorOut } from '@/api/evalTypes'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { formatApiError } from '@/lib/apiError'
import { toast } from '@/stores/toast'
import { EvaluatorEditor } from './EvaluatorEditor'
import { buildRescoreConfigs, describeRescore, evaluatorFromOut, validateEvaluators, type EvaluatorForm } from './evalUtils'

/**
 * 仅重新评分：在现有评估器配置上增删改，提交后服务端按 evaluator_hash 只重算有变化 / 出错的评估器，
 * 不会重新调用被测 agent。
 */
export function RescoreDialog({
  runId,
  evaluators: evaluatorsProp,
  onClose,
  onStarted,
}: {
  runId: string
  evaluators: EvaluatorOut[]
  onClose: () => void
  /** 有用例进入重新评分（评测重新变为运行中）时调用 */
  onStarted?: (r: EvalRescoreResult) => void
}) {
  const rescore = useRescoreEval()
  // 打开时的配置作为基线固定下来，之后详情刷新不影响正在编辑的内容
  const [evaluators] = useState(evaluatorsProp)
  const baseline = useMemo(() => {
    const keys: string[] = []
    return evaluators.map((e) => {
      const f = evaluatorFromOut(e, keys)
      if (f) keys.push(f.key)
      return f
    })
  }, [evaluators])
  const convertible = baseline.every((f) => f !== null)
  const baseForms = useMemo(() => baseline.filter((f): f is EvaluatorForm => f !== null), [baseline])
  const [forms, setForms] = useState<EvaluatorForm[]>(baseForms)
  const [showErrors, setShowErrors] = useState(false)
  const [result, setResult] = useState<EvalRescoreResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async (body: { evaluators?: ReturnType<typeof buildRescoreConfigs> }) => {
    setError(null)
    try {
      const r = await rescore.mutateAsync({ id: runId, ...body })
      setResult(r)
      if (r.rescored_items > 0) {
        toast.success(`已开始为 ${r.rescored_items} 个用例重新评分`)
        onStarted?.(r)
      }
    } catch (e) {
      const f = formatApiError(e, '重新评分失败')
      setError([f.message, ...f.lines].join('\n'))
    }
  }

  const submit = () => {
    setShowErrors(true)
    const v = validateEvaluators(forms)
    if (v.global || Object.keys(v.byUid).length > 0) return
    void run({ evaluators: buildRescoreConfigs(evaluators, baseForms, forms) })
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="xl"
      title="仅重新评分"
      description="不重新运行被测 agent，只用新的评估器配置对已有的回复重新打分。配置没有变化的评估器沿用原结果，只重算有变化或出错的。"
      footer={
        result ? (
          <Button onClick={onClose} data-testid="rescore-close">
            关闭
          </Button>
        ) : (
          <>
            <Button variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button variant="outline" onClick={() => void run({})} disabled={rescore.isPending} data-testid="rescore-errors-only">
              只重算出错 / 过期的评分
            </Button>
            <Button onClick={submit} loading={rescore.isPending} disabled={!convertible} data-testid="submit-rescore">
              按下方配置重新评分
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-2 text-sm" data-testid="rescore-result" data-rescored={result.rescored_items}>
          <p>{describeRescore(result)}</p>
          {result.evaluators_changed.length > 0 && (
            <p className="text-xs text-muted-foreground">
              有变化的评估器：<span className="font-mono">{result.evaluators_changed.join(', ')}</span>
            </p>
          )}
          {result.rescored_items > 0 && <p className="text-xs text-muted-foreground">评测已回到运行中，结束后汇总指标会自动刷新。</p>}
        </div>
      ) : (
        <div className="space-y-3">
          {!convertible && <p className="text-xs text-warning">存在不认识的评估器类型，暂不能在界面上修改；可以点「只重算出错 / 过期的评分」。</p>}
          {convertible && <EvaluatorEditor value={forms} onChange={setForms} showErrors={showErrors} />}
          {error && (
            <p role="alert" className="whitespace-pre-wrap rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          )}
        </div>
      )}
    </Dialog>
  )
}
