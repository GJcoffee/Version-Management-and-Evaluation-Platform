import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { traceStatus, upstreamTiming, upstreamTotalMs } from './upstreamTiming'
import { buildSessionState } from './sessionState'
import { buildStepTree } from './traceTree'
import { TurnMetricsBar, turnElapsedMs } from './TurnMetricsBar'
import { TraceViewer } from './TraceViewer'
import { mk, T0, toolTurnEvents } from './testEvents'

const source = {
  metric_source: 'actual_upstream_trace_timestamps',
  upstream_run_ms: 31668, llm_api_ms: 21941, tool_execution_ms: 1082,
  config_before_query_ms: 1908, trace_query_ms: 3510, config_after_query_ms: 1860,
  sse_transport_ms: 32007, local_total_ms: 39925,
}
const metricLog = mk('log', source, { seq: 100, at: 40000 })

describe('HiAgent与本地耗时分开显示', () => {
  it('采用原始Trace的远端整轮值，独立求和查询，不用LLM累计冒充整轮', () => {
    expect(upstreamTiming([metricLog])).toEqual({
      runMs: 31668, llmMs: 21941, toolMs: 1082, queryMs: 7278, transportMs: 32007, adapterMs: 39925,
    })
    expect(upstreamTiming([mk('log', { ...source, config_after_query_ms: undefined })])?.queryMs).toBeNull()
    for (const upstream_run_ms of [-1, NaN, Infinity, '31668']) {
      expect(upstreamTiming([mk('log', { ...source, upstream_run_ms })])).toBeNull()
    }
    expect(upstreamTiming([mk('log', { ...source, metric_source: 'other' })])).toBeNull()
    expect(upstreamTiming([mk('log', { ...source, source_mode: 'recorded' })])).toBeNull()
  })

  it('混合来源、未结束或缺失上游指标时不展示误导性的会话合计', () => {
    const turn = { ended: true, events: [metricLog] }
    expect(upstreamTotalMs([turn, turn])).toBe(63336)
    expect(upstreamTotalMs([turn, { ended: true, events: [] }])).toBeNull()
    expect(upstreamTotalMs([{ ...turn, ended: false }])).toBeNull()
    expect(upstreamTotalMs([])).toBeNull()
  })

  it('指标条同时展示各自口径，不改原本地耗时或Token', () => {
    const turn = buildSessionState([...toolTurnEvents(), metricLog]).turns[0]
    render(<TurnMetricsBar turn={turn} />)
    expect(screen.getByText('HiAgent 远端用时 31.668s')).toBeInTheDocument()
    expect(screen.getByText('本地用时 0.575s')).toBeInTheDocument()
    expect(screen.getByText('LLM累计 21.941s')).toBeInTheDocument()
    expect(screen.getByText('工具累计 1.082s')).toBeInTheDocument()
    expect(screen.getByText('查询 7.278s')).toBeInTheDocument()
    expect(screen.getByText('SSE接收 32.007s')).toBeInTheDocument()
    expect(turn.metrics.latencyMs).toBe(575)
  })

  it('Trace头部同时显示来源耗时和本地范围', () => {
    const events = [...toolTurnEvents(), metricLog]
    render(<TraceViewer steps={buildStepTree(events)} durationMs={40838} upstream={upstreamTiming(events)} />)
    expect(screen.getByTestId('trace-duration')).toHaveTextContent('本地用时 40.838s · HiAgent 远端用时 31.668s')
  })
})

const remoteMetrics = mk('metrics', {
  scope: 'turn', source: 'hiagent_trace', upstream_run_ms: 20_000, llm_api_ms: 15_000,
  tool_execution_ms: 1_000, trace_status: 'complete',
}, { seq: 100, at: 560 })
const fetchMetrics = mk('metrics', { scope: 'turn', transport_ms: 21_000, trace_fetch_ms: 12_000 }, { seq: 101, at: 570 })

describe('本地用时与HiAgent远端用时', () => {
  const turn = () => buildSessionState([...toolTurnEvents(), remoteMetrics, fetchMetrics]).turns[0]

  it('指标条分别显示本地用时和远端用时，悬停说明 Trace 补齐不计入本地用时', () => {
    render(<TurnMetricsBar turn={turn()} />)
    expect(screen.getByText('HiAgent 远端用时 20.000s')).toBeInTheDocument()
    expect(screen.getByText('本地用时 0.575s')).toBeInTheDocument()
    const title = document.querySelector('[data-metric="latency"]')!.getAttribute('title')!
    expect(title).toContain('Trace 补齐 12.000s')
    expect(title).toContain('不计入')
    expect(screen.getByText('Trace补齐 12.000s')).toBeInTheDocument()
  })

  it('Trace头部与指标条同口径', () => {
    const t = turn()
    render(<TraceViewer steps={t.steps} durationMs={t.metrics.latencyMs} upstream={upstreamTiming(t.events)} />)
    expect(screen.getByTestId('trace-duration')).toHaveTextContent('本地用时 0.575s · HiAgent 远端用时 20.000s')
    expect(screen.getByTestId('trace-duration').getAttribute('title')).toContain('Trace 补齐 12.000s')
  })

  it('等待 Trace 补查时，计时停在最终回复到达的时刻', () => {
    const running = buildSessionState(toolTurnEvents().slice(0, 8)).turns[0] // 到 message.assistant 为止，尚无 turn.end
    expect(running.ended).toBe(false)
    expect(turnElapsedMs(running, T0 + 30_000)).toBe(571)
  })
})

