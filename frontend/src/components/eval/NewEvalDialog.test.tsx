import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

it('逐轮中间件默认折叠，配置随预估和发起提交，修改配置使预估过期', async () => {
  const { api } = setup()
  await userEvent.click(await screen.findByTestId('pick-dataset'))
  const summary = screen.getByText('逐轮中间件（可选）')
  expect(summary.closest('details')).not.toHaveAttribute('open')
  await userEvent.click(summary)
  const plan = { '2': [{ id: 'json_artifact', version: '1.0.0', config: {} }] }
  fireEvent.change(screen.getByLabelText('中间件计划 JSON'), { target: { value: JSON.stringify(plan) } })
  await userEvent.click(screen.getByTestId('estimate-eval'))
  await screen.findByTestId('estimate-panel')
  expect(api.find('POST', /\/evals/)[0].body).toMatchObject({ turn_middlewares: plan })
  fireEvent.change(screen.getByLabelText('中间件计划 JSON'), { target: { value: '{}' } })
  expect(screen.getByTestId('estimate-panel')).toHaveAttribute('data-stale', 'true')
  await userEvent.click(screen.getByTestId('submit-eval'))
  await waitFor(() => expect(api.find('POST', /\/evals/)).toHaveLength(2))
  expect(api.find('POST', /\/evals/)[1].body).toMatchObject({ turn_middlewares: {} })
})

it('非法逐轮中间件 JSON 阻止预估和发起', async () => {
  const { api } = setup()
  await userEvent.click(await screen.findByTestId('pick-dataset'))
  await userEvent.click(screen.getByText('逐轮中间件（可选）'))
  fireEvent.change(screen.getByLabelText('中间件计划 JSON'), { target: { value: '[]' } })
  await userEvent.click(screen.getByTestId('estimate-eval'))
  await userEvent.click(screen.getByTestId('submit-eval'))
  expect(api.find('POST', /\/evals/)).toHaveLength(0)
  expect(screen.getByText(/逐轮中间件须为 JSON 对象/)).toBeInTheDocument()
})

function setup(testSelectable = false, versions: unknown[] = [version], defaults = ['ver_1']) {
  const api = mockApi({
    'GET /api/v1/adapter-environments': { production: {}, test: {}, test_selectable: testSelectable },
    'GET /api/v1/agents': { items: [agent], next_cursor: null },
    'GET /api/v1/agents/agt_1': agent,
    'GET /api/v1/agents/agt_1/versions': { items: versions, next_cursor: null },
    'GET /api/v1/assets': { items: [], next_cursor: null },
    'POST /api/v1/evals': (c: { url: string }) =>
      c.url.includes('dry_run=true') ? { dry_run: true, batch_id: null, runs: [], estimate } : { batch_id: 'evb_1', runs: [{ id: 'evr_9', version_id: 'ver_1', status: 'queued' }], estimate },
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <NewEvalDialog agentId="agt_1" defaultVersionIds={defaults} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { api, onClose }
}

describe('NewEvalDialog：适配器环境', () => {
  it('测试已配置且不同：默认生产（请求不带 environment），选「测试」后预估与发起都带 environment=test', async () => {
    const { api } = setup(true)
    await userEvent.click(await screen.findByTestId('pick-dataset'))
    expect(await screen.findByRole('radio', { name: '生产' })).toBeChecked()
    await userEvent.click(screen.getByTestId('estimate-eval'))
    await waitFor(() => expect(api.find('POST', /\/evals/)).toHaveLength(1))
    expect(api.find('POST', /\/evals/)[0].body).not.toHaveProperty('environment')
    await userEvent.click(screen.getByRole('radio', { name: '测试' }))
    await userEvent.click(screen.getByTestId('submit-eval'))
    await waitFor(() => expect(api.find('POST', /\/evals/)).toHaveLength(2))
    expect(api.find('POST', /\/evals/)[1].body).toMatchObject({ environment: 'test' })
  })

  it('测试未配置或与生产相同：不出现环境选项', async () => {
    setup(false)
    await screen.findByTestId('pick-dataset')
    expect(screen.queryByTestId('environment-switch')).toBeNull()
  })
})

describe('NewEvalDialog：版本归档', () => {
  const plain = { ...version, milestone: false, milestone_title: null, archived_into: null, archived_count: 0 }
  const list = [
    { ...plain, id: 'ver_3', seq: 3 },
    { ...plain, id: 'ver_2', seq: 2, milestone: true, milestone_title: '第一阶段', archived_count: 1 },
    { ...plain, id: 'ver_1', seq: 1, archived_into: 'ver_2' },
  ]

  it('默认只列里程碑（带标识）与未归档的版本，勾选「包括已归档」后才出现被归档的', async () => {
    setup(false, list, ['ver_3'])
    const picker = await screen.findByTestId('version-picker')
    await within(picker).findByTestId('ver-v3')
    expect(within(picker).getByTestId('ver-v2')).toHaveTextContent('★ 第一阶段')
    expect(within(picker).queryByTestId('ver-v1')).toBeNull()
    await userEvent.click(within(picker).getByTestId('include-archived'))
    expect(within(picker).getByTestId('ver-v1')).toBeInTheDocument()
  })

  it('预选的版本已归档时仍然显示（从版本页展开的归档版本发起评测）', async () => {
    setup(false, list, ['ver_1'])
    const picker = await screen.findByTestId('version-picker')
    expect(await within(picker).findByTestId('ver-v1')).toBeInTheDocument()
  })
})

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
