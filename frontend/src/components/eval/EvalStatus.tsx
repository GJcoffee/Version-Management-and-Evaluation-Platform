import type { EvalProgressOut, EvalRunStatusOut } from '@/api/evalTypes'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/cn'
import { EVAL_STATUS_LABEL, EVAL_STATUS_VARIANT, processedCount, progressRatio } from './evalUtils'

export function EvalStatusBadge({ status, className }: { status: EvalRunStatusOut; className?: string }) {
  return (
    <Badge variant={EVAL_STATUS_VARIANT[status]} className={className} data-testid="eval-status" data-status={status}>
      {EVAL_STATUS_LABEL[status]}
    </Badge>
  )
}

/** 进度条 + `已处理/总数`；total 为 0 时只显示空条 */
export function EvalProgressBar({
  progress,
  className,
  showText = true,
}: {
  progress: EvalProgressOut | null | undefined
  className?: string
  showText?: boolean
}) {
  const ratio = progressRatio(progress)
  const failed = progress?.failed ?? 0
  return (
    <div className={cn('flex items-center gap-2', className)} data-testid="eval-progress">
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(ratio * 100)}
        className="h-1.5 min-w-16 flex-1 overflow-hidden rounded-full bg-muted"
      >
        <div className={cn('h-full rounded-full transition-all', failed > 0 ? 'bg-warning' : 'bg-primary')} style={{ width: `${ratio * 100}%` }} />
      </div>
      {showText && (
        <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
          {processedCount(progress)}/{progress?.total ?? 0}
          {failed > 0 && <span className="text-danger"> · 失败 {failed}</span>}
        </span>
      )}
    </div>
  )
}
