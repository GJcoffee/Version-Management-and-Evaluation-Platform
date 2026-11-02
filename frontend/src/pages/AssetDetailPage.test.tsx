// Synthetic fixtures authored by hand; no recorded agent data.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Asset, AssetRevision } from '@/api/types'
import { mockApi, type MockCall } from '@/test/mockApi'
import AssetDetailPage from './AssetDetailPage'

vi.mock('@/components/ui/CodeEditor', () => ({ CodeEditor: ({ value }: { value: string }) => <pre data-testid="code">{value}</pre> }))

afterEach(() => vi.unstubAllGlobals())

const rev = (n: number, meta: AssetRevision['meta'] = {}): AssetRevision => ({
  id: `arv_${n}`,
  asset_id: 'ast_1',
  rev: n,
  blob_hash: `h${n}`,
  content_type: 'application/json',
  size: 10,
  meta,
  message: null,
  created_at: '2026-10-27T00:00:00.000Z',
})
const asset = (meta: AssetRevision['meta']): Asset => ({
  id: 'ast_1',
  kind: 'skill',
  name: 'crawl-engine',
  display_name: null,
  source_id: null,
  former_names: [],
  description: null,
  latest_rev: 2,
  deleted_at: null,
  created_at: '2026-10-27T00:00:00.000Z',
  latest: rev(2, meta),
})

function setup(meta: AssetRevision['meta'], url = '/assets/ast_1', native = false, oldMeta = meta) {
  const fixture = asset(meta)
  const mocked = mockApi({
    'GET /api/v1/assets/ast_1': () => fixture,
    'GET /api/v1/assets/ast_1/revisions': { items: [rev(2, meta), rev(1, oldMeta)], next_cursor: null },
    'GET /api/v1/assets/revisions/arv_1': { ...rev(1, oldMeta), kind: 'skill', name: 'crawl-engine', content: '{"steps": 3}', content_available: true },
    'GET /api/v1/assets/revisions/arv_2': { ...rev(2, meta), kind: 'skill', name: 'crawl-engine', content: '{"steps": 4}', content_available: true },
    'PATCH /api/v1/assets/ast_1': ({ body }: MockCall) => {
      fixture.description = (body as { description: string }).description
      return fixture
    },
    ...(native ? {
      'GET /api/v1/assets/revisions/arv_1/skill/tree': { display_name: 'image-gen', file_count: 1, default_path: 'image-gen-1/SKILL.md', entries: [{ path: 'image-gen-1/SKILL.md', type: 'file', size: 30 }] },
      'GET /api/v1/assets/revisions/arv_2/skill/tree': { display_name: 'image-gen', file_count: 1, default_path: 'image-gen-2/SKILL.md', entries: [{ path: 'image-gen-2/SKILL.md', type: 'file', size: 30 }] },
      'GET /api/v1/assets/revisions/arv_1/skill/file': { path: 'image-gen-1/SKILL.md', content: '# 第一版文档', content_available: true, size: 30 },
      'GET /api/v1/assets/revisions/arv_2/skill/file': { path: 'image-gen-2/SKILL.md', content: '# 第二版文档', content_available: true, size: 30 },
    } : {}),
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/assets/:id" element={<AssetDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return mocked
}

describe('AssetDetailPage：来自 agent 快照的资产只读', () => {
  it('隐藏新建修订 / 删除 / 彻底删除，显示说明；修订历史和对比仍可用', async () => {
    setup({ origin: 'snapshot' })
    expect(await screen.findByTestId('readonly-note')).toHaveTextContent('来自 agent 快照（只读），请在源平台修改后重新发布')
    expect(screen.queryByRole('button', { name: /新建修订/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^删除$/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /彻底删除/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '编辑描述' })).not.toBeInTheDocument()
    expect(await screen.findByTestId('revision-list')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '修订对比' })).toBeInTheDocument()
  })

  it('普通资产保留编辑入口', async () => {
    setup({})
    expect(await screen.findByRole('button', { name: /新建修订/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /彻底删除/ })).toBeInTheDocument()
    expect(screen.queryByTestId('readonly-note')).not.toBeInTheDocument()
  })

  it('?rev=N 默认查看该修订的内容', async () => {
    setup({ origin: 'snapshot' }, '/assets/ast_1?rev=1')
    expect(await screen.findByText('缺少 Skill 原始文件')).toBeInTheDocument()
    expect(screen.queryByTestId('code')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载原始元数据' })).toHaveAttribute('href', '/api/v1/assets/revisions/arv_1/raw')
  })

  it('原始包展示原名，切换修订后打开当时的 SKILL.md 和目录', async () => {
    setup({ origin: 'snapshot', format: 'skill_package', display_name: 'image-gen' }, '/assets/ast_1', true)
    expect(await screen.findByRole('heading', { name: 'image-gen' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '第二版文档' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /r1/ }))
    expect(await screen.findByRole('heading', { name: '第一版文档' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'image-gen-1/SKILL.md' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: 'image-gen-2/SKILL.md' })).not.toBeInTheDocument()
  })

  it('查看历史修订显示当时的描述，缺失时不带入当前描述', async () => {
    setup({ origin: 'snapshot', description: '新版用途' }, '/assets/ast_1', false, { origin: 'snapshot', description: '旧版用途' })
    expect(await screen.findByTestId('skill-description')).toHaveTextContent('新版用途')
    await userEvent.click(await screen.findByRole('button', { name: /r1/ }))
    await waitFor(() => expect(screen.getByTestId('skill-description')).toHaveTextContent('旧版用途'))
    expect(screen.getByTestId('skill-description')).not.toHaveTextContent('新版用途')
  })

  it('旧修订缺少描述时显示暂无描述', async () => {
    setup({ origin: 'snapshot', description: '新版用途' }, '/assets/ast_1?rev=1', false, { origin: 'snapshot' })
    await screen.findByRole('link', { name: '下载原始元数据' })
    expect(screen.getByTestId('skill-description')).toHaveTextContent('暂无描述')
  })

  it('普通 Skill 保存描述后更新展示', async () => {
    const mocked = setup({})
    await userEvent.click(await screen.findByRole('button', { name: '编辑描述' }))
    await userEvent.type(screen.getByRole('textbox', { name: '描述' }), '用于生成虚构图片')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(screen.getByTestId('skill-description')).toHaveTextContent('用于生成虚构图片'))
    expect(mocked.find('PATCH', /\/assets\/ast_1/)[0].body).toEqual({ description: '用于生成虚构图片' })
  })
})

