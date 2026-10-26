import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EvalEstimate, EvalGateResult, EvalStatsOut, EvaluatorOut } from '@/api/evalTypes'
import { mockApi } from '@/test/mockApi'
import { CompareSummary, StatisticalConclusion } from './CompareSummary'
import { EstimatePanel } from './EstimatePanel'
import { GatePanel, GateResultView } from './GatePanel'
import { RescoreDialog } from './RescoreDialog'
import { PauseNotice, RunControls } from './RunControls'
import { StatsPanel } from './StatsPanel'

vi.mock('@/stores/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))

afterEach(() => vi.unstubAllGlobals())

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

const evaluators: EvaluatorOut[] = [
  { key: 'exact', type: 'exact_match', gate: true, values: [], normalize: ['strip'] },
  { key: 'kw', type: 'contains', gate: false, values: ['退货'], mode: 'all', negate: false, case_sensitive: true },
]

function controls(over: Partial<React.ComponentProps<typeof RunControls>> = {}) {
  return (
    <RunControls
      runId="evr_1"
      status="running"
      pauseReason={null}
      progress={{ total: 10, done: 2, failed: 0 }}
      failedCount={0}
      budget={undefined}
      evaluators={evaluators}
      {...over}
    />
  )
}

describe('RunControls：暂停 / 继续 / 重跑失败项', () => {
  it('运行中只有暂停；点击后 POST /pause', async () => {
    const api = mockApi({ 'POST /api/v1/evals/evr_1/pause': { id: 'evr_1', status: 'paused' } })
    render(wrap(controls()))
    expect(screen.queryByTestId('resume-eval')).not.toBeInTheDocument()
    expect(screen.queryByTestId('open-rescore')).not.toBeInTheDocument()
    await userEvent.click(screen.getByTestId('pause-eval'))
    await waitFor(() => expect(api.find('POST', /\/evals\/evr_1\/pause$/)).toHaveLength(1))
  })

  it('手动暂停：继续直接 POST /resume（不带预算）', async () => {
    const api = mockApi({ 'POST /api/v1/evals/evr_1/resume': { id: 'evr_1', status: 'running' } })
    render(wrap(controls({ status: 'paused', pauseReason: 'manual' })))
    expect(screen.queryByTestId('pause-eval')).not.toBeInTheDocument()
    await userEvent.click(screen.getByTestId('resume-eval'))
    await waitFor(() => expect(api.calls).toHaveLength(1))
    expect(api.calls[0]).toMatchObject({ method: 'POST', body: {} })
    expect(api.calls[0].url).toMatch(/\/evals\/evr_1\/resume$/)
  })

  it('预算暂停：先弹出调高预算对话框，提交时带上新预算；非法输入不提交', async () => {
    const api = mockApi({ 'POST /api/v1/evals/evr_1/resume': { id: 'evr_1', status: 'running' } })
    render(wrap(controls({ status: 'paused', pauseReason: 'budget', budget: { max_cost: 1 }, progress: { total: 10, done: 5, failed: 0, cost_new: 1.2 } })))
    await userEvent.click(screen.getByTestId('resume-eval'))
    expect(api.calls).toHaveLength(0)
    const cost = screen.getByLabelText('成本上限')
    expect(cost).toHaveValue(1)
    await userEvent.clear(cost)
    await userEvent.type(cost, '-3')
    await userEvent.click(screen.getByTestId('confirm-resume'))
    expect(screen.getByText('请输入不小于 0 的数字')).toBeInTheDocument()
    expect(api.calls).toHaveLength(0)
    await userEvent.clear(cost)
    await userEvent.type(cost, '5')
    await userEvent.click(screen.getByTestId('confirm-resume'))
    await waitFor(() => expect(api.calls).toHaveLength(1))
    expect(api.calls[0].body).toEqual({ budget: { max_cost: 5 } })
  })

  it('有失败项时显示「重跑失败项」，点击 POST /retry-failed 并通知重新订阅', async () => {
    const api = mockApi({ 'POST /api/v1/evals/evr_1/retry-failed': { retried: 3 } })
    const onReactivated = vi.fn()
    render(wrap(controls({ status: 'completed_with_errors', failedCount: 3, onReactivated })))
    const btn = screen.getByTestId('retry-failed')
    expect(btn).toHaveTextContent('重跑失败项（3）')
    await userEvent.click(btn)
    await waitFor(() => expect(onReactivated).toHaveBeenCalledTimes(1))
    expect(api.find('POST', /retry-failed$/)).toHaveLength(1)
  })

  it('重跑 0 个时不触发重新订阅；没有失败项不显示按钮', async () => {
    mockApi({ 'POST /api/v1/evals/evr_1/retry-failed': { retried: 0 } })
    const onReactivated = vi.fn()
    const { rerender } = render(wrap(controls({ status: 'completed_with_errors', failedCount: 1, onReactivated })))
    await userEvent.click(screen.getByTestId('retry-failed'))
    await waitFor(() => expect(screen.getByTestId('retry-failed')).not.toBeDisabled())
    expect(onReactivated).not.toHaveBeenCalled()
    rerender(wrap(controls({ status: 'completed', failedCount: 0 })))
    expect(screen.queryByTestId('retry-failed')).not.toBeInTheDocument()
  })

  it('后端拒绝时不崩溃（例如仍超预算）', async () => {
    mockApi({ 'POST /api/v1/evals/evr_1/resume': { __status: 409, body: { error: { code: 'INVALID_STATE', message: '已用成本超出预算' } } } })
    render(wrap(controls({ status: 'paused', pauseReason: 'manual' })))
    await userEvent.click(screen.getByTestId('resume-eval'))
    const { toast } = await import('@/stores/toast')
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('无法继续', { description: '已用成本超出预算' }))
  })
})

