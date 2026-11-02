import type { EventEnvelope } from '@/api/types'
import type { TurnState } from './sessionState'

export interface TurnTiming {
  startedAt: string | null
  endedAt: string | null
  durationMs: number | null
}

function timestamp(value: unknown): string | null {
  return typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : null
}

/** 录制回放的事件时间是导入时间；原始轮次总耗时与起止时间在来源日志里。 */
export function recordedTurnTiming(events: readonly EventEnvelope[]): TurnTiming | null {
  for (const event of events) {
    if (event.type !== 'log') continue
    const payload = event.payload as unknown as Record<string, unknown>
    if (payload.source_mode !== 'recorded') continue
    const startedAt = timestamp(payload.original_started_at)
    const endedAt = timestamp(payload.original_ended_at)
    const latency = payload.original_latency_ms
    const span = startedAt && endedAt ? Date.parse(endedAt) - Date.parse(startedAt) : null
    const durationMs = typeof latency === 'number' && Number.isFinite(latency) && latency >= 0
      ? latency
      : span !== null && span >= 0 ? span : null
    if (durationMs !== null) return { startedAt, endedAt, durationMs }
  }
  return null
}

export type TimedTurn = Pick<TurnState, 'metrics' | 'startedAt' | 'ended'> &
  Partial<Pick<TurnState, 'events' | 'endedAt'>>

/** 录制来源未提供原始 TTFT 时，不能用导入过程的首 token 时间。 */
export function turnTtftMs(turn: TimedTurn): number | null {
  for (const event of turn.events ?? []) {
    if (event.type !== 'log') continue
    const payload = event.payload as unknown as Record<string, unknown>
    if (payload.source_mode !== 'recorded') continue
    const ttft = payload.original_ttft_ms
    return typeof ttft === 'number' && Number.isFinite(ttft) && ttft >= 0 ? ttft : null
  }
  return turn.metrics.ttftMs
}

/** 展示口径：录制轮次用原始执行总耗时，普通轮次用 turn.end，运行中按当前时间累计。 */
export function turnTiming(turn: TimedTurn, now?: number): TurnTiming {
  const recorded = recordedTurnTiming(turn.events ?? [])
  if (recorded) return recorded
  const startedAt = turn.startedAt
  const endedAt = turn.endedAt ?? null
  let durationMs = turn.metrics.latencyMs
  if (durationMs === null && startedAt) {
    const start = Date.parse(startedAt)
    const end = turn.ended ? (endedAt ? Date.parse(endedAt) : NaN) : now
    if (Number.isFinite(start) && end !== undefined && Number.isFinite(end)) {
      durationMs = Math.max(0, end - start)
    }
  }
  return { startedAt, endedAt, durationMs }
}