describe('AssetDetailPage：评测集用例详情', () => {
  afterEach(() => vi.restoreAllMocks())

  function setupDataset(expected: unknown, rubric: unknown) {
    // jsdom 没有布局，提供真实虚拟列表需要的容器尺寸。
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(448)
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000)
    const revision = rev(1, { case_count: 1 })
    const fixture = { ...asset({}), kind: 'dataset', latest_rev: 1, latest: revision }
    const api = mockApi({
      'GET /api/v1/assets/ast_1': fixture,
      'GET /api/v1/assets/ast_1/revisions': { items: [revision], next_cursor: null },
      'GET /api/v1/datasets/arv_1/cases': {
        items: [{
          id: 'cas_1', dataset_rev_id: 'arv_1', case_key: 'synthetic-case', idx: 0,
          input: { turns: ['第一个问题', '第二个问题'] }, expected, rubric,
          tags: ['p0'], metadata: { variables: { style: '简洁' } }, case_hash: 'h1',
        }],
        next_cursor: null,
      },
    })
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/assets/ast_1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes><Route path="/assets/:id" element={<AssetDetailPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    return api
  }

  it('对象 expected 和数组 rubric 可在列表和详情显示，不崩溃、不修改用例', async () => {
    const expected = { outputType: 'confirm', only_missing_parameter: 'dialogue' }
    const rubric = [{ criterion: '完整', weight: 1 }, '准确']
    const api = setupDataset(expected, rubric)
    const row = await screen.findByRole('button', { name: /synthetic-case/ })
    expect(row).toHaveTextContent(JSON.stringify(expected))
    expect(within(row).getByText(JSON.stringify(expected))).toHaveAttribute('title', JSON.stringify(expected))
    await userEvent.click(row)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('expected').closest('div')?.querySelector('p')?.textContent).toBe(JSON.stringify(expected, null, 2))
    expect(within(dialog).getByText('rubric').closest('div')?.querySelector('p')?.textContent).toBe(JSON.stringify(rubric, null, 2))
    expect(dialog).toHaveTextContent('第一个问题')
    expect(dialog).toHaveTextContent('第二个问题')
    expect(dialog).toHaveTextContent('简洁')
    expect(api.calls.every((c) => c.method === 'GET')).toBe(true)
  })

  it.each([
    { expected: '第一行\n第二行', rubric: '评分标准', shown: '第一行 第二行', rubricShown: '评分标准' },
    { expected: ['答案 A', { answer: 'B' }], rubric: {}, shown: '["答案 A",{"answer":"B"}]', rubricShown: '{}' },
    { expected: false, rubric: 0, shown: 'false', rubricShown: '0' },
    { expected: null, rubric: null, shown: '-', rubricShown: '-' },
  ])('兼容文本、数组、布尔值和空值：$shown', async ({ expected, rubric, shown, rubricShown }) => {
    setupDataset(expected, rubric)
    const row = await screen.findByRole('button', { name: /synthetic-case/ })
    expect(row).toHaveTextContent(shown)
    await userEvent.click(row)
    const dialog = screen.getByRole('dialog')
    const rubricField = within(dialog).getByText('rubric').closest('div')!
    expect(rubricField).toHaveTextContent(rubricShown)
    expect(dialog).not.toHaveTextContent('[object Object]')
  })
})