describe('PauseNotice', () => {
  it('手动暂停与预算暂停分别提示；非暂停不显示', () => {
    const { rerender } = render(<PauseNotice status="running" reason={null} budget={undefined} progress={undefined} />)
    expect(screen.queryByTestId('pause-notice')).not.toBeInTheDocument()
    rerender(<PauseNotice status="paused" reason="manual" budget={undefined} progress={undefined} />)
    expect(screen.getByTestId('pause-notice')).toHaveAttribute('data-reason', 'manual')
    rerender(<PauseNotice status="paused" reason="budget" budget={{ max_cost: 2 }} progress={{ total: 5, done: 2, failed: 0, cost_new: 2.5 }} />)
    expect(screen.getByTestId('pause-notice')).toHaveAttribute('data-reason', 'budget')
    expect(screen.getByTestId('pause-notice')).toHaveTextContent('超出预算自动暂停')
    expect(screen.getByTestId('pause-notice')).toHaveTextContent('¥2.5000')
  })
})

describe('RescoreDialog', () => {
  it('在现有配置上修改评估器：未改动的原样提交，只提交一次；展示按 evaluator_hash 增量的结果', async () => {
    const api = mockApi({
      'POST /api/v1/evals/evr_1/rescore': { rescored_items: 4, evaluators_changed: ['kw'], rebased: false },
    })
    const onStarted = vi.fn()
    render(wrap(<RescoreDialog runId="evr_1" evaluators={evaluators} onClose={() => {}} onStarted={onStarted} />))
    const kw = screen.getByTestId('evaluator-kw')
    const values = within(kw).getByLabelText('期望值')
    expect(values).toHaveValue('退货')
    await userEvent.type(values, '\n七天无理由')
    await userEvent.click(screen.getByTestId('submit-rescore'))
    await waitFor(() => expect(screen.getByTestId('rescore-result')).toBeInTheDocument())
    expect(api.calls).toHaveLength(1)
    const sent = (api.calls[0].body as { evaluators: Array<Record<string, unknown>> }).evaluators
    expect(sent[0]).toEqual(evaluators[0]) // 未改动的评估器与服务端保存的配置完全一致
    expect(sent[1]).toMatchObject({ key: 'kw', type: 'contains', values: ['退货', '七天无理由'], gate: false })
    expect(screen.getByTestId('rescore-result')).toHaveTextContent('评估器 kw 的配置有变化')
    expect(screen.getByTestId('rescore-result')).toHaveTextContent('4 个用例')
    expect(onStarted).toHaveBeenCalledTimes(1)
  })

  it('配置无变化时服务端返回 0：提示没有需要重新评分的用例，不触发重新订阅', async () => {
    mockApi({ 'POST /api/v1/evals/evr_1/rescore': { rescored_items: 0, evaluators_changed: [], rebased: false } })
    const onStarted = vi.fn()
    render(wrap(<RescoreDialog runId="evr_1" evaluators={evaluators} onClose={() => {}} onStarted={onStarted} />))
    await userEvent.click(screen.getByTestId('submit-rescore'))
    await waitFor(() => expect(screen.getByTestId('rescore-result')).toHaveAttribute('data-rescored', '0'))
    expect(screen.getByTestId('rescore-result')).toHaveTextContent('没有变化')
    expect(onStarted).not.toHaveBeenCalled()
  })

  it('「只重算出错 / 过期」不带 evaluators；表单有错误时不提交', async () => {
    const api = mockApi({ 'POST /api/v1/evals/evr_1/rescore': { rescored_items: 1, evaluators_changed: [], rebased: false } })
    render(wrap(<RescoreDialog runId="evr_1" evaluators={evaluators} onClose={() => {}} />))
    await userEvent.click(screen.getByLabelText('删除评估器 exact'))
    await userEvent.click(screen.getByLabelText('删除评估器 kw'))
    await userEvent.click(screen.getByTestId('submit-rescore'))
    expect(screen.getByText('请至少添加一个评估器')).toBeInTheDocument()
    expect(api.calls).toHaveLength(0)
    await userEvent.click(screen.getByTestId('rescore-errors-only'))
    await waitFor(() => expect(api.calls).toHaveLength(1))
    expect(api.calls[0].body).toEqual({})
  })

  it('后端拒绝（例如评测还在运行）时显示错误并保留对话框', async () => {
    mockApi({ 'POST /api/v1/evals/evr_1/rescore': { __status: 409, body: { error: { code: 'INVALID_STATE', message: '评测当前状态为 running，只有已完成的评测可以重新评分' } } } })
    render(wrap(<RescoreDialog runId="evr_1" evaluators={evaluators} onClose={() => {}} />))
    await userEvent.click(screen.getByTestId('submit-rescore'))
    expect(await screen.findByRole('alert')).toHaveTextContent('只有已完成的评测可以重新评分')
    expect(screen.queryByTestId('rescore-result')).not.toBeInTheDocument()
  })
})