describe('Trace 未取得时说明原因', () => {
  const unavailable = (note: string) => mk('metrics', { scope: 'turn', trace_status: 'unavailable', trace_note: note }, { seq: 90, at: 560 })

  it('读取 Trace 状态与原因', () => {
    expect(traceStatus([unavailable('凭据未提供 observe_headers')])).toEqual({
      status: 'unavailable', note: '凭据未提供 observe_headers',
    })
    expect(traceStatus([remoteMetrics])).toEqual({ status: 'complete', note: null })
    expect(traceStatus([mk('metrics', { trace_status: 'bogus' })])).toBeNull()
    expect(traceStatus([])).toBeNull()
  })

  it('LLM 调用数显示“未取得”并在悬停给出原因，而不是 ×0 或隐藏', () => {
    const events = [
      mk('turn.start', { input: { role: 'user', content: '你好' }, history_len: 0 }, { seq: 1, at: 0 }),
      mk('message.assistant', { message: { role: 'assistant', content: '好' } }, { seq: 2, at: 100 }),
      mk('metrics', { scope: 'turn', source: 'hiagent_sse', usage: { prompt_tokens: 9, completion_tokens: 3 } }, { seq: 3, at: 101 }),
      unavailable('Trace 查询 HTTP 401'),
      mk('turn.end', { status: 'completed', usage: { llm_calls: 0, tool_calls: 0 }, latency_ms: 100 }, { seq: 4, at: 120 }),
    ]
    render(<TurnMetricsBar turn={buildSessionState(events).turns[0]} />)
    const item = document.querySelector('[data-metric="llm_calls"]')!
    expect(item).toHaveTextContent('LLM 调用 未取得')
    expect(item.getAttribute('title')).toContain('Trace 查询 HTTP 401')
  })
})

describe('Trace 面板展示每次大模型调用的输入与输出', () => {
  it('选中模型调用后，直接看到输入消息与输出正文', async () => {
    const events = [
      mk('llm.request', {
        model: '豆包', messages: [{ role: 'system', content: '你是客服' }, { role: 'user', content: '查一下订单' }],
      }, { step: 'llm:a', at: 1000 }),
      mk('llm.response', { content: '订单已发货', usage: { prompt_tokens: 4, completion_tokens: 1 }, latency_ms: 2000 },
        { step: 'llm:a', at: 3000 }),
    ]
    render(<QueryClientProvider client={new QueryClient()}><TraceViewer steps={buildStepTree(events)} /></QueryClientProvider>)
    await userEvent.click(screen.getByTestId('trace-row'))
    const detail = screen.getByTestId('step-detail')
    expect(within(detail).getByTestId('llm-input')).toHaveTextContent('system')
    expect(within(detail).getByTestId('llm-input')).toHaveTextContent('查一下订单')
    expect(within(detail).getByTestId('llm-output')).toHaveTextContent('订单已发货')
  })
})

describe('上游模型时间轴', () => {
  const response = (upstream: Record<string, unknown>) => mk('llm.response', {
    latency_ms: 2546, upstream,
  }, { step: 'llm1', at: 9000 })
  const request = mk('llm.request', { model: 'HiAgent' }, { step: 'llm1', at: 3000 })
  const upstream = {
    source: 'actual_completed_upstream_trace', trace_id: 'trace-1', span_id: 'span-1',
    start_time: new Date(T0 + 1000).toISOString(), end_time: new Date(T0 + 3546).toISOString(),
  }

  it('终态用原生起止时间修正本地SSE接收偏移，同时保留原始事件', () => {
    const events = [request, response(upstream)]
    const before = JSON.stringify(events)
    expect(buildStepTree(events)[0]).toMatchObject({ startMs: T0 + 1000, endMs: T0 + 3546, durationMs: 2546 })
    expect(JSON.stringify(events)).toBe(before)
  })

  it('没有权威来源或起止时间非法时保留普通本地时间轴', () => {
    for (const bad of [{ ...upstream, source: 'other' }, { ...upstream, start_time: 'bad' },
      { ...upstream, end_time: new Date(T0).toISOString() }]) {
      expect(buildStepTree([request, response(bad)])[0].startMs).toBe(T0 + 3000)
    }
  })
})

describe('本地包装步骤标明范围', () => {
  it('仅匹配有本地证据的包装span，不改真实耗时或事件名', () => {
    const events = [
      mk('span.start', { name: 'HiAgent SSE传输', attrs: { metric_scope: 'sse_transport' } }, { step: 'sse', at: 0 }),
      mk('span.end', { name: 'HiAgent SSE传输', attrs: { metric_scope: 'sse_transport' }, latency_ms: 35517 }, { step: 'sse', at: 35517 }),
      mk('span.start', { name: 'HiAgent普通发布会话', attrs: { local_session_id: 'ses_1', config_sha256: 'fixed' } }, { step: 'adapter', at: 0 }),
      mk('span.end', { name: 'HiAgent普通发布会话', latency_ms: 38014 }, { step: 'adapter', at: 38014 }),
      mk('span.start', { name: 'HiAgent SSE传输' }, { step: 'ordinary', at: 1 }),
    ]
    const nodes = buildStepTree(events)
    expect(nodes.find((n) => n.id === 'sse')).toMatchObject({ name: 'SSE接收与终态核对（本地）', durationMs: 35517 })
    expect(nodes.find((n) => n.id === 'adapter')).toMatchObject({ name: '发布API适配器（本地）', durationMs: 38014 })
    expect(nodes.find((n) => n.id === 'ordinary')?.name).toBe('HiAgent SSE传输')
    expect(events[0].payload).toMatchObject({ name: 'HiAgent SSE传输' })
  })
})
