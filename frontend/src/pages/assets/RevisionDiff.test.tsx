// Synthetic fixtures authored by hand; no recorded agent data.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '@/test/mockApi'
import { RevisionDiff } from './RevisionDiff'

vi.mock('@/components/ui/DiffEditor', () => ({ DiffEditor: ({ modified }: { modified: string }) => <div data-testid="diff-editor">{modified}</div> }))
afterEach(() => vi.unstubAllGlobals())

describe('资产修订对比：Skill 文件包', () => {
  it('显示逐文件变化而不是整包对比', async () => {
    mockApi({
      'GET /api/v1/assets/diff': {
        kind: 'skill', type: 'skill_package', identical: false, content_diff: null,
        package_diff: {
          added: [], removed: ['old.txt'], modified: [{ path: 'SKILL.md', content_diff: { format: 'unified', text: '@@' } }],
          unchanged_count: 4, left_root: 'image-gen-0.0.1', right_root: 'image-gen-0.0.2',
        },
      },
      'GET /api/v1/assets/revisions/arv_r/skill/file': { path: 'x', content: '画一只狗', content_available: true, size: 12 },
      'GET /api/v1/assets/revisions/arv_l/skill/file': { path: 'x', content: '画一只猫', content_available: true, size: 12 },
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><RevisionDiff kind="skill" leftId="arv_l" rightId="arv_r" /></QueryClientProvider>)
    expect(await screen.findByTestId('package-diff')).toBeInTheDocument()
    expect(screen.getByText('未变化 4 个文件')).toBeInTheDocument()
    expect(await screen.findByTestId('diff-editor')).toHaveTextContent('画一只狗')
  })
})
