import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPane, type ChatPaneHandle } from './ChatPane'
import { buildSessionState } from './sessionState'
import { mk, toolTurnEvents } from './testEvents'

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>
}

/** 两轮已完成的对话：第 1 轮是带工具调用的完整一轮，第 2 轮输入为「第二个问题」 */
function twoTurns() {
  const second = toolTurnEvents('trn_2', 20).map((e) => (e.type === 'turn.start' ? mk('turn.start', { input: { role: 'user', content: '第二个问题' }, history_len: 1 }, { seq: e.seq, turn: 'trn_2' }) : e))
  return buildSessionState([...toolTurnEvents('trn_1', 1), ...second]).turns
}

/** 第 3 轮：只有 turn.start，仍在流式进行中 */
function runningTurn() {
  return buildSessionState([mk('turn.start', { input: { role: 'user', content: '进行中的问题' }, history_len: 2 }, { seq: 40, turn: 'trn_3' })]).turns
}

afterEach(() => vi.restoreAllMocks())

describe('ChatPane 按轮折叠', () => {
  it('每轮顶部有标题行：第 N 轮、输入摘要、状态与六项统计', () => {
    render(wrap(<ChatPane turns={twoTurns()} />))
    const headers = screen.getAllByTestId('turn-header')
    expect(headers).toHaveLength(2)
    expect(headers[0]).toHaveTextContent('第 1 轮')
    expect(within(headers[0]).getByTestId('turn-summary')).toHaveTextContent('查订单 A001')
    expect(within(headers[0]).getByTestId('turn-status')).toHaveTextContent('已完成')
    expect(headers[1]).toHaveTextContent('第 2 轮')
    expect(within(headers[1]).getByTestId('turn-summary')).toHaveTextContent('第二个问题')
    const stats = within(headers[0]).getByTestId('turn-header-metrics')
    expect(stats.querySelector('[data-metric="tokens"]')).toHaveTextContent(/60.*13/) // 输入 60 / 输出 13
    expect(stats.querySelector('[data-metric="latency"]')).toHaveTextContent('575ms')
    expect(stats.querySelector('[data-metric="ttft"]')).toHaveTextContent('120ms')
    expect(stats.querySelector('[data-metric="cost"]')).toHaveTextContent('¥0.0030')
    expect(stats.querySelector('[data-metric="llm_calls"]')).toHaveTextContent('LLM ×2')
    expect(stats.querySelector('[data-metric="tool_calls"]')).toHaveTextContent('×1')
    // 展开时底部的指标条仍保留
    expect(screen.getAllByTestId('turn-metrics')).toHaveLength(2)
  })

  it('点击标题行折叠 / 展开该轮：折叠后只剩标题行，且不会切换 Trace 选中的轮次', async () => {
    const picked: string[] = []
    render(wrap(<ChatPane turns={twoTurns()} onSelectTurn={(id) => picked.push(id)} />))
    const [first, second] = screen.getAllByTestId('turn')
    await userEvent.click(within(first).getByTestId('turn-header'))
    expect(first).toHaveAttribute('data-collapsed')
    expect(within(first).getByRole('button', { name: /第 1 轮/ })).toHaveAttribute('aria-expanded', 'false')
    expect(within(first).queryByTestId('user-message')).toBeNull()
    expect(within(first).queryByTestId('assistant-message')).toBeNull()
    expect(within(first).queryByTestId('turn-metrics')).toBeNull()
    expect(within(first).getByTestId('turn-header')).toBeInTheDocument()
    expect(within(second).getByTestId('user-message')).toBeInTheDocument() // 其它轮不受影响
    expect(picked).toEqual([])
    await userEvent.click(within(first).getByTestId('turn-header'))
    expect(first).not.toHaveAttribute('data-collapsed')
    expect(within(first).getByTestId('user-message')).toBeInTheDocument()
  })

  it('「全部折叠」「全部展开」', async () => {
    render(wrap(<ChatPane turns={twoTurns()} />))
    await userEvent.click(screen.getByRole('button', { name: '全部折叠' }))
    expect(screen.queryByTestId('user-message')).toBeNull()
    expect(screen.getAllByTestId('turn-header')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: '全部展开' }))
    expect(screen.getAllByTestId('user-message')).toHaveLength(2)
  })

  it('流式进行中的新一轮默认展开，即使之前已全部折叠', async () => {
    const done = twoTurns()
    const { rerender } = render(wrap(<ChatPane turns={done} />))
    await userEvent.click(screen.getByRole('button', { name: '全部折叠' }))
    rerender(wrap(<ChatPane turns={[...done, ...runningTurn()]} />))
    const running = screen.getAllByTestId('turn')[2]
    expect(running).toHaveAttribute('data-status', 'running')
    expect(running).not.toHaveAttribute('data-collapsed')
    expect(within(running).getByTestId('user-message')).toHaveTextContent('进行中的问题')
    expect(within(running).getByTestId('turn-status')).toHaveTextContent('运行中')
    expect(screen.getAllByTestId('user-message')).toHaveLength(1)
  })

  it('scrollToTurn：已折叠的轮次先展开，并把容器滚到该轮顶部', async () => {
    const ref = createRef<ChatPaneHandle>()
    render(wrap(<ChatPane ref={ref} turns={twoTurns()} />))
    const pane = screen.getByTestId('chat-pane')
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const top = this === pane ? 100 : this.getAttribute('data-turn-id') === 'trn_2' ? 700 : 0
      return { top } as DOMRect
    })
    await userEvent.click(within(screen.getAllByTestId('turn')[1]).getByTestId('turn-header'))
    expect(screen.getAllByTestId('turn')[1]).toHaveAttribute('data-collapsed')
    pane.scrollTop = 50
    act(() => ref.current!.scrollToTurn('trn_2'))
    expect(screen.getAllByTestId('turn')[1]).not.toHaveAttribute('data-collapsed')
    expect(pane.scrollTop).toBe(700 - 100 + 50)
  })
})
