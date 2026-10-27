import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Agent, VersionAssetItem, VersionSummary } from '@/api/types'
import { mockApi } from '@/test/mockApi'
import { AssetsTab } from './AssetsTab'

afterEach(() => vi.unstubAllGlobals())

const agent = (kind: Agent['kind']): Agent => ({
  id: 'agt_1',
  name: 'bot',
  kind,
  description: null,
  archived: false,
  created_at: '',
  updated_at: '',
  latest_version: null,
  tags: [],
  conventions: { tag_from_param: null, require_notes: false, require_source_id: [], snapshot_readonly: false },
})
const ver = (seq: number, tags: string[] = []): VersionSummary => ({
  id: `ver_${seq}`,
  agent_id: 'agt_1',
  seq,
  spec_hash: 'h',
  source: {},
  parent_version_id: null,
  from_draft_id: null,
  notes: null,
  created_via: 'cli',
  created_at: '',
  tags,
})
const asset = (kind: VersionAssetItem['kind'], name: string, over: Partial<VersionAssetItem> = {}): VersionAssetItem => ({
  asset_id: `ast_${name}`,
  kind,
  name,
  display_name: null,
  source_id: null,
  rev: 1,
  asset_revision_id: `arv_${name}`,
  spec_paths: [],
  origin: 'snapshot',
  changed_in_seq: 1,
  changed_in_version_id: 'ver_1',
  previous_rev: null,
  ...over,
})

const ITEMS_V2 = [
  asset('skill', 'crawl-engine', { rev: 4, previous_rev: 3, changed_in_seq: 2, changed_in_version_id: 'ver_2' }),
  asset('mcp', 'search'),
  asset('kb_config', 'bot-knowledge-base'),
  asset('database', 'bot-database'),
  asset('app_config', 'bot-app-config'),
  asset('prompt', 'bot-system'),
  asset('file', 'snap-bot-logo'),
]

function setup(kind: Agent['kind'], versions = [ver(2, ['prod']), ver(1)]) {
  const api = mockApi({
    'GET /api/v1/versions/ver_2/assets': { version: versions[0], items: ITEMS_V2 },
    'GET /api/v1/versions/ver_1/assets': { version: ver(1), items: [asset('skill', 'crawl-engine', { rev: 3 })] },
  })
  const onEditDraft = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AssetsTab agent={agent(kind)} versions={versions} onEditDraft={onEditDraft} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { api, onEditDraft }
}

describe('AssetsTab', () => {
  it('默认显示最新版本，资产按类型分组，每项显示修订、最近变化与只读标记', async () => {
    const { api } = setup('code')
    const skill = await screen.findByTestId('asset-group-skill')
    expect(api.find('GET', /\/versions\/ver_2\/assets/)).toHaveLength(1)
    const link = within(skill).getByRole('link', { name: 'crawl-engine' })
    expect(link).toHaveAttribute('href', '/assets/ast_crawl-engine?rev=4')
    expect(within(skill).getByText('r4')).toBeInTheDocument()
    expect(within(skill).getByText('v2')).toBeInTheDocument()
    expect(within(skill).getByText('r3 → r4')).toBeInTheDocument()
    expect(within(skill).getByText('只读')).toBeInTheDocument()
    for (const key of ['prompt', 'mcp', 'knowledge', 'database', 'app_config', 'other']) {
      expect(screen.getByTestId(`asset-group-${key}`)).toBeInTheDocument()
    }
    // 知识库组里区分知识库配置
    expect(within(screen.getByTestId('asset-group-knowledge')).getByText('知识库配置')).toBeInTheDocument()
    expect(within(screen.getByTestId('asset-group-mcp')).getByText('r1（首次出现）')).toBeInTheDocument()
    expect(screen.getByText(/来自 agent 快照（只读），请在源平台修改后重新发布/)).toBeInTheDocument()
    // 代码型 agent 没有草稿编辑入口
    expect(screen.queryByRole('button', { name: /在草稿中编辑/ })).not.toBeInTheDocument()
  })

  it('有来源信息的资产显示原始名称（可含中文）、资产名与来源 ID', async () => {
    const versions = [ver(1)]
    mockApi({
      'GET /api/v1/versions/ver_1/assets': {
        version: versions[0],
        items: [asset('skill', 'crawl-engine', { display_name: '爬虫引擎', source_id: 'sk_123' })],
      },
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <AssetsTab agent={agent('code')} versions={versions} onEditDraft={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const link = await screen.findByRole('link', { name: '爬虫引擎' })
    expect(link).toHaveAttribute('href', '/assets/ast_crawl-engine?rev=1')
    const identity = screen.getByTestId('asset-identity')
    expect(identity).toHaveTextContent('crawl-engine')
    expect(identity).toHaveTextContent('ID sk_123')
  })

  it('切换版本后显示该版本绑定的资产', async () => {
    setup('code')
    await screen.findByTestId('asset-group-skill')
    await userEvent.selectOptions(screen.getByLabelText('选择版本'), 'ver_1')
    await waitFor(() => expect(screen.queryByTestId('asset-group-mcp')).not.toBeInTheDocument())
    expect(within(screen.getByTestId('asset-group-skill')).getByText('r3')).toBeInTheDocument()
  })

  it('配置型 agent 保留编辑入口', async () => {
    const { onEditDraft } = setup('config', [ver(1)])
    await screen.findByText('crawl-engine')
    await userEvent.click(screen.getByRole('button', { name: /在草稿中编辑/ }))
    expect(onEditDraft).toHaveBeenCalled()
  })

  it('没有版本时给出提示', () => {
    setup('config', [])
    expect(screen.getByText('还没有发布过版本')).toBeInTheDocument()
  })
})
