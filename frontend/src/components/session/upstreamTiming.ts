import type { EventEnvelope } from '@/api/types'

export interface UpstreamTiming {
  runMs: number
  llmMs: number | null
  toolMs: number | null
  queryMs: number | null
  transportMs: number | null
  adapterMs: number | null
}

function duration(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

/** 已核验的上游 Trace 指标独立于本地 turn.end，不用本地等待时间替代缺失值。 */
export function upstreamTiming(events: readonly EventEnvelope[]): UpstreamTiming | null {
  const unified = events.filter((e) => e.type === 'metrics' && e.payload.source === 'hiagent_trace')
  if (unified.length) {
    const metrics = Object.assign({}, ...events.filter((e) => e.type === 'metrics').map((e) => e.payload))
    const runMs = duration(metrics.upstream_run_ms)
    if (runMs !== null) return {
      runMs,
      llmMs: duration(metrics.llm_api_ms),
      toolMs: duration(metrics.tool_execution_ms),
      queryMs: duration(metrics.adapter_query_ms),
      transportMs: duration(metrics.transport_ms),
      adapterMs: null,
    }
  }
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i]
    if (event.type !== 'log') continue
    const p = event.payload as unknown as Record<string, unknown>
    if (p.metric_source !== 'actual_upstream_trace_timestamps' || p.source_mode === 'recorded') continue
    const runMs = duration(p.upstream_run_ms)
    if (runMs === null) continue
    const queries = [p.config_before_query_ms, p.trace_query_ms, p.config_after_query_ms].map(duration)
    return {
      runMs,
      llmMs: duration(p.llm_api_ms),
      toolMs: duration(p.tool_execution_ms),
      queryMs: queries.every((v) => v !== null) ? queries.reduce<number>((sum, v) => sum + v!, 0) : null,
      transportMs: duration(p.sse_transport_ms),
      adapterMs: duration(p.local_total_ms),
    }
  }
  return null
}

/** 只有全部已结束轮次具有同口径指标时才给出会话合计，不把部分结果伪装成总量。 */
export function upstreamTotalMs(turns: readonly { ended: boolean; events: readonly EventEnvelope[] }[]): number | null {
  if (!turns.length || turns.some((turn) => !turn.ended)) return null
  const timings = turns.map((turn) => upstreamTiming(turn.events))
  return timings.every((timing) => timing !== null)
    ? timings.reduce((sum, timing) => sum + timing!.runMs, 0) : null
}

/** 毫秒精度用于与HiAgent原始记录逐项核对，避免不同四舍五入看起来不一致。 */
export function exactSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(3)}s`
}
