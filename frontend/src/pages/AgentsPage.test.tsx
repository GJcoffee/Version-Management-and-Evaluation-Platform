import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Agent } from '@/api/types'
import { TagsCell } from './AgentsPage'

const agent = (tags: string[]): Agent => ({
  id: 'agt_1',
  name: 'bot',
  kind: 'config',
  description: null,
  archived: false,
  created_at: '2026-10-25T08:00:00.000Z',
  updated_at: '2026-10-25T08:00:00.000Z',
  latest_version: null,
  tags,
  conventions: { tag_from_param: null, require_notes: false, require_source_id: [], snapshot_readonly: false },
})

describe('TagsCell', () => {
  it('直接显示列表里的 tags（不再逐行请求 /agents/{id}/tags，所以不需要 QueryClient）', () => {
    render(<TagsCell agent={agent(['prod', 'stable'])} />)
    expect(screen.getByText('prod')).toBeInTheDocument()
    expect(screen.getByText('stable')).toBeInTheDocument()
  })
  it('没有标签显示占位；超过 4 个折叠为 +N', () => {
    const { rerender } = render(<TagsCell agent={agent([])} />)
    expect(screen.getByText('-')).toBeInTheDocument()
    rerender(<TagsCell agent={agent(['a', 'b', 'c', 'd', 'e', 'f'])} />)
    expect(screen.getByText('+2')).toBeInTheDocument()
    expect(screen.queryByText('e')).not.toBeInTheDocument()
  })
})
