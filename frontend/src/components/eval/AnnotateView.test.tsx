import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EvalRunDetailOut } from '@/api/evalTypes'
import { mockApi } from '@/test/mockApi'
import { AnnotateView } from './AnnotateView'

vi.mock('@/stores/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))
// 会话回放依赖 EventSource，这里只验证它被渲染
vi.mock('./ItemDetail', () => ({ ItemConversation: ({ sessionId }: { sessionId?: string | null }) => <div data-testid="conversation">{sessionId}</div> }))

beforeEach(() => window.localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const run = (over: Partial<EvalRunDetailOut> = {}): EvalRunDetailOut =>
  ({
    id: 'evr_1',
    batch_id: 'evb_1',
    name: '人工评审',
    agent_id: 'agt_1',
    agent_name: 'bot',
    version_id: 'ver_1',
    version_seq: 1,
    dataset_rev_id: 'arv_1',
    dataset_name: 'ds',
    dataset_rev: 1,
    case_filter: {},
    evaluators: [{ key: 'human', type: 'human', gate: true, scale: [1, 5], guideline: '回复是否礼貌、是否解决问题' }],
    repeats: 1,
    concurrency: 4,
    cache_policy: 'reuse',
    status: 'completed',
    progress: { total: 3, done: 3, failed: 0 },
    stats: {
      cases: 3,
      items: 3,
      done: 3,
      failed: 0,
      cached: 0,
      pass_rate: null,
      evaluators: { human: { mean: 0.5, n: 1, pending_human: 2 } },
      latency_ms: { p50: 1, p95: 1 },
      tokens: { prompt: 1, completion: 1 },
      cost: 0,
      error_rate: 0,
    },
    error: null,
    created_at: '2026-10-25T00:00:00Z',
    started_at: null,
    finished_at: null,
    batch_runs: [],
    ...over,
  }) as EvalRunDetailOut

const item = (id: string, key: string, over: Record<string, unknown> = {}) => ({
  id,
  run_id: 'evr_1',
  case_id: `cas_${key}`,
  case_key: key,
  repeat_idx: 0,
  input_preview: `问题 ${key}`,
  output_preview: `回答 ${key}`,
  expected: null,
  tags: ['p0'],
  status: 'done',
  passed: null,
  score: null,
  cached: false,
  attempts: 1,
  error: null,
  latency_ms: 10,
  cost: 0,
  usage: null,
  session_id: `ses_${key}`,
  scores: [{ evaluator_key: 'human', evaluator_type: 'human', status: 'pending_human', score: null, raw_score: null, passed: null, reason: null }],
  ...over,
})
const detail = (id: string, key: string) => ({ ...item(id, key), output: `完整回答 ${key}`, turns: [], case_detail: { input: { turns: [`完整问题 ${key}`] }, rubric: '不能骂人' } })

function setup(r = run(), items = [item('evi_1', 'c001'), item('evi_2', 'c002')]) {
  const api = mockApi({
    'GET /api/v1/evals/evr_1/items': { items, next_cursor: null },
    'GET /api/v1/evals/evr_1/items/evi_1': detail('evi_1', 'c001'),
    'GET /api/v1/evals/evr_1/items/evi_2': detail('evi_2', 'c002'),
    'POST /api/v1/evals/evr_1/human-scores': { updated: 1 },
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AnnotateView run={r} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return api
}

describe('AnnotateView', () => {
  it('显示当前用例的输入 / 输出 / 评分标准 / 轨迹，以及进度', async () => {
    const api = setup()
    expect(await screen.findByTestId('annotate-case')).toHaveTextContent('c001')
    await waitFor(() => expect(screen.getByTestId('annotate-output')).toHaveTextContent('完整回答 c001'))
    expect(screen.getByTestId('annotate-input')).toHaveTextContent('完整问题 c001')
    expect(screen.getByText('不能骂人')).toBeInTheDocument()
    expect(screen.getByTestId('guideline')).toHaveTextContent('回复是否礼貌')
    expect(screen.getByTestId('conversation')).toHaveTextContent('ses_c001')
    expect(screen.getByTestId('annotate-progress')).toHaveTextContent('已评 1/3，待评 2')
    // 待评审队列用 class=pending_human 取
    expect(api.find('GET', /items\?/)[0].url).toContain('class=pending_human')
  })

  it('输出里的图片和视频链接直接渲染，方便人工审核', async () => {
    mockApi({
      'GET /api/v1/evals/evr_1/items': { items: [item('evi_1', 'c001')], next_cursor: null },
      'GET /api/v1/evals/evr_1/items/evi_1': {
        ...detail('evi_1', 'c001'),
        output: '生成好了 ![图](https://cdn.example.com/a.png) 视频 https://cdn.example.com/v.mp4',
      },
    })
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <AnnotateView run={run()} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const out = await screen.findByTestId('annotate-output')
    await waitFor(() => expect(within(out).getAllByTestId('media')).toHaveLength(2))
    expect(within(out).getAllByTestId('media').map((m) => m.getAttribute('data-kind'))).toEqual(['image', 'video'])
    expect(out).toHaveTextContent('https://cdn.example.com/v.mp4') // 原文链接仍在
  })

  it('没选分数时不能提交；选分、写备注后提交到 human-scores，并自动切到下一条', async () => {
    const api = setup()
    await screen.findByTestId('annotate-case')
    expect(screen.getByTestId('submit-score')).toBeDisabled()
    await userEvent.click(screen.getByTestId('score-btn-human-4'))
    expect(screen.getByTestId('score-btn-human-4')).toHaveAttribute('aria-pressed', 'true')
    await userEvent.type(screen.getByLabelText('human 备注'), '语气不错')
    await userEvent.type(screen.getByLabelText('评审人'), '小王')
    await userEvent.click(screen.getByTestId('submit-score'))
    await waitFor(() => expect(api.find('POST', /human-scores$/)).toHaveLength(1))
    expect(api.find('POST', /human-scores$/)[0].body).toEqual({
      scores: [{ item_id: 'evi_1', evaluator_key: 'human', raw_score: 4, reason: '语气不错', annotator: '小王' }],
    })
    await waitFor(() => expect(screen.getByTestId('annotate-case')).toHaveTextContent('c002'))
    // 下一条是干净的草稿
    expect(screen.getByTestId('submit-score')).toBeDisabled()
    expect(window.localStorage.getItem('evalp.annotator')).toBe('小王')
  })

  it('键盘：数字键打分、J/K 切换、Enter 提交', async () => {
    const api = setup()
    await screen.findByTestId('annotate-case')
    await userEvent.keyboard('3')
    expect(screen.getByTestId('score-btn-human-3')).toHaveAttribute('aria-pressed', 'true')
    await userEvent.keyboard('9') // 量程外：忽略
    expect(screen.getByTestId('score-btn-human-3')).toHaveAttribute('aria-pressed', 'true')
    await userEvent.keyboard('j')
    expect(screen.getByTestId('annotate-case')).toHaveTextContent('c002')
    await userEvent.keyboard('k')
    expect(screen.getByTestId('annotate-case')).toHaveTextContent('c001')
    // 草稿随用例保留
    expect(screen.getByTestId('score-btn-human-3')).toHaveAttribute('aria-pressed', 'true')
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(api.find('POST', /human-scores$/)).toHaveLength(1))
    expect((api.find('POST', /human-scores$/)[0].body as { scores: Array<{ raw_score: number }> }).scores[0].raw_score).toBe(3)
  })

  it('在备注输入框里按数字键不会打分', async () => {
    setup()
    await screen.findByTestId('annotate-case')
    await userEvent.type(screen.getByLabelText('human 备注'), '5')
    expect(screen.getByLabelText('human 备注')).toHaveValue('5')
    expect(screen.getByTestId('submit-score')).toBeDisabled()
  })

  it('后端拒绝时显示错误，仍停留在当前用例', async () => {
    mockApi({
      'GET /api/v1/evals/evr_1/items': { items: [item('evi_1', 'c001')], next_cursor: null },
      'GET /api/v1/evals/evr_1/items/evi_1': detail('evi_1', 'c001'),
      'POST /api/v1/evals/evr_1/human-scores': { __status: 400, body: { error: { code: 'VALIDATION_ERROR', message: '人工评分不合法', details: { errors: [{ path: '[0].raw_score', message: '必须在 [1, 5] 内' }] } } } },
    })
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <AnnotateView run={run()} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await screen.findByTestId('annotate-case')
    await userEvent.click(screen.getByTestId('score-btn-human-2'))
    await userEvent.click(screen.getByTestId('submit-score'))
    expect(await screen.findByRole('alert')).toHaveTextContent('人工评分不合法')
    expect(screen.getByRole('alert')).toHaveTextContent('必须在 [1, 5] 内')
    expect(screen.getByTestId('annotate-case')).toHaveTextContent('c001')
  })

  it('已评审的用例在「全部」里可以修改：沿用原分数', async () => {
    const done = item('evi_1', 'c001', { scores: [{ evaluator_key: 'human', evaluator_type: 'human', status: 'done', score: 0.75, raw_score: 4, passed: true, reason: '好' }], passed: true })
    setup(run(), [done])
    expect(await screen.findByTestId('annotate-empty')).toHaveTextContent('没有待评审')
    await userEvent.click(screen.getByTestId('mode-all'))
    await screen.findByTestId('annotate-case')
    expect(screen.getByTestId('score-btn-human-4')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('human 备注')).toHaveValue('好')
  })

  it('量程超过 11 档时用数字输入框，超出范围不能提交', async () => {
    setup(run({ evaluators: [{ key: 'human', type: 'human', gate: false, scale: [0, 100] }] }))
    await screen.findByTestId('annotate-case')
    const input = screen.getByLabelText('human 分数')
    await userEvent.type(input, '150')
    expect(screen.getByText('分数必须在 0 到 100 之间')).toBeInTheDocument()
    expect(screen.getByTestId('submit-score')).toBeDisabled()
    await userEvent.clear(input)
    await userEvent.type(input, '80')
    expect(screen.getByTestId('submit-score')).not.toBeDisabled()
  })

  it('没有配置人工评审评估器时给出说明', () => {
    mockApi({})
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <AnnotateView run={run({ evaluators: [{ key: 'exact', type: 'exact_match' }] })} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(screen.getByTestId('no-human')).toHaveTextContent('没有配置「人工评审」评估器')
  })
})
