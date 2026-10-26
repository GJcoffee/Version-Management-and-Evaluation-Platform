import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '@/test/mockApi'
import { NewEvalDialog } from './NewEvalDialog'

vi.mock('@/stores/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))
// 数据集选择器本身有自己的测试：这里换成一个直接给出引用的按钮
vi.mock('@/components/AssetRefPicker', () => ({
  AssetRefPicker: ({ onChange }: { onChange: (r: string) => void }) => (
    <button type="button" data-testid="pick-dataset" onClick={() => onChange('asset://dataset/ds@1')}>
      选数据集
    </button>
  ),
}))
vi.mock('@/api/queries/providers', () => ({ useProviders: () => ({ data: [], isSuccess: true }) }))

afterEach(() => vi.unstubAllGlobals())

const agent = { id: 'agt_1', name: 'bot', kind: 'config', description: null, archived: false, created_at: '', updated_at: '', latest_version: null, tags: [] }
const version = { id: 'ver_1', agent_id: 'agt_1', seq: 1, spec_hash: 'h', source: 'draft', parent_version_id: null, from_draft_id: null, notes: null, created_via: 'ui', created_at: '', tags: [] }
const estimate = {
  items: 8,
  cached_items: 0,
  tokens: 4000,
  cost: 0.5,
  gross_tokens: 4000,
  gross_cost: 0.5,
  cost_known: true,
  basis: 'heuristic',
  exceeds_budget: true,
  per_version: [],
}

function setup() {
  const api = mockApi({
    'GET /api/v1/agents': { items: [agent], next_cursor: null },
    'GET /api/v1/agents/agt_1': agent,
    'GET /api/v1/agents/agt_1/versions': { items: [version], next_cursor: null },
    'GET /api/v1/assets': { items: [], next_cursor: null },
    'POST /api/v1/evals': (c: { url: string }) =>
      c.url.includes('dry_run=true') ? { dry_run: true, batch_id: null, runs: [], estimate } : { batch_id: 'evb_1', runs: [{ id: 'evr_9', version_id: 'ver_1', status: 'queued' }], estimate },
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <NewEvalDialog agentId="agt_1" defaultVersionIds={['ver_1']} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { api, onClose }
}

describe('NewEvalDialog：预算与成本预估', () => {
  it('预估成本走 dry_run，带上预算，显示预估并提示超出预算；不创建评测', async () => {
    const { api } = setup()
    await userEvent.click(await screen.findByTestId('pick-dataset'))
    await userEvent.type(screen.getByLabelText('成本上限（元）'), '0.2')
    await userEvent.type(screen.getByLabelText('tokens 上限'), '100000')
    await userEvent.click(screen.getByTestId('estimate-eval'))
    expect(await screen.findByTestId('estimate-panel')).toBeInTheDocument()
    expect(screen.getByTestId('estimate-exceeds')).toBeInTheDocument()
    const posts = api.find('POST', /\/evals/)
    expect(posts).toHaveLength(1)
    expect(posts[0].url).toContain('dry_run=true')
    expect(posts[0].body).toMatchObject({
      version_ids: ['ver_1'],
      dataset: 'asset://dataset/ds@1',
      budget: { max_cost: 0.2, max_tokens: 100000 },
      via: 'ui',
    })
  })

  it('改动配置后预估标记为过期', async () => {
    setup()
    await userEvent.click(await screen.findByTestId('pick-dataset'))
    await userEvent.click(screen.getByTestId('estimate-eval'))
    await screen.findByTestId('estimate-panel')
    expect(screen.getByTestId('estimate-panel')).not.toHaveAttribute('data-stale')
    await userEvent.clear(screen.getByLabelText('重复次数 repeats'))
    await userEvent.type(screen.getByLabelText('重复次数 repeats'), '3')
    expect(screen.getByTestId('estimate-panel')).toHaveAttribute('data-stale', 'true')
  })

  it('没有设置预算时请求体不带 budget；预算非法时不发请求', async () => {
    const { api } = setup()
    await userEvent.click(await screen.findByTestId('pick-dataset'))
    await userEvent.type(screen.getByLabelText('tokens 上限'), '1.5')
    await userEvent.click(screen.getByTestId('estimate-eval'))
    expect(screen.getByText('请输入不小于 0 的整数')).toBeInTheDocument()
    expect(api.find('POST', /\/evals/)).toHaveLength(0)
    await userEvent.clear(screen.getByLabelText('tokens 上限'))
    await userEvent.click(screen.getByTestId('estimate-eval'))
    await waitFor(() => expect(api.find('POST', /\/evals/)).toHaveLength(1))
    expect('budget' in (api.find('POST', /\/evals/)[0].body as object) && (api.find('POST', /\/evals/)[0].body as { budget?: unknown }).budget).toBeFalsy()
  })

  it('正式发起不带 dry_run 参数，并带上预算', async () => {
    const { api, onClose } = setup()
    await userEvent.click(await screen.findByTestId('pick-dataset'))
    await userEvent.type(screen.getByLabelText('成本上限（元）'), '2')
    await userEvent.click(screen.getByTestId('submit-eval'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const post = api.find('POST', /\/evals/)[0]
    expect(post.url).not.toContain('dry_run')
    expect(post.body).toMatchObject({ budget: { max_cost: 2 } })
  })
})
