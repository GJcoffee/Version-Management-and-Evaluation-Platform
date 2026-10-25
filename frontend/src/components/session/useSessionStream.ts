import { useMemo } from 'react'
import { apiUrl } from '@/api/client'
import { SESSION_EVENT_TYPES, type EventEnvelope } from '@/api/types'
import { useEventStream, type EventStreamStatus } from '@/hooks/useEventStream'
import {
  initialSessionState,
  isRunning,
  runningTurn,
  sessionReducer,
  type SessionStreamState,
  type TurnState,
} from './sessionState'

export interface UseSessionStreamOptions {
  /** 设为 false 时不连接（例如会话尚未加载完） */
  enabled?: boolean
}

export interface UseSessionStreamResult {
  /** 结构化状态：轮次 -> 消息 -> 步骤树 */
  state: SessionStreamState
  turns: TurnState[]
  status: EventStreamStatus
  /** 有轮次在运行（尚未收到 turn.end） */
  running: boolean
  /** 正在运行的轮次（没有则为 null） */
  runningTurn: TurnState | null
  /** 正在连接或回放历史：已连接但还没收到 idle / 任何事件 */
  loading: boolean
  /**
   * 重新订阅（从已收到的最大 seq 继续）。发起新一轮之后调用：
   * 连接因 idle 关闭后不会自动恢复。连接仍开着时调用也是安全的。
   */
  reopen: () => void
  close: () => void
}

/**
 * 订阅一个会话的 SSE 事件流，并还原成结构化状态（详设 14.3）。
 *
 * - 基于 useEventStream：断线自动带 after_seq 重连、按 seq 去重、收到 idle 后关闭；
 * - 回放历史与实时输出走同一条路径：打开已结束的会话时，事件回放完毕后收到 idle，状态即为完整内容；
 * - 发送新一轮（POST /sessions/{id}/turns 返回 202）之后调用 reopen()。
 *
 * 不依赖路由，可在同一页面里为多个会话各调用一次（双栏对比）。
 */
export function useSessionStream(sessionId: string | undefined, opts: UseSessionStreamOptions = {}): UseSessionStreamResult {
  const enabled = (opts.enabled ?? true) && !!sessionId
  const { state, status, reopen, close } = useEventStream<SessionStreamState, EventEnvelope>({
    buildUrl: (after) => (sessionId ? apiUrl(`/sessions/${sessionId}/stream`, { after_seq: after }) : null),
    reducer: sessionReducer,
    initialState: initialSessionState,
    eventTypes: SESSION_EVENT_TYPES,
    enabled,
  })
  return useMemo(
    () => ({
      state,
      turns: state.turns,
      status,
      running: isRunning(state),
      runningTurn: runningTurn(state),
      loading: enabled && (status === 'disabled' || status === 'connecting' || (status === 'open' && state.turns.length === 0)),
      reopen,
      close,
    }),
    [state, status, reopen, close, enabled],
  )
}
