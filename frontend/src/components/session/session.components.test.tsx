import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { ChatPane } from './ChatPane'
import { MessageInput } from './MessageInput'
import { TraceViewer } from './TraceViewer'
import { buildSessionState, highlightKey } from './sessionState'
import { useSessionStream } from './useSessionStream'
import { mk, toolTurnEvents } from './testEvents'

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

const state = buildSessionState(toolTurnEvents())
const turn = state.turns[0]

describe('ChatPane', () => {
  it('渲染用户消息、assistant 消息、工具卡片与指标条', () => {
    render(wrap(<ChatPane turns={state.turns} />))
    expect(screen.getByTestId('user-message')).toHaveTextContent('查订单 A001')
    const msgs = screen.getAllByTestId('assistant-message')
    expect(msgs).toHaveLength(2)
    expect(msgs[1]).toHaveTextContent('订单已发货')
    const card = screen.getByTestId('tool-card')
    expect(card).toHaveAttribute('data-tool', 'query_order')
    expect(within(card).getByText('mock')).toBeInTheDocument()
    const metrics = screen.getByTestId('turn-metrics')
    expect(metrics).toHaveTextContent('LLM ×2')
    expect(metrics).toHaveTextContent('¥0.0030')
    expect(metrics.querySelector('[data-metric="latency"]')).toHaveTextContent('575ms')
    expect(metrics.querySelector('[data-metric="ttft"]')).toHaveTextContent('120ms')
  })

  it('助手回复里的图片和视频直接渲染，原文链接仍可见', () => {
    const text = '画好了 ![图](https://cdn.example.com/a.png) 视频：https://cdn.example.com/v.mp4'
    const media = buildSessionState(
      toolTurnEvents().map((e) =>
        e.type === 'llm.response' && (e.payload as { content: string | null }).content
          ? mk('llm.response', { ...(e.payload as object), content: text }, { seq: e.seq, step: e.step_id, at: 570 })
          : e,
      ),
    )
    render(wrap(<ChatPane turns={media.turns} />))
    const final = screen.getAllByTestId('assistant-message')[1]
    expect(within(final).getAllByTestId('media').map((m) => m.getAttribute('data-kind'))).toEqual(['image', 'video'])
    expect(final).toHaveTextContent('https://cdn.example.com/v.mp4')
  })

  it('工具卡片默认折叠，点击展开显示参数与结果', async () => {
    render(wrap(<ChatPane turns={state.turns} />))
    const card = screen.getByTestId('tool-card')
    expect(within(card).queryByText('参数')).not.toBeInTheDocument()
    await userEvent.click(within(card).getByRole('button', { name: /query_order/ }))
    expect(within(card).getByText('参数')).toBeInTheDocument()
    expect(within(card).getByText(/A001/)).toBeInTheDocument()
    expect(within(card).getByText(/已发货/)).toBeInTheDocument()
  })

  it('流式输出时显示光标；高亮集合标出对应步骤', () => {
    const streaming = buildSessionState([
      mk('turn.start', { input: { role: 'user', content: 'hi' }, history_len: 0 }, { seq: 1 }),
      mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1' }),
      mk('llm.delta', { step_id: 's1', text: '正在' }, { step: 's1' }),
    ])
    const { container, rerender } = render(wrap(<ChatPane turns={streaming.turns} />))
    expect(container.querySelector('.md-streaming')).not.toBeNull()
    expect(screen.getByTestId('assistant-message')).toHaveTextContent('正在')

    rerender(wrap(<ChatPane turns={state.turns} highlightSteps={new Set(['s2', highlightKey('trn_1', 's3')])} />))
    expect(screen.getByTestId('tool-card').className).toContain('ring-warning')
  })

  it('错误 / 超时 / 取消有各自明确的提示', () => {
    const mkState = (status: string, withError: boolean) =>
      buildSessionState([
        mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
        ...(withError ? [mk('error', { code: 'E_X', message: '出错了', retriable: true }, { seq: 2 })] : []),
        mk('turn.end', { status, output: null, usage: null, cost: 0, latency_ms: 1, ttft_ms: null }, { seq: 3 }),
      ])
    const { rerender } = render(wrap(<ChatPane turns={mkState('error', true).turns} />))
    expect(screen.getByTestId('turn-error')).toHaveTextContent('E_X')
    expect(screen.getByTestId('turn-error')).toHaveTextContent('出错了')
    expect(screen.getByTestId('turn-error')).toHaveTextContent('可重试')
    rerender(wrap(<ChatPane turns={mkState('timeout', true).turns} />))
    expect(screen.getByTestId('turn-timeout')).toBeInTheDocument()
    rerender(wrap(<ChatPane turns={mkState('cancelled', false).turns} />))
    expect(screen.getByTestId('turn-cancelled')).toHaveTextContent('已取消')
  })

  it('取消 / 出错的轮次显示已流出的部分文本并标注', () => {
    const mkState = (status: string) =>
      buildSessionState([
        mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
        mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1' }),
        mk('turn.end', { status, output: null, usage: null, cost: 0, latency_ms: 1, ttft_ms: null, partial_text: '说到一半' }, { seq: 3 }),
      ])
    const { rerender } = render(wrap(<ChatPane turns={mkState('cancelled').turns} />))
    expect(screen.getByTestId('assistant-message')).toHaveTextContent('说到一半')
    expect(screen.getByTestId('partial-mark')).toHaveTextContent('已取消')
    rerender(wrap(<ChatPane turns={mkState('error').turns} />))
    expect(screen.getByTestId('assistant-message')).toHaveTextContent('说到一半')
    expect(screen.getByTestId('partial-mark')).toHaveTextContent('执行中断')
  })

  it('点击“查看 Trace”触发轮次选择', async () => {
    const onSelectTurn = vi.fn()
    render(wrap(<ChatPane turns={state.turns} onSelectTurn={onSelectTurn} />))
    await userEvent.click(screen.getByTestId('select-turn'))
    expect(onSelectTurn).toHaveBeenCalledWith('trn_1')
  })

  it('被转存为 blob 的工具结果：点击后加载完整内容', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ call_id: 'c1', name: 'big', output: { rows: [1, 2, 3] }, is_error: false }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const s = buildSessionState([
      mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
      mk('tool.call', { call_id: 'c1', name: 'big', arguments: {} }, { seq: 2, step: 's2' }),
      mk('tool.result', { $blob: 'b'.repeat(64), call_id: 'c1', name: 'big', is_error: false, latency_ms: 1, mocked: false }, { seq: 3, step: 's2' }),
    ])
    render(wrap(<ChatPane turns={s.turns} sessionId="ses_1" />))
    await userEvent.click(screen.getByRole('button', { name: /big/ }))
    await userEvent.click(screen.getByTestId('load-blob'))
    await waitFor(() => expect(screen.getByText(/rows/)).toBeInTheDocument())
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/blobs/${'b'.repeat(64)}?session_id=ses_1`)
    vi.unstubAllGlobals()
  })
})

describe('ChatPane：边界情况', () => {
  it('用户输入被转存为 blob：显示加载按钮，加载后显示内容', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ input: { role: 'user', content: '超长的用户输入' }, history_len: 0 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const s = buildSessionState([mk('turn.start', { $blob: 'e'.repeat(64), history_len: 0 }, { seq: 1 })])
    render(wrap(<ChatPane turns={s.turns} sessionId="ses_1" />))
    expect(screen.getByTestId('load-blob')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('load-blob'))
    await waitFor(() => expect(screen.getByTestId('user-message')).toHaveTextContent('超长的用户输入'))
    vi.unstubAllGlobals()
  })

  it('模型调用失败的步骤没有内容，不画空的 assistant 气泡', () => {
    const s = buildSessionState([
      mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
      mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1' }),
      mk('error', { code: 'E', message: 'boom', retriable: false }, { seq: 3 }),
      mk('turn.end', { status: 'error', output: null, usage: null, cost: 0, latency_ms: 1, ttft_ms: null }, { seq: 4 }),
    ])
    render(wrap(<ChatPane turns={s.turns} />))
    expect(screen.queryAllByTestId('assistant-message')).toHaveLength(0)
    expect(screen.getByTestId('turn-error')).toBeInTheDocument()
  })

  it('没有轮次：加载中 / 空状态', () => {
    const { rerender } = render(wrap(<ChatPane turns={[]} loading />))
    expect(screen.getByText('加载会话…')).toBeInTheDocument()
    rerender(wrap(<ChatPane turns={[]} />))
    expect(screen.getByText(/还没有消息/)).toBeInTheDocument()
  })
})

describe('MessageInput', () => {
  it('Enter 发送并清空，Shift+Enter 换行', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined)
    render(<MessageInput onSend={onSend} />)
    const box = screen.getByLabelText('消息输入')
    await userEvent.type(box, '你好{Shift>}{Enter}{/Shift}第二行')
    expect(onSend).not.toHaveBeenCalled()
    expect(box).toHaveValue('你好\n第二行')
    await userEvent.type(box, '{Enter}')
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1))
    expect(onSend).toHaveBeenCalledWith('你好\n第二行', {})
    await waitFor(() => expect(box).toHaveValue(''))
  })

  it('输入法组词期间的 Enter 不发送；空白内容不发送', () => {
    const onSend = vi.fn()
    render(<MessageInput onSend={onSend} />)
    const box = screen.getByLabelText('消息输入')
    fireEvent.change(box, { target: { value: '拼音' } })
    fireEvent.keyDown(box, { key: 'Enter', isComposing: true, keyCode: 229 })
    expect(onSend).not.toHaveBeenCalled()
    fireEvent.change(box, { target: { value: '   ' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('发送失败时保留输入内容', async () => {
    const onSend = vi.fn().mockRejectedValue(new Error('busy'))
    render(<MessageInput onSend={onSend} />)
    const box = screen.getByLabelText('消息输入')
    await userEvent.type(box, 'abc{Enter}')
    await waitFor(() => expect(onSend).toHaveBeenCalled())
    expect(box).toHaveValue('abc')
  })

  it('运行中显示停止按钮，点击调用 onStop，且不能发送', async () => {
    const onSend = vi.fn()
    const onStop = vi.fn()
    render(<MessageInput onSend={onSend} onStop={onStop} running />)
    expect(screen.queryByTestId('send-button')).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('消息输入'), 'x{Enter}')
    expect(onSend).not.toHaveBeenCalled()
    await userEvent.click(screen.getByTestId('stop-button'))
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('展开“本轮变量”后，变量随消息一起发送', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined)
    render(<MessageInput onSend={onSend} />)
    await userEvent.click(screen.getByRole('button', { name: /本轮变量/ }))
    await userEvent.click(screen.getByRole('button', { name: '添加变量' }))
    await userEvent.type(screen.getByLabelText('键'), 'lang')
    await userEvent.type(screen.getByLabelText('值'), 'en')
    await userEvent.type(screen.getByLabelText('消息输入'), 'hi{Enter}')
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('hi', { variables: { lang: 'en' } }))
  })
})

describe('TraceViewer', () => {
  it('渲染树与瀑布图；点击步骤显示详情，可切换请求/响应', async () => {
    render(wrap(<TraceViewer steps={turn.steps} turnId={turn.id} startedAt={turn.startedAt} endedAt={turn.endedAt} />))
    const rows = screen.getAllByTestId('trace-row')
    expect(rows.map((r) => r.getAttribute('data-step'))).toEqual(['s1', 's2', 's3', null])
    expect(screen.getAllByTestId('waterfall-bar')).toHaveLength(4)
    await userEvent.click(rows[0])
    const detail = screen.getByTestId('step-detail')
    expect(within(detail).getByRole('tab', { name: '请求' })).toBeInTheDocument()
    expect(within(detail).getAllByText(/fake-model/).length).toBeGreaterThan(0)
    await userEvent.click(within(detail).getByRole('tab', { name: '响应' }))
    expect(screen.getByTestId('step-payload')).toHaveAttribute('data-event', 'llm.response')
    expect(within(detail).getByText('tokens')).toBeInTheDocument()
  })

  it('按类型筛选与文本搜索', async () => {
    render(wrap(<TraceViewer steps={turn.steps} />))
    await userEvent.click(screen.getByTestId('filter-tool'))
    expect(screen.getAllByTestId('trace-row').map((r) => r.getAttribute('data-kind'))).toEqual(['llm', 'tool']) // 祖先保留
    await userEvent.click(screen.getByTestId('filter-tool')) // 取消
    await userEvent.type(screen.getByLabelText('搜索 Trace'), '订单已发货')
    const kinds = screen.getAllByTestId('trace-row').map((r) => r.getAttribute('data-kind'))
    expect(kinds).toContain('llm')
    expect(kinds).not.toContain('tool')
    await userEvent.clear(screen.getByLabelText('搜索 Trace'))
    await userEvent.type(screen.getByLabelText('搜索 Trace'), '没有这个')
    expect(screen.getByText('没有匹配的步骤')).toBeInTheDocument()
  })

  it('折叠子节点', async () => {
    render(wrap(<TraceViewer steps={turn.steps} />))
    await userEvent.click(screen.getAllByRole('button', { name: '折叠' })[0])
    expect(screen.getAllByTestId('trace-row').map((r) => r.getAttribute('data-step'))).toEqual(['s1', 's3', null])
  })

  it('payload 被转存为 blob 时，点击后通过 GET /blobs/{hash} 加载', async () => {
    const full = { provider: 'fake', model: 'fake-model', params: {}, messages: [{ role: 'user', content: '很长的内容' }], tools: [] }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(full), { status: 200, headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const hash = 'c'.repeat(64)
    const s = buildSessionState([
      mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
      mk('llm.request', { $blob: hash, provider: 'fake', model: 'fake-model' }, { seq: 2, step: 's1' }),
    ])
    render(wrap(<TraceViewer steps={s.turns[0].steps} sessionId="ses_1" />))
    await userEvent.click(screen.getByTestId('trace-row'))
    expect(screen.queryByText(/messages/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByTestId('load-blob'))
    await waitFor(() => expect(screen.getByText(/messages/)).toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/blobs/${hash}?session_id=ses_1`)
    vi.unstubAllGlobals()
  })

  it('没有步骤时显示空状态', () => {
    render(wrap(<TraceViewer steps={[]} />))
    expect(screen.getByText('暂无 Trace')).toBeInTheDocument()
  })
})

