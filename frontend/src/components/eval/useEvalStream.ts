import { useMemo } from 'react'
import { apiUrl } from '@/api/client'
import {
  EVAL_STREAM_EVENT_TYPES,
  type EvalItemOut,
  type EvalProgressOut,
  type EvalRunStatusOut,
  type EvalStatsOut,
  type EvalStreamEvent,
} from '@/api/evalTypes'
import { useEventStream, type EventStreamStatus } from '@/hooks/useEventStream'

/** 评测 SSE 累积出的实时状态；`status` 为 null 表示还没收到快照 */
export interface EvalStreamState {
  status: EvalRunStatusOut | null
  stats: EvalStatsOut | null
  progress: EvalProgressOut | null
  finishedAt: string | null
  /** 已完成的 Item（按 id 索引，后到的覆盖先到的） */
  items: Record<string, EvalItemOut>
  /** 收到的 item_done 次数，用于触发列表刷新 */
  itemDoneCount: number
}

export const initialEvalStreamState: EvalStreamState = {
  status: null,
  stats: null,
  progress: null,
  finishedAt: null,
  items: {},
  itemDoneCount: 0,
}

/** 纯函数 reducer：便于单测 */
export function evalStreamReducer(state: EvalStreamState, ev: EvalStreamEvent): EvalStreamState {
  switch (ev.type) {
    case 'eval.status':
      return {
        ...state,
        status: ev.status ?? state.status,
        stats: ev.stats ?? state.stats,
        progress: ev.progress ?? state.progress,
        finishedAt: ev.finished_at ?? state.finishedAt,
      }
    case 'eval.progress':
      return { ...state, progress: ev.progress ?? state.progress }
    case 'eval.item_done':
      if (!ev.item?.id) return state
      return { ...state, items: { ...state.items, [ev.item.id]: ev.item }, itemDoneCount: state.itemDoneCount + 1 }
    default:
      return state
  }
}

export interface UseEvalStreamResult {
  state: EvalStreamState
  status: EventStreamStatus
}

/**
 * 订阅 `/evals/{id}/stream`。run 已结束时服务端只发 eval.status 和 idle，随后连接关闭。
 * `enabled=false` 时不连接（例如详情还没加载）。
 */
export function useEvalStream(runId: string | undefined, enabled = true): UseEvalStreamResult {
  const { state, status } = useEventStream<EvalStreamState, EvalStreamEvent>({
    buildUrl: () => (runId ? apiUrl(`/evals/${runId}/stream`) : null),
    reducer: evalStreamReducer,
    initialState: initialEvalStreamState,
    eventTypes: EVAL_STREAM_EVENT_TYPES,
    enabled: enabled && !!runId,
  })
  return useMemo(() => ({ state, status }), [state, status])
}
