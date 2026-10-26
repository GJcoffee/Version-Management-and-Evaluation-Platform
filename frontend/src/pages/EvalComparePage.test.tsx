import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '@/test/mockApi'
import EvalComparePage from './EvalComparePage'

vi.mock('@/components/eval/CompareCaseDrawer', () => ({ CompareCaseDrawer: () => null }))

afterEach(() => vi.unstubAllGlobals())

const runOut = (id: string, seq: number) => ({
  id,
  batch_id: 'b',
  name: `v${seq}`,
  agent_id: 'a',
  agent_name: 'bot',
  version_id: `ver_${seq}`,
  version_seq: seq,
  dataset_name: 'ds',
  dataset_rev: 1,
  status: 'completed',
  created_at: '2026-10-25T00:00:00Z',
  stats: { pass_rate: 0.5, cost: 0.1 },
})
const side = (id: string, seq: number) => ({ id, version_seq: seq, stats: null })

const compare = {
  left: side('evr_a', 1),
  right: side('evr_b', 2),
  warnings: [],
  summary: {
    improved: 1,
    regressed: 2,
    unchanged: 5,
    new: 0,
    missing: 0,
    unscored: 0,
    flaky: 1,
    paired: 8,
    mean_delta: -0.1,
    ci95: [-0.3, 0.05],
    wins: 1,
    ties: 5,
    losses: 2,
    sign_test_p: 1,
    metric_deltas: { pass_rate: -0.1 },
  },
  cases: {
    items: [
      { case_key: 'c001', class: 'regressed', flaky: true, delta: -0.5, tags: ['p0'], left: { score: 1, pass_rate: 1, item_ids: [] }, right: { score: 0.5, pass_rate: 0.5, item_ids: [] } },
      { case_key: 'c002', class: 'regressed', flaky: false, delta: -0.4, tags: [], left: { score: 1, pass_rate: 1, item_ids: [] }, right: { score: 0.6, pass_rate: 0.6, item_ids: [] } },
    ],
    next_cursor: null,
  },
}

function setup() {
  const api = mockApi({
    'GET /api/v1/evals': { items: [runOut('evr_a', 1), runOut('evr_b', 2)], next_cursor: null },
    'GET /api/v1/evals/evr_a': { ...runOut('evr_a', 1), batch_runs: [] },
    'GET /api/v1/evals/evr_b': { ...runOut('evr_b', 2), batch_runs: [] },
    'GET /api/v1/evals/compare': compare,
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/evals/compare?left=evr_a&right=evr_b']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <EvalComparePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return api
}

const metric = (left: number | null, right: number | null) => ({ left, right, delta: left === null || right === null ? null : right - left })

describe('EvalComparePage：按工具指标变化', () => {
  it('每个工具显示左 → 右与变化量，延迟、失败率变差标为 bad', async () => {
    mockApi({
      'GET /api/v1/evals': { items: [runOut('evr_a', 1), runOut('evr_b', 2)], next_cursor: null },
      'GET /api/v1/evals/evr_a': { ...runOut('evr_a', 1), batch_runs: [] },
      'GET /api/v1/evals/evr_b': { ...runOut('evr_b', 2), batch_runs: [] },
      'GET /api/v1/evals/compare': {
        ...compare,
        summary: {
          ...compare.summary,
          metrics: {
            tools: {
              draw_image: {
                calls: metric(10, 14),
                calls_per_case: metric(1, 1.4),
                latency_ms: { p50: metric(5000, 8000), p95: metric(9000, 8000) },
                error_rate: metric(0, 0.1),
              },
              search: {
                calls: metric(0, 3),
                calls_per_case: metric(0, 0.3),
                latency_ms: { p50: metric(null, 200), p95: metric(null, 300) },
                error_rate: metric(null, 0),
              },
            },
          },
        },
      },
    })
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/evals/compare?left=evr_a&right=evr_b']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <EvalComparePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const draw = await screen.findByTestId('tool-delta-draw_image')
    expect(within(draw).getByTestId('calls')).toHaveTextContent('10 → 14+4')
    expect(within(draw).getByTestId('calls')).toHaveAttribute('data-tone', 'neutral')
    expect(within(draw).getByTestId('p50')).toHaveTextContent('5.0s → 8.0s+3000ms')
    expect(within(draw).getByTestId('p50')).toHaveAttribute('data-tone', 'bad')
    expect(within(draw).getByTestId('p95')).toHaveAttribute('data-tone', 'good')
    expect(within(draw).getByTestId('error-rate')).toHaveTextContent('0.0% → 10.0%+10.0pp')
    expect(within(draw).getByTestId('error-rate')).toHaveAttribute('data-tone', 'bad')
    // 只在右侧出现的工具：左侧耗时为空
    const search = screen.getByTestId('tool-delta-search')
    expect(within(search).getByTestId('calls')).toHaveTextContent('0 → 3+3')
    expect(within(search).getByTestId('p50')).toHaveTextContent('- → 200ms-')
  })

  it('没有工具调用时不显示', async () => {
    setup()
    await screen.findByTestId('compare-summary')
    expect(screen.queryByTestId('tool-compare')).toBeNull()
  })
})

describe('EvalComparePage：统计结论与不稳定用例', () => {
  it('显示置信区间、符号检验 p 值；不稳定用例带标记', async () => {
    setup()
    expect(await screen.findByTestId('ci95-value')).toHaveTextContent('[-0.300, +0.050]')
    expect(screen.getByTestId('ci95')).toHaveAttribute('data-tone', 'neutral')
    expect(screen.getByTestId('sign-p')).toHaveTextContent('p = 1.000')
    expect(screen.getByTestId('wtl')).toHaveTextContent('1 / 5 / 2')
    const rows = await screen.findAllByTestId('compare-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toContainElement(screen.getByTestId('flaky-badge'))
    expect(rows[1]).not.toHaveTextContent('不稳定')
  })

  it('有「不稳定」页签，点击后按 class=flaky 查询', async () => {
    const api = setup()
    const tab = await screen.findByRole('tab', { name: /不稳定/ })
    expect(tab).toHaveTextContent('1')
    await userEvent.click(tab)
    await waitFor(() => expect(api.calls.some((c) => c.url.includes('/evals/compare') && c.url.includes('class=flaky'))).toBe(true))
  })
})
