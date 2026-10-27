// Synthetic fixtures authored by hand; no recorded agent data.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PackageDiff } from '@/api/types'
import { mockApi, type MockCall } from '@/test/mockApi'
import { PackageDiffView } from './PackageDiffView'

vi.mock('@/components/ui/DiffEditor', () => ({
  DiffEditor: ({ original, modified }: { original: string; modified: string }) => (
    <div data-testid="diff-editor">{original} → {modified}</div>
  ),
}))
afterEach(() => vi.unstubAllGlobals())

const diff: PackageDiff = {
  added: ['scripts/new.py'],
  removed: ['old.txt'],
  modified: [
    { path: 'SKILL.md', content_diff: { format: 'unified', text: '@@ -1 +1 @@\n-画一只猫\n+画一只狗' } },
    { path: 'scripts/run.py', content_diff: { format: 'unified', text: '@@ -1 +1 @@\n-a\n+b' } },
    { path: 'assets/logo.bin' },
  ],
  unchanged_count: 2,
  left_root: 'image-gen-0.0.1',
  right_root: 'image-gen-0.0.2',
}

function setup() {
  const api = mockApi({
    'GET /api/v1/assets/revisions/(arv_l|arv_r)/skill/file': ({ url }: MockCall) => {
      const u = new URL(url, 'http://test')
      const path = u.searchParams.get('path') ?? ''
      const side = u.pathname.includes('arv_l') ? '旧' : '新'
      return { path, content: `${side}:${path}`, content_available: true, size: 10 }
    },
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <PackageDiffView diff={diff} leftRevisionId="arv_l" rightRevisionId="arv_r" />
    </QueryClientProvider>,
  )
  return api
}

describe('Skill 文件包逐文件 diff', () => {
  it('带计数的新增 / 删除 / 修改列表，默认打开第一个修改的文件，按各侧顶层目录读取', async () => {
    const api = setup()
    expect(screen.getByRole('button', { name: /新增\s*1/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /删除\s*1/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /修改\s*3/ })).toBeInTheDocument()
    expect(screen.getByText('未变化 2 个文件')).toBeInTheDocument()
    expect(await screen.findByTestId('diff-editor')).toHaveTextContent('旧:image-gen-0.0.1/SKILL.md → 新:image-gen-0.0.2/SKILL.md')
    expect(api.find('GET', /arv_l\/skill\/file\?path=image-gen-0.0.1%2FSKILL.md/)).toHaveLength(1)
  })

  it('点击另一个修改的文件切换 diff；二进制文件只提示不请求内容', async () => {
    const api = setup()
    await userEvent.click(screen.getByRole('button', { name: 'scripts/run.py' }))
    expect(await screen.findByText(/旧:image-gen-0.0.1\/scripts\/run.py/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'assets/logo.bin' }))
    expect(await screen.findByText('二进制文件，无法逐行对比')).toBeInTheDocument()
    expect(api.find('GET', /logo\.bin/)).toHaveLength(0)
  })

  it('新增 / 删除页签只列出文件', async () => {
    setup()
    await userEvent.click(screen.getByRole('button', { name: /新增\s*1/ }))
    expect(screen.getByText('scripts/new.py')).toBeInTheDocument()
    expect(screen.queryByTestId('diff-editor')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /删除\s*1/ }))
    expect(screen.getByText('old.txt')).toBeInTheDocument()
  })
})
