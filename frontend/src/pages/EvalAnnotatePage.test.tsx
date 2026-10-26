import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '@/test/mockApi'
import { routes } from '@/routes'
import EvalAnnotatePage from './EvalAnnotatePage'

vi.mock('@/components/eval/ItemDetail', () => ({ ItemConversation: () => null }))

afterEach(() => vi.unstubAllGlobals())

function renderAt(path: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/evals/:id/annotate" element={<EvalAnnotatePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('EvalAnnotatePage', () => {
  it('路由 /evals/:id/annotate 已注册', () => {
    const evals = routes[0].children?.find((r) => r.path === 'evals')
    const one = evals?.children?.find((r) => r.path === ':id')
    expect(one?.children?.some((r) => r.path === 'annotate')).toBe(true)
  })

  it('评测不存在时给出提示与返回链接', async () => {
    mockApi({})
    renderAt('/evals/evr_x/annotate')
    expect(await screen.findByText(/评测不存在/)).toBeInTheDocument()
  })

  it('加载评测后渲染评审界面', async () => {
    mockApi({
      'GET /api/v1/evals/evr_1': {
        id: 'evr_1',
        batch_id: 'b',
        name: 'n',
        agent_id: 'a',
        evaluators: [{ key: 'human', type: 'human', scale: [1, 5] }],
        repeats: 1,
        status: 'completed',
        progress: { total: 0, done: 0, failed: 0 },
        stats: null,
        batch_runs: [],
      },
      'GET /api/v1/evals/evr_1/items': { items: [], next_cursor: null },
    })
    renderAt('/evals/evr_1/annotate')
    expect(await screen.findByTestId('annotate-view')).toBeInTheDocument()
    expect(await screen.findByTestId('annotate-empty')).toBeInTheDocument()
  })
})