describe('EstimatePanel', () => {
  const est = (over: Partial<EvalEstimate> = {}): EvalEstimate => ({
    items: 600,
    cached_items: 120,
    tokens: 1_800_000,
    cost: 6.4,
    gross_tokens: 2_250_000,
    gross_cost: 8,
    cost_known: true,
    basis: 'history',
    exceeds_budget: null,
    per_version: [],
    ...over,
  })

  it('显示条数、新增 tokens 与成本、估算依据', () => {
    render(<EstimatePanel estimate={est()} />)
    expect(screen.getByTestId('estimate-items')).toHaveTextContent('600')
    expect(screen.getByTestId('estimate-tokens')).toHaveTextContent('1.8M')
    expect(screen.getByTestId('estimate-cost')).toHaveTextContent('¥6.4000')
    expect(screen.getByTestId('estimate-panel')).toHaveTextContent('120 次命中缓存')
    expect(screen.getByTestId('estimate-panel')).toHaveTextContent('历史评测均值')
    expect(screen.queryByTestId('estimate-exceeds')).not.toBeInTheDocument()
  })

  it('超出预算、价格未知、已过期各自提示', () => {
    render(<EstimatePanel estimate={est({ exceeds_budget: true, cost_known: false, basis: 'heuristic' })} stale />)
    expect(screen.getByTestId('estimate-exceeds')).toBeInTheDocument()
    expect(screen.getByTestId('estimate-cost')).toHaveTextContent('未知')
    expect(screen.getByTestId('estimate-panel')).toHaveTextContent('没有配置价格')
    expect(screen.getByTestId('estimate-panel')).toHaveAttribute('data-stale', 'true')
    expect(screen.getByTestId('estimate-panel')).toHaveTextContent('粗略估算')
  })
})

