import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Asset, AssetRevision } from '@/api/types'
import { mockApi } from '@/test/mockApi'
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
  description: null,
  latest_rev: 2,
  deleted_at: null,
  created_at: '2026-10-27T00:00:00.000Z',
  latest: rev(2, meta),
})

function setup(meta: AssetRevision['meta'], url = '/assets/ast_1') {
  mockApi({
    'GET /api/v1/assets/ast_1': asset(meta),
    'GET /api/v1/assets/ast_1/revisions': { items: [rev(2, meta), rev(1, meta)], next_cursor: null },
    'GET /api/v1/assets/revisions/arv_1': { ...rev(1, meta), kind: 'skill', name: 'crawl-engine', content: '{"steps": 3}', content_available: true },
    'GET /api/v1/assets/revisions/arv_2': { ...rev(2, meta), kind: 'skill', name: 'crawl-engine', content: '{"steps": 4}', content_available: true },
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
}

describe('AssetDetailPage：来自 agent 快照的资产只读', () => {
  it('隐藏新建修订 / 删除 / 彻底删除，显示说明；修订历史和对比仍可用', async () => {
    setup({ origin: 'snapshot' })
    expect(await screen.findByTestId('readonly-note')).toHaveTextContent('来自 agent 快照（只读），请在源平台修改后重新发布')
    expect(screen.queryByRole('button', { name: /新建修订/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^删除$/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /彻底删除/ })).not.toBeInTheDocument()
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
    expect(await screen.findByTestId('code')).toHaveTextContent('"steps": 3')
  })
})
