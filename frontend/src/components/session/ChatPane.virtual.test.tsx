import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { ChatPane, VIRTUALIZE_THRESHOLD } from './ChatPane'
import { buildSessionState } from './sessionState'
import { mk, toolTurnEvents } from './testEvents'

const VIEWPORT = 600
const TURN_H = 200

// jsdom 没有布局：用 clientHeight 模拟滚动容器（600px），offsetHeight 模拟每个轮次（200px）
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => TURN_H })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => VIEWPORT })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 800 })
})
afterEach(() => {
  delete (HTMLElement.prototype as unknown as { scrollHeight?: number }).scrollHeight
})

/** 模拟浏览器：滚动位置变化后触发 scroll 事件（jsdom 不会自己触发），并让 scrollHeight 跟随虚拟容器高度 */
function setupPane() {
  const pane = screen.getByTestId('chat-pane')
  const total = () => parseInt(screen.getByTestId('chat-virtual').style.height, 10)
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, get: total })
  const fireScroll = () => act(() => void pane.dispatchEvent(new Event('scroll')))
  return { pane, total, fireScroll }
}

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>
}

/** n 轮对话：每轮用户输入为「问题 i」，最终回复「回答 i」 */
function manyTurns(n: number) {
  const events = []
  for (let i = 1; i <= n; i++) {
    const id = `trn_${i}`
    const seq = i * 10
    events.push(
      mk('turn.start', { input: { role: 'user', content: `问题 ${i}` }, history_len: i - 1 }, { seq, turn: id }),
      mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: seq + 1, turn: id, step: 's1' }),
      mk(
        'llm.response',
        {
          content: `回答 ${i}`,
          tool_calls: [],
          finish_reason: 'stop',
          usage: { prompt_tokens: 1, completion_tokens: 1 },
          latency_ms: 10,
          ttft_ms: 5,
          cost: 0,
        },
        { seq: seq + 2, turn: id, step: 's1' },
      ),
      mk(
        'turn.end',
        {
          status: 'completed',
          output: { final: { role: 'assistant', content: `回答 ${i}` }, messages: [] },
          usage: { prompt_tokens: 1, completion_tokens: 1, llm_calls: 1, tool_calls: 0 },
          cost: 0,
          latency_ms: 10,
          ttft_ms: 5,
        },
        { seq: seq + 3, turn: id },
      ),
    )
  }
  return buildSessionState(events).turns
}

describe('ChatPane 虚拟滚动', () => {
  it('轮次数不超过阈值时完整渲染，不启用虚拟滚动', () => {
    render(wrap(<ChatPane turns={manyTurns(VIRTUALIZE_THRESHOLD)} />))
    expect(screen.queryByTestId('chat-virtual')).toBeNull()
    expect(screen.getAllByTestId('turn')).toHaveLength(VIRTUALIZE_THRESHOLD)
  })

  it('几百轮长会话只渲染可视区附近的轮次', async () => {
    const turns = manyTurns(400)
    render(wrap(<ChatPane turns={turns} />))
    expect(screen.getByTestId('chat-virtual')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByTestId('turn').length).toBeGreaterThan(0))
    expect(screen.getAllByTestId('turn').length).toBeLessThan(40)
  })

  it('初始贴底：滚到底部并渲染最后一轮，且新增轮次时继续贴底', async () => {
    const { rerender } = render(wrap(<ChatPane turns={manyTurns(300)} />))
    const { pane, total, fireScroll } = setupPane()
    rerender(wrap(<ChatPane turns={manyTurns(301)} />))
    expect(pane.scrollTop).toBe(total())
    fireScroll()
    expect(await screen.findByText('问题 301')).toBeInTheDocument()
    // 滚动到底后没有「回到底部」按钮
    expect(screen.queryByRole('button', { name: '回到底部' })).toBeNull()
    rerender(wrap(<ChatPane turns={manyTurns(302)} />))
    expect(pane.scrollTop).toBe(total())
  })

  it('用户向上滚动后不再强制贴底，并出现「回到底部」', async () => {
    const { rerender } = render(wrap(<ChatPane turns={manyTurns(300)} />))
    const { pane, fireScroll } = setupPane()
    pane.scrollTop = 1000
    fireScroll()
    expect(await screen.findByRole('button', { name: '回到底部' })).toBeInTheDocument()
    rerender(wrap(<ChatPane turns={manyTurns(301)} />))
    expect(pane.scrollTop).toBe(1000)
  })

  it('流式输出：最后一轮的文本增长会实时反映，且保持贴底', async () => {
    const base = manyTurns(299)
    const stream = (text: string) =>
      buildSessionState([
        mk('turn.start', { input: { role: 'user', content: '最后的问题' }, history_len: 0 }, { seq: 6000, turn: 'trn_last' }),
        mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 6001, turn: 'trn_last', step: 's1' }),
        mk('llm.delta', { step_id: 's1', text }, { turn: 'trn_last', step: 's1' }),
      ]).turns
    const { rerender } = render(wrap(<ChatPane turns={[...base, ...stream('正在')]} />))
    const { pane, total, fireScroll } = setupPane()
    pane.scrollTop = total()
    fireScroll()
    expect(await screen.findByText(/正在/)).toBeInTheDocument()
    rerender(wrap(<ChatPane turns={[...base, ...stream('正在输出更多内容')]} />))
    expect(await screen.findByText(/正在输出更多内容/)).toBeInTheDocument()
    expect(document.querySelector('.md-streaming')).not.toBeNull()
    expect(pane.scrollTop).toBe(total())
  })

  it('虚拟滚动下仍可展开工具卡片、点击轮次选中', async () => {
    const tool = buildSessionState(toolTurnEvents('trn_t', 1)).turns
    const turns = [...manyTurns(299), ...tool]
    const picked: string[] = []
    render(wrap(<ChatPane turns={turns} onSelectTurn={(id) => picked.push(id)} />))
    const { pane, total, fireScroll } = setupPane()
    pane.scrollTop = total()
    fireScroll()
    const card = await screen.findByTestId('tool-card')
    expect(within(card).queryByText('参数')).not.toBeInTheDocument()
    await userEvent.click(within(card).getByRole('button', { name: /query_order/ }))
    expect(within(card).getByText('参数')).toBeInTheDocument()
    await userEvent.click(screen.getAllByTestId('select-turn').at(-1)!)
    expect(picked.at(-1)).toBe('trn_t')
  })
})
