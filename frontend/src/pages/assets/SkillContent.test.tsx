// Synthetic fixtures authored by hand; no recorded agent data.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi, type MockCall } from '@/test/mockApi'
import { SkillContent } from './SkillContent'

vi.mock('@/components/ui/CodeEditor', () => ({ CodeEditor: ({ value, readOnly }: { value: string; readOnly?: boolean }) => <pre data-testid="code" data-readonly={readOnly}>{value}</pre> }))
afterEach(() => vi.unstubAllGlobals())

function setup() {
  const api = mockApi({
    'GET /api/v1/assets/revisions/arv_1/skill/tree': {
      format: 'skill_package', display_name: 'image-gen', file_count: 3, default_path: 'image-gen-0.0.1/SKILL.md',
      entries: [
        { path: 'image-gen-0.0.1/SKILL.md', type: 'file', size: 50 },
        { path: 'image-gen-0.0.1/scripts/deep/run.py', type: 'file', size: 20 },
        { path: 'image-gen-0.0.1/references/empty', type: 'directory', size: 0 },
        { path: 'image-gen-0.0.1/assets/data.bin', type: 'file', size: 256 },
      ],
    },
    'GET /api/v1/assets/revisions/arv_1/skill/file': ({ url }: MockCall) => {
      const path = new URL(url, 'http://test').searchParams.get('path') ?? ''
      const content = path.endsWith('SKILL.md') ? '---\nname: image-gen\n---\n# 原始说明' : path.endsWith('.py') ? "print('原始脚本')\r\n" : null
      return { path, content, content_available: content !== null, size: content?.length ?? 256 }
    },
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><SkillContent revisionId="arv_1" packaged /></QueryClientProvider>)
  return api
}

describe('Skill 原始文件浏览', () => {
  it('默认 SKILL.md，保留根目录 / 深层路径 / 空目录，文本只读，二进制提供下载', async () => {
    const api = setup()
    expect(await screen.findByRole('heading', { name: '原始说明' })).toBeInTheDocument()
    expect(screen.getByText('image-gen-0.0.1')).toBeInTheDocument()
    expect(screen.getByText('empty')).toBeInTheDocument()
    const script = screen.getByRole('button', { name: 'image-gen-0.0.1/scripts/deep/run.py' })
    await userEvent.click(script)
    expect(await screen.findByTestId('code')).toHaveTextContent("print('原始脚本')")
    expect(screen.getByTestId('code')).toHaveAttribute('data-readonly', 'true')
    expect(script).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'image-gen-0.0.1/assets/data.bin' }))
    expect(await screen.findByText('此文件无法预览')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载原文件' })).toHaveAttribute('href', '/api/v1/assets/revisions/arv_1/skill/file?path=image-gen-0.0.1%2Fassets%2Fdata.bin&download=true')
    expect(api.find('GET', /path=image-gen-0.0.1%2Fscripts%2Fdeep%2Frun.py/)).toHaveLength(1)
  })

  it('可以查看 SKILL.md 原始 frontmatter', async () => {
    setup()
    await screen.findByRole('heading', { name: '原始说明' })
    await userEvent.click(screen.getByRole('button', { name: '源文件' }))
    expect(await screen.findByTestId('code')).toHaveTextContent('name: image-gen')
  })
})
