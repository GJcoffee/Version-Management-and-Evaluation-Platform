import type { EventEnvelope } from '@/api/types'
import type { TraceSync } from './sessionState'

/** 毫秒精度用于与HiAgent原始记录逐项核对，避免不同四舍五入看起来不一致。 */
export function exactSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(3)}s`
}

function duration(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

export interface RemoteBreakdown {
  llmMs: number | null
  toolMs: number | null
}

/** 远端 Trace 汇总里的 LLM 调用 / 工具执行耗时（各调用之和，并发时可能重叠）；本地测量不在其中。 */
export function remoteBreakdown(events: readonly EventEnvelope[]): RemoteBreakdown | null {
  const metrics = events.filter((e) => e.type === 'metrics' && e.payload.source === 'hiagent_trace').at(-1)
  if (!metrics) return null
  const p = metrics.payload as Record<string, unknown>
  return { llmMs: duration(p.llm_api_ms), toolMs: duration(p.tool_execution_ms) }
}

/** 远端用时的文字：补齐中显示「同步中」，未取得显示「未取得」（悬停给原因），否则为精确秒数。 */
export function remoteValueText(trace: TraceSync | null, ms: number | null): string {
  if (trace?.status === 'pending') return '同步中'
  return ms === null ? '未取得' : exactSeconds(ms)
}

/** 远端指标状态的悬停说明 */
export function remoteTitle(trace: TraceSync, what: string): string {
  if (trace.status === 'pending') return `${what}：HiAgent Trace 正在后台补齐，到达后自动刷新`
  if (trace.status === 'unavailable') return `${what}未取得：${trace.note ?? '原因未知'}`
  return trace.note ? `${what}来自 HiAgent Trace；部分缺失：${trace.note}` : `${what}来自 HiAgent Trace`
}

/** 最近一个带整轮用量的指标事件里的 usage（HiAgent 可能不返回 token：对应字段缺失） */
export function reportedUsage(events: readonly EventEnvelope[]): Record<string, unknown> | undefined {
  const withUsage = events.filter((e) => e.type === 'metrics' && (e.payload as Record<string, unknown>).usage).at(-1)
  return withUsage ? (withUsage.payload as Record<string, unknown>).usage as Record<string, unknown> : undefined
}
