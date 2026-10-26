import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EvalRunDetailOut, EvalStatsOut } from '@/api/evalTypes'
import { initialEvalStreamState, type EvalStreamState } from '@/components/eval/useEvalStream'
import { mockApi } from '@/test/mockApi'
import EvalReportPage from './EvalReportPage'

vi.mock('@/stores/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))

let streamState: EvalStreamState = initialEvalStreamState
const reopen = vi.fn()
vi.mock('@/components/eval/useEvalStream', async () => {
  const actual = await vi.importActual<typeof import('@/components/eval/useEvalStream')>('@/components/eval/useEvalStream')
  return { ...actual, useEvalStream: () => ({ state: streamState, status: 'open', reopen }) }
})
// 用例表（虚拟滚动）和抽屉有各自的测试；这里只看报告页传给它们的筛选条件
vi.mock('@/components/eval/EvalItemsTable', () => ({
  EvalItemsTable: (p: { cls?: string; tag?: string; hasHuman?: boolean }) => (
    <div data-testid="items-table" data-cls={p.cls} data-tag={p.tag} data-has-human={String(!!p.hasHuman)} />
  ),
}))
vi.mock('@/components/eval/EvalItemDrawer', () => ({ EvalItemDrawer: () => null }))

beforeEach(() => {
  streamState = initialEvalStreamState
  reopen.mockClear()
})
afterEach(() => vi.unstubAllGlobals())

const stats = (over: Partial<EvalStatsOut> = {}): EvalStatsOut => ({
  cases: 10,
  items: 30,
  done: 28,
  failed: 2,
  cached: 0,
  pass_rate: 0.7,
  evaluators: { exact: { mean: 0.7, pass_rate: 0.7, errors: 0 } },
  latency_ms: { p50: 1000, p95: 2000 },
  tokens: { prompt: 100, completion: 50 },
  cost: 0.01,
  error_rate: 0.07,
  pass_at_k: { '1': 0.7, '3': 0.85 },
  flaky_cases: 3,
  by_tag: { p0: { pass_rate: 0.9, cases: 4 }, p1: { pass_rate: 0.5, cases: 6 } },
  ...over,
})

const run = (over: Partial<EvalRunDetailOut> = {}): EvalRunDetailOut =>
  ({
    id: 'evr_1',
    batch_id: 'evb_1',
    name: '回归',
    agent_id: 'agt_1',
    agent_name: 'bot',
    version_id: 'ver_1',
    version_seq: 3,
    dataset_rev_id: 'arv_1',
    dataset_name: 'ds',
    dataset_rev: 1,
    case_filter: {},
    evaluators: [{ key: 'exact', type: 'exact_match', gate: true, values: [], normalize: ['strip'] }],
    repeats: 3,
    concurrency: 4,
    cache_policy: 'reuse',
    budget: {},
    status: 'completed_with_errors',
    progress: { total: 30, done: 28, failed: 2 },
    stats: stats(),
    error: null,
    created_at: '2026-10-25T00:00:00Z',
    started_at: '2026-10-25T00:00:01Z',
    finished_at: '2026-10-25T00:01:00Z',
    batch_runs: [],
    ...over,
  }) as EvalRunDetailOut

function setup(r: EvalRunDetailOut, extra: Record<string, unknown> = {}) {
  const api = mockApi({
    'GET /api/v1/evals/evr_1': r,
    'GET /api/v1/evals': { items: [], next_cursor: null },
    'POST /api/v1/evals/evr_1/pause': { ...r, status: 'paused' },
    'POST /api/v1/evals/evr_1/retry-failed': { retried: 2 },
    ...extra,
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <MemoryRouter initialEntries={['/evals/evr_1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/evals/:id" element={<EvalReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return api
}

describe('EvalReportPage：运行控制', () => {
  it('部分失败：可以重跑失败项（重新订阅实时进度）和重新评分；不显示暂停', async () => {
    const api = setup(run())
    const retry = await screen.findByTestId('retry-failed')
    expect(retry).toHaveTextContent('重跑失败项（2）')
    expect(screen.getByTestId('open-rescore')).toBeInTheDocument()
    expect(screen.queryByTestId('pause-eval')).not.toBeInTheDocument()
    await userEvent.click(retry)
    await waitFor(() => expect(api.find('POST', /retry-failed$/)).toHaveLength(1))
    await waitFor(() => expect(reopen).toHaveBeenCalledTimes(1))
  })

  it('运行中：可以暂停和取消，不能重新评分', async () => {
    const api = setup(run({ status: 'running', stats: null, finished_at: null, progress: { total: 30, done: 5, failed: 0 } }))
    await userEvent.click(await screen.findByTestId('pause-eval'))
    await waitFor(() => expect(api.find('POST', /pause$/)).toHaveLength(1))
    expect(screen.getByTestId('cancel-eval')).toBeInTheDocument()
    expect(screen.queryByTestId('open-rescore')).not.toBeInTheDocument()
  })

  it('因超预算自动暂停（来自实时状态）：显示预算提示，按钮变为「调高预算并继续」', async () => {
    streamState = { ...initialEvalStreamState, status: 'paused', pauseReason: 'budget', progress: { total: 30, done: 10, failed: 0, cost_new: 1.5 } }
    setup(run({ status: 'running', stats: null, finished_at: null, budget: { max_cost: 1 }, progress: { total: 30, done: 10, failed: 0 } }))
    const notice = await screen.findByTestId('pause-notice')
    expect(notice).toHaveAttribute('data-reason', 'budget')
    expect(notice).toHaveTextContent('¥1.0000')
    expect(notice).toHaveTextContent('¥1.5000')
    expect(screen.getByTestId('resume-eval')).toHaveTextContent('调高预算并继续')
    expect(screen.getByTestId('run-budget')).toHaveTextContent('¥1.0000')
  })

  it('从详情读到的暂停原因（刷新页面后没有实时状态）也能显示', async () => {
    setup(run({ status: 'paused', pause_reason: 'manual', stats: null, finished_at: null }))
    expect(await screen.findByTestId('pause-notice')).toHaveAttribute('data-reason', 'manual')
    expect(screen.getByTestId('resume-eval')).toHaveTextContent('继续')
  })
})

describe('EvalReportPage：统计与门禁', () => {
  it('显示 pass@k、不稳定用例、标签分组；点击后联动用例表的筛选', async () => {
    setup(run())
    expect(await screen.findByTestId('pass-at-3')).toHaveTextContent('85.0%')
    expect(screen.getByTestId('flaky-summary')).toHaveAttribute('data-flaky', '3')
    const table = screen.getByTestId('items-table')
    expect(table).toHaveAttribute('data-cls', '')
    await userEvent.click(screen.getByTestId('show-flaky'))
    expect(screen.getByTestId('items-table')).toHaveAttribute('data-cls', 'flaky')
    await userEvent.click(screen.getByTestId('tag-row-p1'))
    expect(screen.getByTestId('items-table')).toHaveAttribute('data-tag', 'p1')
    await userEvent.click(screen.getByTestId('tag-row-p1'))
    expect(screen.getByTestId('items-table')).toHaveAttribute('data-tag', '')
  })

  it('页面上有 CI 门禁面板，可以直接检查', async () => {
    const api = setup(run({ status: 'completed' }), {
      'GET /api/v1/evals/evr_1/gate': { passed: true, reasons: [], summary: { run_id: 'evr_1', status: 'completed', pass_rate: 0.7, max_regressions: 0, min_pass_rate: null } },
    })
    const panel = await screen.findByTestId('gate-panel')
    await userEvent.click(within(panel).getByTestId('run-gate'))
    expect(await within(panel).findByTestId('gate-verdict')).toHaveTextContent('门禁通过')
    expect(api.find('GET', /\/evals\/evr_1\/gate/)).toHaveLength(1)
  })
})

describe('EvalReportPage：人工评审入口', () => {
  const human = { key: 'human', type: 'human', gate: true, scale: [1, 5] }

  it('有人工评审评估器时显示入口与待评数量，并提示去评审', async () => {
    setup(
      run({
        evaluators: [{ key: 'exact', type: 'exact_match', gate: true }, human],
        stats: stats({ evaluators: { exact: { mean: 1 }, human: { mean: null, n: 0, pending_human: 4 } } }),
      }),
    )
    const link = await screen.findByTestId('open-annotate')
    expect(link).toHaveAttribute('href', '/evals/evr_1/annotate')
    expect(link).toHaveTextContent('待评 4')
    expect(screen.getByTestId('pending-human')).toHaveTextContent('4 条评分在等待人工评审')
    expect(screen.getByTestId('items-table')).toHaveAttribute('data-has-human', 'true')
  })

  it('没有人工评审评估器时没有入口', async () => {
    setup(run())
    await screen.findByTestId('eval-report')
    expect(screen.queryByTestId('open-annotate')).not.toBeInTheDocument()
    expect(screen.queryByTestId('pending-human')).not.toBeInTheDocument()
  })
})
