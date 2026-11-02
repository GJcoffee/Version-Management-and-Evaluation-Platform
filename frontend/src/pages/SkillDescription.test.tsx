// Synthetic fixtures authored by hand; no recorded agent data.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import type { Asset, SkillUpload } from '@/api/types'
import { mockApi } from '@/test/mockApi'
import AssetsPage from './AssetsPage'
import { SkillImportDialog } from './assets/SkillImportDialog'

afterEach(() => vi.unstubAllGlobals())

const asset: Asset = {
  id: 'ast_demo', kind: 'skill', name: 'demo', display_name: '示例 Skill', source_id: null,
  former_names: [], description: '将虚构文本整理为分镜，生成图片提示词。',
  latest_rev: 1, deleted_at: null, created_at: '2026-10-27T00:00:00Z', latest: null,
}
const upload: SkillUpload = {
  blob_upload_id: 'a'.repeat(64), size: 100, display_name: '示例 Skill',
  description: '将虚构文本整理为分镜，生成图片提示词。', suggested_name: 'demo', file_count: 1,
}

function show(element: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/assets?kind=skill']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        {element}
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

it('Skill 列表用独立描述列展示用途，悬停可读完整内容', async () => {
  mockApi({ 'GET /api/v1/assets': { items: [asset, { ...asset, id: 'ast_empty', name: 'empty', display_name: null, description: null }], next_cursor: null } })
  show(<AssetsPage />)
  const table = await screen.findByRole('table')
  expect(within(table).getByRole('columnheader', { name: '描述' })).toBeInTheDocument()
  expect(within(table).queryByRole('columnheader', { name: '摘要' })).not.toBeInTheDocument()
  expect(within(table).getByText(asset.description!)).toHaveAttribute('title', asset.description)
  expect(within(table).getByText('暂无描述')).toBeInTheDocument()
})

it.each([false, true])('导入时自动读取描述，编辑=%s 时保存补充用途', async (edit) => {
  const mocked = mockApi({
    'POST /api/v1/assets/skill/upload': upload,
    'POST /api/v1/assets': { asset, revision: { id: 'arv_demo', rev: 1 }, existed: false },
  })
  const onClose = vi.fn()
  show(<SkillImportDialog onClose={onClose} />)
  await userEvent.upload(screen.getByLabelText('选择 Skill ZIP'), new File(['synthetic'], 'demo.zip', { type: 'application/zip' }))
  const description = await screen.findByRole('textbox', { name: '描述' })
  expect(description).toHaveValue(upload.description)
  if (edit) {
    await userEvent.clear(description)
    await userEvent.type(description, '补充用途')
  }
  await userEvent.click(screen.getByRole('button', { name: '导入' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  const body = mocked.find('POST', /\/assets$/)[0].body as Record<string, unknown>
  expect(body.blob_upload_id).toBe(upload.blob_upload_id)
  if (edit) expect(body.description).toBe('补充用途')
  else expect(body).not.toHaveProperty('description')
})
