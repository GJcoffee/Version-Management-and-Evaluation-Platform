import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import type { EvalCompareCase, EvalCompareCaseSide, EvalRunOut } from '@/api/evalTypes'
import { useEvalItem } from '@/api/queries/evals'
import { Badge } from '@/components/ui/Badge'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { formatCost, formatDuration } from '@/lib/format'
import { Drawer } from './Drawer'
import { ItemConversation, ScoreList, VerdictBadge } from './ItemDetail'
import { TONE_CLASS, deltaTone, formatScore, formatSigned, runVersionLabel } from './evalUtils'
import { cn } from '@/lib/cn'

function Side({ title, run, side, testId }: { title: string; run: Pick<EvalRunOut, 'id' | 'version_seq' | 'version_id'> | null; side: EvalCompareCaseSide | null; testId: string }) {
  const [idx, setIdx] = useState(0)
  const ids = side?.item_ids ?? []
  const itemId = ids[Math.min(idx, ids.length - 1)]
  const item = useEvalItem(run?.id, itemId)
  return (
    <div className="flex min-w-0 flex-col gap-3 p-4" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{title}</span>
        {run && <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{runVersionLabel(run)}</span>}
        <span className="font-mono text-xs text-muted-foreground">分数 {formatScore(side?.score)}</span>
        {ids.length > 1 && (
          <Select aria-label={`${title} 重复序号`} className="ml-auto w-28" value={String(idx)} onChange={(e) => setIdx(Number(e.target.value))}>
            {ids.map((_, i) => (
              <option key={i} value={i}>
                重复 #{i + 1}
              </option>
            ))}
          </Select>
        )}
      </div>
      {!side || ids.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">这一侧没有该用例的结果</p>
      ) : item.isLoading ? (
        <LoadingBlock />
      ) : item.data ? (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <VerdictBadge item={item.data} />
            {item.data.cached && <Badge variant="outline">缓存命中</Badge>}
            <span className="ml-auto font-mono text-muted-foreground">
              {formatDuration(item.data.latency_ms)} · {formatCost(item.data.cost)}
            </span>
          </div>
          <ScoreList scores={item.data.scores} />
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-muted-foreground">对话</span>
              {item.data.session_id && (
                <Link to={`/sessions/${item.data.session_id}`} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                  在会话页打开
                  <ExternalLink className="h-3 w-3" />
                </Link>
              )}
            </div>
            <div className="h-[30rem] overflow-hidden rounded-md border border-border" data-testid={`${testId}-conversation`}>
              <ItemConversation sessionId={item.data.session_id} className="h-full" />
            </div>
          </div>
        </>
      ) : (
        <p className="text-xs text-danger">无法加载该用例的执行结果</p>
      )}
    </div>
  )
}

/** 对比抽屉：左右并排，各自的评估器分数 / 理由和完整对话 */
export function CompareCaseDrawer({
  item,
  left,
  right,
  onClose,
}: {
  item: EvalCompareCase | null
  left: Pick<EvalRunOut, 'id' | 'version_seq' | 'version_id'> | null
  right: Pick<EvalRunOut, 'id' | 'version_seq' | 'version_id'> | null
  onClose: () => void
}) {
  // 输入 / 期望取任意一侧的第一个 item
  const anyRun = item?.left?.item_ids.length ? left : item?.right?.item_ids.length ? right : null
  const anyId = item?.left?.item_ids[0] ?? item?.right?.item_ids[0]
  const base = useEvalItem(anyRun?.id, anyId)
  const turns = base.data?.case_detail?.input?.turns ?? []
  const tone = deltaTone(item?.delta, true)
  return (
    <Drawer
      open={!!item}
      onClose={onClose}
      testId="compare-drawer"
      className="w-[min(1280px,100vw)]"
      title={
        item && (
          <span className="flex items-center gap-2">
            <span className="font-mono">{item.case_key}</span>
            <span className={cn('font-mono text-xs', TONE_CLASS[tone])}>Δ {formatSigned(item.delta, 2)}</span>
          </span>
        )
      }
    >
      {item && (
        <div>
          <div className="space-y-2 border-b border-border p-4 text-sm">
            <div className="flex flex-wrap gap-1.5">
              {item.tags.map((t) => (
                <Badge key={t} variant="secondary" className="font-mono">
                  {t}
                </Badge>
              ))}
            </div>
            <div>
              <div className="mb-0.5 text-xs font-semibold text-muted-foreground">输入</div>
              <div className="whitespace-pre-wrap rounded-md bg-muted/50 px-3 py-2">{turns.length > 0 ? turns.join('\n\n') : (base.data?.input_preview ?? '-')}</div>
            </div>
            <div>
              <div className="mb-0.5 text-xs font-semibold text-muted-foreground">期望</div>
              <div className="whitespace-pre-wrap rounded-md bg-muted/50 px-3 py-2">{base.data?.expected ?? <span className="text-muted-foreground">（用例没有 expected）</span>}</div>
            </div>
          </div>
          <div className="grid divide-x divide-border md:grid-cols-2">
            <Side title="左（基线）" run={left} side={item.left} testId="compare-left" />
            <Side title="右（对比）" run={right} side={item.right} testId="compare-right" />
          </div>
        </div>
      )}
    </Drawer>
  )
}