describe('StatsPanel', () => {
  const stats = (over: Partial<EvalStatsOut> = {}): EvalStatsOut => ({
    cases: 20,
    items: 60,
    done: 60,
    failed: 0,
    cached: 0,
    pass_rate: 0.8,
    evaluators: {},
    latency_ms: { p50: 1, p95: 2 },
    tokens: { prompt: 1, completion: 1 },
    cost: 1,
    error_rate: 0,
    pass_at_k: { '1': 0.8, '3': 0.9 },
    flaky_cases: 4,
    by_tag: { p0: { pass_rate: 0.95, cases: 10 }, p1: { pass_rate: 0.4, cases: 5 } },
    ...over,
  })

  it('pass@k、不稳定用例数与占比、标签分组（最弱的在前）', () => {
    render(<StatsPanel stats={stats()} />)
    expect(screen.getByTestId('pass-at-1')).toHaveTextContent('80.0%')
    expect(screen.getByTestId('pass-at-3')).toHaveTextContent('90.0%')
    expect(screen.getByTestId('flaky-summary')).toHaveTextContent('4')
    expect(screen.getByTestId('flaky-summary')).toHaveTextContent('占 20.0%')
    const rows = within(screen.getByTestId('by-tag')).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('p1')
    expect(rows[0]).toHaveTextContent('40.0%')
    expect(rows[1]).toHaveTextContent('p0')
  })

  it('点击「查看不稳定用例」与标签行触发回调；0 个不稳定时不显示链接', async () => {
    const onShowFlaky = vi.fn()
    const onPickTag = vi.fn()
    const { rerender } = render(<StatsPanel stats={stats()} onShowFlaky={onShowFlaky} onPickTag={onPickTag} />)
    await userEvent.click(screen.getByTestId('show-flaky'))
    await userEvent.click(screen.getByTestId('tag-row-p1'))
    expect(onShowFlaky).toHaveBeenCalledTimes(1)
    expect(onPickTag).toHaveBeenCalledWith('p1')
    rerender(<StatsPanel stats={stats({ flaky_cases: 0 })} onShowFlaky={onShowFlaky} />)
    expect(screen.queryByTestId('show-flaky')).not.toBeInTheDocument()
  })

  it('repeats 不足没有 pass@k 时说明原因；什么统计都没有就不渲染', () => {
    const { rerender, container } = render(<StatsPanel stats={stats({ pass_at_k: {}, by_tag: {} })} />)
    expect(screen.getByText(/重复次数不足/)).toBeInTheDocument()
    rerender(<StatsPanel stats={stats({ pass_at_k: undefined, flaky_cases: undefined, by_tag: undefined })} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('对比的统计结论', () => {
  const summary = {
    improved: 7,
    regressed: 1,
    unchanged: 10,
    new: 0,
    missing: 0,
    mean_delta: 0.12,
    paired: 18,
    ci95: [0.03, 0.2] as [number, number],
    wins: 7,
    ties: 10,
    losses: 1,
    sign_test_p: 0.0703,
    flaky: 2,
    unscored: 1,
    metric_deltas: {},
  }

  it('置信区间与符号检验 p 值', () => {
    render(<StatisticalConclusion summary={summary} />)
    expect(screen.getByTestId('ci95-value')).toHaveTextContent('[+0.030, +0.200]')
    expect(screen.getByTestId('ci95')).toHaveAttribute('data-tone', 'good')
    expect(screen.getByTestId('ci95')).toHaveTextContent('18 个可配对用例')
    expect(screen.getByTestId('wtl')).toHaveTextContent('7 / 10 / 1')
    expect(screen.getByTestId('sign-p')).toHaveTextContent('p = 0.070')
    expect(screen.getByTestId('sign-test')).toHaveAttribute('data-tone', 'neutral')
    expect(screen.getByTestId('stat-footnote')).toHaveTextContent('2 个用例在重复间结果不稳定')
    expect(screen.getByTestId('stat-footnote')).toHaveTextContent('1 个用例某一侧没有可用分数')
  })

  it('配对用例不足：区间与 p 值显示 -', () => {
    render(<StatisticalConclusion summary={{ ...summary, ci95: null, sign_test_p: null, wins: 0, losses: 0, flaky: 0, unscored: 0 }} />)
    expect(screen.getByTestId('ci95-value')).toHaveTextContent('-')
    expect(screen.getByTestId('sign-p')).toHaveTextContent('p = -')
    expect(screen.queryByTestId('stat-footnote')).not.toBeInTheDocument()
  })

  it('CompareSummary 内嵌统计结论', () => {
    const side = { id: 'a', version_seq: 1, stats: null }
    render(<CompareSummary data={{ left: side, right: { ...side, id: 'b' }, warnings: [], summary, cases: { items: [], next_cursor: null } }} />)
    expect(screen.getByTestId('stat-conclusion')).toBeInTheDocument()
  })
})

describe('CI 门禁', () => {
  const failing: EvalGateResult = {
    passed: false,
    reasons: ['相对基线有 4 个用例退化，超过允许的 1 个', '通过率 0.0% 低于要求的 90.0%'],
    summary: {
      run_id: 'evr_2',
      status: 'completed',
      pass_rate: 0,
      baseline_run_id: 'evr_1',
      baseline_pass_rate: 1,
      max_regressions: 1,
      min_pass_rate: 0.9,
      regressions: 4,
      improved: 0,
      unchanged: 0,
      mean_delta: -1,
      sign_test_p: 0.125,
      regressed_cases: [{ case_key: 'c001', delta: -1, item_ids: [] }],
    },
  }

  it('GateResultView：未通过时列出原因与退化用例，并链接到对比页', () => {
    render(wrap(<GateResultView result={failing} />))
    expect(screen.getByTestId('gate-verdict')).toHaveTextContent('门禁未通过')
    expect(screen.getByTestId('gate-reasons')).toHaveTextContent('4 个用例退化')
    expect(screen.getByTestId('gate-regressed')).toHaveTextContent('c001')
    expect(screen.getByText('在评测对比页查看详情')).toHaveAttribute('href', '/evals/compare?left=evr_1&right=evr_2')
  })

  it('GateResultView：通过时没有原因列表', () => {
    render(wrap(<GateResultView result={{ passed: true, reasons: [], summary: { ...failing.summary, regressed_cases: undefined } }} />))
    expect(screen.getByTestId('gate-verdict')).toHaveTextContent('门禁通过')
    expect(screen.queryByTestId('gate-reasons')).not.toBeInTheDocument()
  })

  it('GatePanel：选基线、设阈值后 GET /gate，参数正确（最低通过率按百分比换算）', async () => {
    const api = mockApi({
      'GET /api/v1/evals': {
        items: [
          { id: 'evr_1', batch_id: 'b', name: '基线', agent_id: 'agt_1', agent_name: 'bot', version_id: 'v', version_seq: 1, dataset_name: 'ds', dataset_rev: 1, status: 'completed', created_at: '2026-10-25T00:00:00Z' },
          { id: 'evr_2', batch_id: 'b', name: '本次', agent_id: 'agt_1', agent_name: 'bot', version_id: 'v', version_seq: 2, dataset_name: 'ds', dataset_rev: 1, status: 'completed', created_at: '2026-10-25T00:00:00Z' },
        ],
        next_cursor: null,
      },
      'GET /api/v1/evals/evr_2/gate': failing,
    })
    render(wrap(<GatePanel run={{ id: 'evr_2', agent_id: 'agt_1', status: 'completed' }} />))
    const select = screen.getByTestId('gate-baseline')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(2)) // 不比较基线 + evr_1（排除自己）
    await userEvent.selectOptions(select, 'evr_1')
    await userEvent.clear(screen.getByLabelText('允许退化的用例数'))
    await userEvent.type(screen.getByLabelText('允许退化的用例数'), '1')
    await userEvent.type(screen.getByLabelText('最低通过率'), '90')
    await userEvent.click(screen.getByTestId('run-gate'))
    expect(await screen.findByTestId('gate-result')).toHaveAttribute('data-passed', 'false')
    const call = api.find('GET', /\/gate\?/)[0]
    const q = new URL(call.url, 'http://x').searchParams
    expect(Object.fromEntries(q)).toEqual({ baseline: 'evr_1', max_regressions: '1', min_pass_rate: '0.9', threshold: '0.05' })
    // 查询候选基线时按 agent 过滤，且只要已结束的评测
    const list = api.find('GET', /\/api\/v1\/evals\?/)[0]
    expect(new URL(list.url, 'http://x').searchParams.get('agent_id')).toBe('agt_1')
  })

  it('GatePanel：不选基线只检查通过率；非法输入不发请求', async () => {
    const api = mockApi({
      'GET /api/v1/evals': { items: [], next_cursor: null },
      'GET /api/v1/evals/evr_2/gate': { passed: true, reasons: [], summary: { ...failing.summary, baseline_run_id: null, regressions: undefined, regressed_cases: undefined } },
    })
    render(wrap(<GatePanel run={{ id: 'evr_2', agent_id: 'agt_1', status: 'running' }} />))
    expect(screen.getByText(/还没有成功结束/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('最低通过率'), '150')
    await userEvent.click(screen.getByTestId('run-gate'))
    expect(screen.getByText('请输入 0 到 100 之间的数')).toBeInTheDocument()
    expect(api.find('GET', /gate/)).toHaveLength(0)
    await userEvent.clear(screen.getByLabelText('最低通过率'))
    await userEvent.click(screen.getByTestId('run-gate'))
    expect(await screen.findByTestId('gate-verdict')).toHaveTextContent('门禁通过')
    expect(Object.fromEntries(new URL(api.find('GET', /gate/)[0].url, 'http://x').searchParams)).toEqual({ max_regressions: '0', threshold: '0.05' })
  })
})
