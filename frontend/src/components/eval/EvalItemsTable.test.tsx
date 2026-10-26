import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EvalItemOut } from '@/api/evalTypes'
import { mockApi } from '@/test/mockApi'
import { EvalItemsTable, itemMatchesClass } from './EvalItemsTable'

afterEach(() => vi.unstubAllGlobals())

function setup(props: Partial<React.ComponentProps<typeof EvalItemsTable>> = {}) {
  const api = mockApi({ 'GET /api/v1/evals/evr_1/items': { items: [], next_cursor: null } })
  const onTagChange = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <EvalItemsTable runId="evr_1" evaluatorKeys={['exact']} repeats={3} live={{}} liveVersion={0} running={false} onOpen={() => {}} onTagChange={onTagChange} {...props} />
    </QueryClientProvider>,
  )
  return { api, onTagChange }
}
const lastQuery = (api: ReturnType<typeof mockApi>) => new URL(api.calls[api.calls.length - 1].url, 'http://x').searchParams

describe('EvalItemsTable：不稳定与人工评审筛选', () => {
  it('有「不稳定」筛选；点击后请求 class=flaky', async () => {
    const { api } = setup()
    await userEvent.click(screen.getByTestId('filter-flaky'))
    await waitFor(() => expect(lastQuery(api).get('class')).toBe('flaky'))
    expect(screen.getByTestId('filter-flaky')).toHaveAttribute('aria-pressed', 'true')
  })

  it('只有存在人工评审评估器时才有「待人工评审」筛选', async () => {
    const { api } = setup({ hasHuman: true })
    await userEvent.click(screen.getByTestId('filter-pending_human'))
    await waitFor(() => expect(lastQuery(api).get('class')).toBe('pending_human'))
  })

  it('没有人工评审评估器时不显示该筛选', () => {
    setup()
    expect(screen.queryByTestId('filter-pending_human')).not.toBeInTheDocument()
  })

  it('由外部控制筛选与标签：请求带上 class / tag，标签可清除', async () => {
    const { api, onTagChange } = setup({ cls: 'flaky', tag: 'p1' })
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0))
    const q = lastQuery(api)
    expect(q.get('class')).toBe('flaky')
    expect(q.get('tag')).toBe('p1')
    await userEvent.click(screen.getByTestId('tag-chip'))
    expect(onTagChange).toHaveBeenCalledWith('')
  })

  it('itemMatchesClass：flaky / pending_human 由服务端判定，本地不隐藏', () => {
    const it = { status: 'done', passed: true, scores: [] } as unknown as EvalItemOut
    expect(itemMatchesClass(it, 'flaky')).toBe(true)
    expect(itemMatchesClass(it, 'pending_human')).toBe(true)
    expect(itemMatchesClass(it, 'failed')).toBe(false)
    expect(itemMatchesClass({ ...it, passed: null } as EvalItemOut, 'passed')).toBe(false)
  })
})
