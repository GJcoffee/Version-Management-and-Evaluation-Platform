import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { buildSessionState, totalMetrics } from './sessionState'
import { buildStepTree } from './traceTree'
import { TurnMetricsBar, turnElapsedMs } from './TurnMetricsBar'
import { TraceViewer } from './TraceViewer'
import { turnTiming, turnTtftMs } from './turnTiming'
import { remoteBreakdown, remoteTitle, remoteValueText } from './upstreamTiming'
import { mk, T0 } from './testEvents'

/**
 * HiAgent 轮次：规范指标以远端为主。本轮 SSE 阶段结束（turn.end）时 usage.trace_status 为 pending，
 * 用时为空；后台补齐的 metrics 事件到达后改写为 Trace 的运行时长。本地测量不展示。
 */
const start = mk('turn.start', { input: { role: 'user', content: '查订单' }, history_len: 0 }, { seq: 1, at: 0 })
const answer = mk('message.assistant', { message: { role: 'assistant', content: '已发货' } }, { seq: 2, at: 300 })
const sse = mk('metrics', { scope: 'turn', source: 'hiagent_sse', usage: { prompt_tokens: 9, completion_tokens: 3 }, transport_ms: 21_000 },
  { seq: 3, at: 301 })
const end = (usage: Record<string, unknown> = { trace_status: 'pending', llm_calls: 0, tool_calls: 1, prompt_tokens: 9, completion_tokens: 3 }) =>
  mk('turn.end', { status: 'completed', output: null, usage, cost: 0, latency_ms: null, ttft_ms: null }, { seq: 4, at: 310 })
const complete = mk('metrics', {
  scope: 'turn', source: 'hiagent_trace', usage: { prompt_tokens: 9, completion_tokens: 3 }, llm_calls: 2,
  llm_api_ms: 15_000, tool_execution_ms: 1_000, upstream_run_ms: 20_000, trace_status: 'complete',
}, { seq: 5, at: 400 })
const unavailable = (note: string) => mk('metrics', { scope: 'turn', trace_status: 'unavailable', trace_note: note }, { seq: 5, at: 400 })
const turnOf = (...events: ReturnType<typeof mk>[]) => buildSessionState(events).turns[0]
const metric = (name: string) => document.querySelector(`[data-metric="${name}"]`)

describe('HiAgent 轮次：补齐前显示同步中，不用本地值顶替', () => {
  it('turn.end 时用时为空、状态 pending；指标条显示同步中，不显示首 token 与成本', () => {
    const turn = turnOf(start, answer, sse, end())
    expect(turn.trace).toEqual({ status: 'pending', note: null })
    expect(turn.metrics.latencyMs).toBeNull()
    expect(turnTiming(turn, T0 + 60_000).durationMs).toBeNull() // 不会退回本地时间差
    render(<TurnMetricsBar turn={turn} />)
    expect(metric('latency')).toHaveTextContent('HiAgent 用时 同步中')
    expect(metric('llm_calls')).toHaveTextContent('LLM 同步中')
    expect(metric('tokens')).toHaveTextContent('9')
    for (const gone of ['ttft', 'cost', 'sse_time', 'trace_fetch_time', 'query_time']) expect(metric(gone)).toBeNull()
    expect(screen.queryByText(/本地/)).toBeNull()
  })

  it('补齐事件到达后改写为远端用时、调用次数，并给出远端 LLM / 工具累计', () => {
    const turn = turnOf(start, answer, sse, end(), complete)
    expect(turn.trace).toEqual({ status: 'complete', note: null })
    expect(turn.metrics).toMatchObject({ latencyMs: 20_000, ttftMs: null, llmCalls: 2, promptTokens: 9, completionTokens: 3 })
    render(<TurnMetricsBar turn={turn} />)
    expect(metric('latency')).toHaveTextContent('HiAgent 用时 20.000s')
    expect(metric('llm_calls')).toHaveTextContent('LLM ×2')
    expect(metric('llm_time')).toHaveTextContent('LLM累计 15.000s')
    expect(metric('tool_time')).toHaveTextContent('工具累计 1.000s')
    expect(metric('ttft')).toBeNull()
    expect(metric('cost')).toBeNull()
    expect(screen.queryByText(/SSE|本地|补齐 /)).toBeNull() // 传输、SSE、Trace 补齐耗时只是诊断，界面不显示
  })

  it('未取得：用时与 LLM 调用显示未取得，悬停给出原因', () => {
    const turn = turnOf(start, answer, sse, end(), unavailable('Trace 查询 HTTP 401'))
    expect(turn.trace).toEqual({ status: 'unavailable', note: 'Trace 查询 HTTP 401' })
    render(<TurnMetricsBar turn={turn} />)
    expect(metric('latency')).toHaveTextContent('HiAgent 用时 未取得')
    expect(metric('latency')!.getAttribute('title')).toContain('Trace 查询 HTTP 401')
    expect(metric('llm_calls')).toHaveTextContent('LLM 调用 未取得')
    expect(metric('llm_calls')!.getAttribute('title')).toContain('Trace 查询 HTTP 401')
  })

  it('部分缺失仍用远端用时，原因在悬停里', () => {
    const partial = mk('metrics', { scope: 'turn', source: 'hiagent_trace', upstream_run_ms: 5000, trace_status: 'partial',
      trace_note: '1 个 span 未通过核验已跳过', llm_calls: 1 }, { seq: 5, at: 400 })
    const turn = turnOf(start, answer, sse, end(), partial)
    render(<TurnMetricsBar turn={turn} />)
    expect(metric('latency')).toHaveTextContent('HiAgent 用时 5.000s')
    expect(metric('latency')!.getAttribute('title')).toContain('1 个 span 未通过核验已跳过')
  })

  it('旧数据（Trace 指标先于 turn.end，turn.end 里是本地用时）也以远端为准', () => {
    const old = mk('turn.end', { status: 'completed', output: null, usage: { trace_status: 'complete', llm_calls: 2, tool_calls: 1 },
      cost: 0, latency_ms: 31_000, ttft_ms: 7000 }, { seq: 6, at: 500 })
    const turn = turnOf(start, answer, complete, old)
    expect(turn.metrics.latencyMs).toBe(20_000)
    expect(turn.metrics.ttftMs).toBeNull()
    expect(turnElapsedMs(turn, T0)).toBe(20_000)
  })

  it('HiAgent 没有返回整轮 token 时显示未提供，而不是 0', () => {
    const noTokens = mk('metrics', { scope: 'turn', source: 'hiagent_sse', usage: {} }, { seq: 3, at: 301 })
    render(<TurnMetricsBar turn={turnOf(start, answer, noTokens, end({ trace_status: 'pending' }))} />)
    expect(metric('tokens')).toHaveTextContent('未提供')
  })

  it('没有远端数据的 agent 保持原有指标：用时、首 token、成本', () => {
    const turn = turnOf(start, answer, mk('turn.end', { status: 'completed', output: null, usage: { llm_calls: 1, tool_calls: 0 },
      cost: 0.002, latency_ms: 575, ttft_ms: 120 }, { seq: 4, at: 575 }))
    expect(turn.trace).toBeNull()
    render(<TurnMetricsBar turn={turn} />)
    expect(metric('latency')).toHaveTextContent('575ms')
    expect(metric('ttft')).toHaveTextContent('120ms')
    expect(metric('cost')).toBeInTheDocument()
    expect(turnTtftMs(turn)).toBe(120)
  })
})

