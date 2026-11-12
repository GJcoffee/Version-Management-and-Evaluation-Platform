import { useTurn } from '@/api/queries/sessions'
import { Badge } from '@/components/ui/Badge'
import { BlobJson } from './BlobView'
import type { MiddlewareSnapshot } from '@/api/types'

type Step = MiddlewareSnapshot['steps'][number]
type Status = Step['status']

const INPUT_LABELS: Record<Status, string> = {
  pending: '正在读取上一轮 JSON', running: '正在读取上一轮 JSON',
  ok: '已附带上一轮 JSON', error: 'JSON 处理失败',
  cancelled: 'JSON 处理已取消', timeout: 'JSON 处理超时',
}
const OUTPUT_LABELS: Record<Status, string> = {
  pending: '正在校验本轮输出契约', running: '正在校验本轮输出契约',
  ok: '本轮输出契约通过', error: '本轮输出契约失败',
  cancelled: '输出契约校验已取消', timeout: '输出契约校验超时',
}
const DEFAULT_LABELS: Record<Status, string> = {
  pending: '中间件处理中', running: '中间件处理中',
  ok: '中间件处理完成', error: '中间件处理失败',
  cancelled: '中间件处理已取消', timeout: '中间件处理超时',
}

function isOutput(step: Step) {
  return step.phase === 'output' || step.id === 'output_contract'
}

export function MiddlewareTurnDetails({ turnId, sessionId, initial }: {
  turnId: string
  sessionId?: string
  initial: MiddlewareSnapshot
}) {
  const query = useTurn(turnId)
  const snapshot = query.data?.middleware_snapshot ?? initial
  const steps = snapshot.steps
  if (!steps.length) return null
  const artifactStep = steps.find((step) => step.result_blob_hash)
  return (
    <div className="ml-9 space-y-1 text-xs" data-testid="middleware-result">
      {steps.map((step, index) => {
        const labels = isOutput(step) ? OUTPUT_LABELS : step.id === 'json_artifact' ? INPUT_LABELS : DEFAULT_LABELS
        return (
          <div key={`${step.id}:${index}`} className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={step.status === 'ok' ? 'success' : 'outline'}>{labels[step.status]}</Badge>
              {isOutput(step) && step.stage != null && (
                <span className="text-muted-foreground">
                  阶段 {step.stage}{step.outputType ? ` · ${step.outputType}` : ''}
                  {step.artifactType != null ? ` · 产物 ${step.artifactType}` : ''}
                </span>
              )}
            </div>
            {step.error && <p role="alert" className="text-danger">{step.error.message}</p>}
          </div>
        )
      })}
      {query.error && <p role="alert" className="text-danger">无法读取中间件记录：{query.error.message}</p>}
      <details>
        <summary className="cursor-pointer text-muted-foreground">查看处理配置与实际输入</summary>
        <p className="my-1 text-muted-foreground">
          处理用时 {steps.map((step) => step.duration_ms).join(' / ')} ms
        </p>
        <BlobJson sessionId={sessionId} value={{
          middleware: snapshot,
          effective_input: query.data?.effective_input ?? null,
          ...(artifactStep?.result_blob_hash ? { artifact: { $blob: artifactStep.result_blob_hash } } : {}),
        }} />
      </details>
    </div>
  )
}
