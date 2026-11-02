import { describe, expect, it } from 'vitest'
import { buildSessionState, sessionSpanMs, totalMetrics } from './sessionState'
import { turnElapsedMs } from './TurnMetricsBar'
import { recordedTurnTiming, turnTiming, turnTtftMs } from './turnTiming'
import { T0, mk, recordedTurnEvents, toolTurnEvents } from './testEvents'

describe('轮次和会话的总耗时', () => {
  it('录制轮次采用原始总耗时，会话累加轮次，跨度保留用户等待时间', () => {
    const turns = buildSessionState([
      ...recordedTurnEvents('a', 1),
      ...recordedTurnEvents('b', 20, 600_000),
    ]).turns
    expect(turnElapsedMs(turns[0], Date.now())).toBe(71_235)
    expect(totalMetrics(turns).latencyMs).toBe(142_470)
    expect(sessionSpanMs(turns)).toBe(671_235)
    expect(turnTiming(turns[0]).startedAt).toBe(new Date(T0 - 86_400_000).toISOString())
    expect(turns[0].metrics.latencyMs).toBe(600) // 存储事件与回放指标未改写
    expect(turns[0].startedAt).toBe(new Date(T0).toISOString())
  })

  it('普通已结束、运行中、无时间信息的轮次保持原有口径', () => {
    const turn = buildSessionState(toolTurnEvents()).turns[0]
    expect(turnElapsedMs(turn, T0 + 100_000)).toBe(575)
    const running = buildSessionState(toolTurnEvents().slice(0, 4)).turns[0]
    expect(turnElapsedMs(running, T0 + 1000)).toBe(1000)
    expect(totalMetrics([turn, running]).latencyMs).toBe(575)
    expect(turnElapsedMs({ ...running, startedAt: 'invalid' }, T0 + 1000)).toBeNull()
    expect(turnElapsedMs({ ...turn, metrics: { ...turn.metrics, latencyMs: null } }, T0 + 1000)).toBe(575)
  })

  it('原始耗时缺失或非法时用有效原始起止时间，普通日志不会被误判为录制', () => {
    const source = { source_mode: 'recorded', original_started_at: new Date(T0).toISOString(),
      original_ended_at: new Date(T0 + 71235).toISOString() }
    expect(recordedTurnTiming([mk('log', { ...source, original_latency_ms: -1 })])?.durationMs).toBe(71235)
    expect(recordedTurnTiming([mk('log', { ...source, original_latency_ms: 0 })])?.durationMs).toBe(0)
    expect(recordedTurnTiming([mk('log', { ...source, source_mode: 'live' })])).toBeNull()
    expect(recordedTurnTiming([mk('log', { source_mode: 'recorded', original_started_at: 'bad', original_latency_ms: NaN })])).toBeNull()
  })

  it('录制 TTFT 仅采用有效来源值，缺少原始时间也不会回退到导入 TTFT', () => {
    const turn = buildSessionState(toolTurnEvents()).turns[0]
    expect(turnTtftMs(turn)).toBe(120)
    for (const value of [undefined, null, -1, NaN, Infinity, '602']) {
      expect(turnTtftMs({ ...turn, events: [mk('log', { source_mode: 'recorded', original_ttft_ms: value })] })).toBeNull()
    }
    for (const value of [0, 38_672]) {
      expect(turnTtftMs({ ...turn, events: [mk('log', { source_mode: 'recorded', original_ttft_ms: value })] })).toBe(value)
    }
    expect(turnTtftMs({ ...turn, events: [mk('log', { source_mode: 'live', original_ttft_ms: 999 })] })).toBe(120)
  })
})
