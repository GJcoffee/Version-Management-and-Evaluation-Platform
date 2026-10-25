import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EvalCompareOut, EvalStatsOut } from '@/api/evalTypes'
import { CompareSummary, WarningBar } from './CompareSummary'
import { EvalProgressBar } from './EvalStatus'
import { EvaluatorEditor } from './EvaluatorEditor'
import { MetricCards } from './MetricCards'
import type { EvaluatorForm } from './evalUtils'

const stats = (over: Partial<EvalStatsOut> = {}): EvalStatsOut => ({
  cases: 4,
  items: 4,
  done: 4,
  failed: 0,
  cached: 0,
  pass_rate: 0.75,
  evaluators: { judge: { mean: 0.8, pass_rate: 0.75, errors: 0 } },
  latency_ms: { p50: 1200, p95: 3400 },
  tokens: { prompt: 1000, completion: 500 },
  cost: 0.0123,
  error_rate: 0,
  ...over,
})

describe('MetricCards', () => {
  it('显示通过率、评估器均值、延迟、tokens、成本、错误率', () => {
    render(<MetricCards stats={stats()} />)
    expect(screen.getByTestId('metric-pass-rate')).toHaveTextContent('75.0%')
    expect(screen.getByTestId('metric-ev-judge')).toHaveTextContent('0.80')
    expect(screen.getByTestId('metric-latency')).toHaveTextContent('1.2s / 3.4s')
    expect(screen.getByTestId('metric-tokens')).toHaveTextContent('1.5k')
    expect(screen.getByTestId('metric-cost')).toHaveTextContent('¥0.0123')
    expect(screen.getByTestId('metric-error-rate')).toHaveTextContent('0.0%')
  })
})

describe('EvalProgressBar', () => {
  it('显示进度与失败数', () => {
    render(<EvalProgressBar progress={{ total: 10, done: 3, failed: 1 }} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40')
    expect(screen.getByTestId('eval-progress')).toHaveTextContent('4/10')
    expect(screen.getByTestId('eval-progress')).toHaveTextContent('失败 1')
  })
})

describe('CompareSummary', () => {
  const data: EvalCompareOut = {
    left: { id: 'a', version_seq: 12, stats: stats({ latency_ms: { p50: 1000, p95: 2000 }, cost: 0.01 }) },
    right: { id: 'b', version_seq: 13, stats: stats({ pass_rate: 0.5, latency_ms: { p50: 800, p95: 2000 }, cost: 0.02 }) },
    warnings: ['两次评测的数据集修订不同（rev 3 vs rev 4）'],
    summary: {
      improved: 1,
      regressed: 2,
      unchanged: 3,
      new: 0,
      missing: 0,
      mean_delta: -0.25,
      metric_deltas: { pass_rate: -0.25, latency_p50_ms: -200, cost: 0.01, tokens_total: 0 },
    },
    cases: { items: [], next_cursor: null },
  }

  it('计数、平均 Δ；延迟降低为绿色，成本升高与通过率下降为红色', () => {
    render(<CompareSummary data={data} />)
    expect(screen.getByTestId('count-regressed')).toHaveTextContent('2')
    expect(screen.getByTestId('count-improved')).toHaveTextContent('1')
    expect(screen.getByTestId('mean-delta')).toHaveTextContent('-0.250')
    expect(screen.getByTestId('delta-latency_p50_ms')).toHaveAttribute('data-tone', 'good')
    expect(screen.getByTestId('delta-cost')).toHaveAttribute('data-tone', 'bad')
    expect(screen.getByTestId('delta-pass_rate')).toHaveAttribute('data-tone', 'bad')
    expect(screen.getByTestId('delta-tokens_total')).toHaveAttribute('data-tone', 'neutral')
  })

  it('警告条', () => {
    render(<WarningBar warnings={data.warnings} />)
    expect(screen.getByTestId('compare-warnings')).toHaveTextContent('数据集修订不同')
  })
})

afterEach(() => vi.unstubAllGlobals())

function qc(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // 评审 provider 列表等请求在测试中不会返回
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  return (
    <QueryClientProvider client={client}>
      {ui}
    </QueryClientProvider>
  )
}

function Harness({ initial }: { initial: EvaluatorForm[] }) {
  const [v, setV] = useState(initial)
  return <EvaluatorEditor value={v} onChange={setV} showErrors />
}

describe('EvaluatorEditor', () => {
  it('可添加四种评估器并删除；regex 缺少表达式时提示', async () => {
    render(qc(<Harness initial={[]} />))
    expect(screen.getByText('请至少添加一个评估器')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('add-evaluator-regex'))
    const card = screen.getByTestId('evaluator-regex')
    expect(within(card).getByText('请填写正则表达式', { selector: 'p.text-danger' })).toBeInTheDocument()
    await userEvent.type(within(card).getByLabelText('正则表达式'), 'A\\d+')
    expect(within(card).queryByText('请填写正则表达式', { selector: 'p.text-danger' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('add-evaluator-contains'))
    await userEvent.click(screen.getByTestId('add-evaluator-llm_judge'))
    await userEvent.click(screen.getByTestId('add-evaluator-exact_match'))
    expect(screen.getByTestId('evaluator-contains')).toBeInTheDocument()
    expect(screen.getByTestId('evaluator-judge')).toBeInTheDocument()
    expect(within(screen.getByTestId('evaluator-judge')).getByLabelText('评审 Provider')).toBeInTheDocument()
    expect(screen.getByTestId('evaluator-exact')).toBeInTheDocument()

    await userEvent.click(screen.getByLabelText('删除评估器 contains'))
    expect(screen.queryByTestId('evaluator-contains')).not.toBeInTheDocument()
  })
})