class MockEventSource {
  static instances: MockEventSource[] = []
  closed = false
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  private listeners = new Map<string, Array<(m: MessageEvent) => void>>()
  constructor(public url: string) {
    MockEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: (m: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn])
  }
  close() {
    this.closed = true
  }
  emit(type: string, data: unknown) {
    const m = new MessageEvent(type, { data: JSON.stringify(data) })
    this.listeners.get(type)?.forEach((fn) => fn(m))
  }
}

describe('useSessionStream', () => {
  beforeEach(() => {
    MockEventSource.instances = []
    vi.stubGlobal('EventSource', MockEventSource)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('回放历史 -> idle 后关闭；发送新一轮后 reopen 从最大 seq 继续；重复事件被忽略', () => {
    const { result } = renderHook(() => useSessionStream('ses_1'))
    const es = MockEventSource.instances[0]
    expect(es.url).toBe('/api/v1/sessions/ses_1/stream?after_seq=0')
    expect(result.current.loading).toBe(true)
    const evs = toolTurnEvents('trn_1', 1)
    act(() => {
      for (const e of evs) es.emit(e.type, e)
      es.emit('turn.end', evs[evs.length - 1]) // 重复
    })
    expect(result.current.turns).toHaveLength(1)
    expect(result.current.running).toBe(false)
    act(() => es.emit('idle', { status: 'idle', last_seq: 9 }))
    expect(result.current.status).toBe('idle')
    expect(es.closed).toBe(true)
    expect(result.current.loading).toBe(false)

    act(() => result.current.reopen())
    const es2 = MockEventSource.instances[1]
    expect(es2.url).toBe('/api/v1/sessions/ses_1/stream?after_seq=9')
    const second = toolTurnEvents('trn_2', 10)
    act(() => {
      es2.emit('turn.start', second[0])
    })
    expect(result.current.running).toBe(true)
    expect(result.current.runningTurn?.id).toBe('trn_2')
  })

  it('llm.delta 没有 seq，直接合并到当前消息', () => {
    const { result } = renderHook(() => useSessionStream('ses_1'))
    const es = MockEventSource.instances[0]
    act(() => {
      es.emit('turn.start', mk('turn.start', { input: { role: 'user', content: 'hi' }, history_len: 0 }, { seq: 1 }))
      es.emit('llm.request', mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1' }))
      es.emit('llm.delta', mk('llm.delta', { step_id: 's1', text: 'A' }, { step: 's1' }))
      es.emit('llm.delta', mk('llm.delta', { step_id: 's1', text: 'B' }, { step: 's1' }))
    })
    expect(result.current.turns[0].messages[0].content).toBe('AB')
    expect(result.current.turns[0].messages[0].streaming).toBe(true)
  })

  it('sessionId 为空时不连接', () => {
    renderHook(() => useSessionStream(undefined))
    expect(MockEventSource.instances).toHaveLength(0)
  })
})
