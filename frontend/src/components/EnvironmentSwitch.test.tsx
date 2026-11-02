import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import type { Agent, VersionSummary } from '@/api/types'
import { NewSessionDialog } from '@/pages/session/NewSessionDialog'

const agent = { id: 'agt_1', name: 'bot', latest_version: { seq: 2 } } as Agent
const versions = [{ id: 'ver_2', seq: 2, tags: [] }] as unknown as VersionSummary[]

function setup(selectable: boolean) {
  vi.spyOn(api, 'get').mockResolvedValue({
    production: {}, test: {}, test_selectable: selectable,
  } as never)
  const post = vi.spyOn(api, 'post').mockResolvedValue({ session: { id: 'ses_1' } } as never)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><NewSessionDialog agent={agent} versions={versions} onClose={() => undefined} /></MemoryRouter>
  </QueryClientProvider>)
  return post
}

describe('发起时的适配器环境选择', () => {
  afterEach(() => vi.restoreAllMocks())

  it('测试未配置或与生产相同：不出现切换，请求不带 environment', async () => {
    const post = setup(false)
    await userEvent.click(await screen.findByTestId('create-session'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('environment-switch')).toBeNull()
    expect(post.mock.calls[0][1]?.body).toEqual({ agent: 'agt_1', tag: 'latest' })
  })

  it('测试已配置且不同：出现「生产 | 测试」，默认生产，选测试后请求带 environment=test', async () => {
    const post = setup(true)
    expect(await screen.findByRole('radio', { name: '生产' })).toBeChecked()
    await userEvent.click(screen.getByTestId('create-session'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(post.mock.calls[0][1]?.body).toEqual({ agent: 'agt_1', tag: 'latest' })

    await userEvent.click(screen.getByRole('radio', { name: '测试' }))
    await userEvent.click(screen.getByTestId('create-session'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2))
    expect(post.mock.calls[1][1]?.body).toEqual({ agent: 'agt_1', tag: 'latest', environment: 'test' })
  })
})