describe('会话合计与 Trace 面板的远端口径', () => {
  it('汇总标出有远端数据、补齐中与未取得', () => {
    const synced = turnOf(start, answer, sse, end(), complete)
    const syncing = turnOf(start, answer, sse, end())
    const missing = turnOf(start, answer, sse, end(), unavailable('x'))
    expect(totalMetrics([synced])).toMatchObject({ remote: true, syncing: false, missing: false, latencyMs: 20_000 })
    expect(totalMetrics([synced, syncing])).toMatchObject({ remote: true, syncing: true, latencyMs: 20_000 })
    expect(totalMetrics([synced, missing])).toMatchObject({ remote: true, missing: true, latencyMs: 20_000 })
    const local = turnOf(start, mk('turn.end', { status: 'completed', output: null, usage: {}, cost: 0, latency_ms: 100, ttft_ms: 10 },
      { seq: 4, at: 100 }))
    expect(totalMetrics([local])).toMatchObject({ remote: false, syncing: false, missing: false, latencyMs: 100 })
  })

  it('Trace 头部显示远端用时、同步中或未取得，不显示本地用时', () => {
    const synced = turnOf(start, answer, sse, end(), complete)
    const { rerender } = render(<TraceViewer steps={synced.steps} durationMs={synced.metrics.latencyMs} trace={synced.trace} />)
    expect(screen.getByTestId('trace-duration')).toHaveTextContent('HiAgent 用时 20.000s')
    expect(screen.getByTestId('trace-duration')).not.toHaveTextContent('本地')
    const syncing = turnOf(start, answer, sse, end())
    rerender(<TraceViewer steps={syncing.steps} durationMs={null} trace={syncing.trace} />)
    expect(screen.getByTestId('trace-duration')).toHaveTextContent('HiAgent 用时 同步中')
    rerender(<TraceViewer steps={syncing.steps} durationMs={null} trace={{ status: 'unavailable', note: '凭据未提供' }} />)
    expect(screen.getByTestId('trace-duration')).toHaveTextContent('HiAgent 用时 未取得')
    expect(screen.getByTestId('trace-duration').getAttribute('title')).toContain('凭据未提供')
  })

  it('远端明细只取 Trace 汇总里的 LLM / 工具耗时', () => {
    expect(remoteBreakdown([sse, complete])).toEqual({ llmMs: 15_000, toolMs: 1_000 })
    expect(remoteBreakdown([sse])).toBeNull()
    expect(remoteValueText({ status: 'pending', note: null }, null)).toBe('同步中')
    expect(remoteValueText({ status: 'complete', note: null }, null)).toBe('未取得')
    expect(remoteTitle({ status: 'pending', note: null }, 'HiAgent 用时')).toContain('后台补齐')
  })

  it('上游输出阶段的接收时间差不冒充上游耗时', () => {
    const events = [
      mk('span.start', { name: '上游输出阶段', attrs: { source: 'hiagent_sse', upstream_id: 'seg-1' } }, { step: 'stage:1', at: 0 }),
      mk('span.end', { name: '上游输出阶段', attrs: { upstream_id: 'seg-1' } }, { step: 'stage:1', at: 4000 }),
    ]
    expect(buildStepTree(events)[0].durationMs).toBeNull()
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
