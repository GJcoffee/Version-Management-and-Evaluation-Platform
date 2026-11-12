import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { mockApi } from '@/test/mockApi'
import { EvalRunList } from './EvalRunList'

afterEach(() => vi.unstubAllGlobals())

const run = (id: string) => ({
  id, name: id, agent_id: 'a', version_id: id, version_seq: 1,
  status: 'completed', dataset_name: 'ds', dataset_rev: 1, stats: null,
  created_at: '2026-11-11T00:00:00Z',
})

it('归档筛选切换会清除隐藏评测的选择，不能带入对比', async () => {
  const api = mockApi({
    'GET /api/v1/evals': (c: { url: string }) => ({
      items: c.url.includes('include_archived=true') ? [run('visible'), run('archived')] : [run('visible')],
      next_cursor: null,
    }),
  })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><EvalRunList /></MemoryRouter>
  </QueryClientProvider>)
  await screen.findByTestId('eval-row-visible')
  expect(screen.queryByTestId('eval-row-archived')).toBeNull()
  expect(api.find('GET', /evals\?/)[0].url).toContain('include_archived=false')
  await userEvent.click(screen.getByLabelText('包括已归档评测'))
  await screen.findByTestId('eval-row-archived')
  await userEvent.click(screen.getByLabelText('选择 visible'))
  await userEvent.click(screen.getByLabelText('选择 archived'))
  expect(screen.getByTestId('compare-selected')).toBeEnabled()
  await userEvent.click(screen.getByLabelText('包括已归档评测'))
  await waitFor(() => expect(screen.queryByTestId('eval-row-archived')).toBeNull())
  expect(screen.getByTestId('compare-selected')).toBeDisabled()
  expect(screen.getByLabelText('选择 visible')).not.toBeChecked()
})
