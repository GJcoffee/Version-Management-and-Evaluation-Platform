import { useEffect } from 'react'
import type { EvalItemDetailOut, EvalItemOut } from '@/api/evalTypes'
import { useEvalItem } from '@/api/queries/evals'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Drawer } from './Drawer'
import { ItemDetailBody } from './ItemDetail'

/** 把列表里的 Item 补成详情结构（详情还在加载时先显示已有信息） */
export function asDetail(item: EvalItemOut, detail?: EvalItemDetailOut): EvalItemDetailOut {
  return detail ?? { ...item, output: item.output_preview, turns: [], case_detail: { expected: item.expected, tags: item.tags } }
}

/** 报告页的用例抽屉：输入、期望、各评估器的分数与理由，以及该次执行的完整对话 */
export function EvalItemDrawer({ runId, item, onClose }: { runId: string; item: EvalItemOut | null; onClose: () => void }) {
  const detail = useEvalItem(runId, item?.id)
  const { refetch } = detail
  const detailStatus = detail.data?.status
  const liveStatus = item?.status
  // 运行中打开时用例可能还没跑完：状态变化后重新取详情
  useEffect(() => {
    if (item && detailStatus && liveStatus && detailStatus !== liveStatus) void refetch()
  }, [item, detailStatus, liveStatus, refetch])

  return (
    <Drawer
      open={!!item}
      onClose={onClose}
      testId="item-drawer"
      title={
        item && (
          <span className="font-mono">
            {item.case_key}
            {item.repeat_idx > 0 && <span className="text-muted-foreground"> #{item.repeat_idx + 1}</span>}
          </span>
        )
      }
    >
      {item &&
        (detail.isLoading && !detail.data ? (
          <LoadingBlock />
        ) : (
          <ItemDetailBody item={asDetail(item, detail.data)} />
        ))}
    </Drawer>
  )
}
