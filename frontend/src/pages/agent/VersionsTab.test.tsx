import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Agent, VersionSummary } from '@/api/types'
import { mockApi } from '@/test/mockApi'
import { VersionsTab } from './VersionsTab'

afterEach(() => vi.unstubAllGlobals())

const agent = { id: 'agt_1', name: 'bot' } as Agent
const versions = [1, 2].map((seq) => ({
  id: `ver_${seq}`, seq, tags: [], spec_hash: 'h', notes: null, created_via: 'ui', created_at: '2026-11-01T00:00:00Z',
})) as unknown as VersionSummary[]

function setup(testSelectable: boolean) {
  const api = mockApi({
    'GET /api/v1/adapter-environments': { production: {}, test: {}, test_selectable: testSelectable },
    'POST /api/v1/sessions': { session: { id: 'ses_1' } },
    'POST /api/v1/compare': { id: 'cmp_1' },
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <VersionsTab agent={agent} versions={versions} hasMore={false} loadingMore={false} onLoadMore={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return api
}

describe('版本列表', () => {
  it('不再有按版本绑定接入的入口', () => {
    setup(false)
    expect(screen.queryByText('调用接入')).toBeNull()
    expect(screen.queryByText('批量修改接入')).toBeNull()
  })

  it('对话与双栏对话默认用生产；选「测试」后请求带 environment=test', async () => {
    const api = setup(true)
    await userEvent.click(screen.getByTestId('chat-v2'))
    await waitFor(() => expect(api.find('POST', /\/sessions$/)).toHaveLength(1))
    expect(api.find('POST', /\/sessions$/)[0].body).toEqual({ version_id: 'ver_2' })

    await userEvent.click(await screen.findByRole('radio', { name: '测试' }))
    await userEvent.click(screen.getByLabelText('选择 v1'))
    await userEvent.click(screen.getByLabelText('选择 v2'))
    await userEvent.click(screen.getByTestId('dual-chat'))
    await waitFor(() => expect(api.find('POST', /\/compare$/)).toHaveLength(1))
    expect(api.find('POST', /\/compare$/)[0].body).toMatchObject({ mode: 'live', environment: 'test' })
  })
})
