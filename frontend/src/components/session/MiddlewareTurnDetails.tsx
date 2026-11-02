import { useTurn } from '@/api/queries/sessions'
import { Badge } from '@/components/ui/Badge'
import { BlobJson } from './BlobView'
import type { MiddlewareSnapshot } from '@/api/types'

export function MiddlewareTurnDetails({ turnId, sessionId, initial }: {
  turnId: string
  sessionId?: string
  initial: MiddlewareSnapshot
}) {
  const query = useTurn(turnId)
  const snapshot = query.data?.middleware_snapshot ?? initial
  const step = snapshot.steps[0]
  if (!step) return null
  const label = { pending: '正在读取上一轮 JSON', running: '正在读取上一轮 JSON',
    ok: '已附带上一轮 JSON', error: 'JSON 处理失败', cancelled: 'JSON 处理已取消', timeout: 'JSON 处理超时' }[step.status]
  return (
    <div className="ml-9 space-y-1 text-xs" data-testid="middleware-result">
      <Badge variant={step.status === 'ok' ? 'success' : 'outline'}>{label}</Badge>
      {step.error && <p role="alert" className="text-danger">{step.error.message}</p>}
      {query.error && <p role="alert" className="text-danger">无法读取中间件记录：{query.error.message}</p>}
      <details>
        <summary className="cursor-pointer text-muted-foreground">查看处理配置与实际输入</summary>
        <p className="my-1 text-muted-foreground">处理用时 {step.duration_ms} ms</p>
        <BlobJson sessionId={sessionId} value={{
          middleware: snapshot,
          effective_input: query.data?.effective_input ?? null,
          ...(step.result_blob_hash ? { artifact: { $blob: step.result_blob_hash } } : {}),
        }} />
      </details>
    </div>
  )
}
