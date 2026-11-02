import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Agent, VersionSummary } from '@/api/types'
import { mockApi } from '@/test/mockApi'
import { VersionsTab } from './VersionsTab'

vi.mock('@/stores/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))

afterEach(() => vi.unstubAllGlobals())

const agent = { id: 'agt_1', name: 'bot' } as Agent
const mk = (seq: number, extra: Partial<VersionSummary> = {}) =>
  ({
    id: `ver_${seq}`, seq, tags: [], spec_hash: 'h', notes: null, created_via: 'ui', created_at: '2026-11-01T00:00:00Z',
    milestone: false, milestone_title: null, archived_into: null, archived_count: 0, ...extra,
  }) as unknown as VersionSummary
const versions = [mk(2), mk(1)]

function setup(testSelectable: boolean, list: VersionSummary[] = versions) {
  const api = mockApi({
    'GET /api/v1/adapter-environments': { production: {}, test: {}, test_selectable: testSelectable },
    'POST /api/v1/sessions': { session: { id: 'ses_1' } },
    'POST /api/v1/compare': { id: 'cmp_1' },
    'POST /api/v1/versions/ver_\\d/milestone': { id: 'ver_5' },
    'POST /api/v1/versions/ver_\\d/unarchive': { id: 'ver_1' },
    'DELETE /api/v1/versions/ver_\\d/milestone': { id: 'ver_5' },
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <VersionsTab agent={agent} versions={list} hasMore={false} loadingMore={false} onLoadMore={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return api
}

describe('版本列表', () => {
  it('不再有按版本绑定接入的入口', () => {
    setup(false)
    expect(screen.queryByText('调用接入')).toBeNull()
    expect(screen.queryByText('批量修改接入')).toBeNull()
  })

  it('对话与双栏对话默认用生产；选「测试」后请求带 environment=test', async () => {
    const api = setup(true)
    await userEvent.click(screen.getByTestId('chat-v2'))
    await waitFor(() => expect(api.find('POST', /\/sessions$/)).toHaveLength(1))
    expect(api.find('POST', /\/sessions$/)[0].body).toEqual({ version_id: 'ver_2' })

    await userEvent.click(await screen.findByRole('radio', { name: '测试' }))
    await userEvent.click(screen.getByLabelText('选择 v1'))
    await userEvent.click(screen.getByLabelText('选择 v2'))
    await userEvent.click(screen.getByTestId('dual-chat'))
    await waitFor(() => expect(api.find('POST', /\/compare$/)).toHaveLength(1))
    expect(api.find('POST', /\/compare$/)[0].body).toMatchObject({ mode: 'live', environment: 'test' })
  })
})


describe('版本列表：阶段性成果与归档', () => {
  // v6 v5(★ 里程碑，归档了 v3 v4) v4 v3 v2(★ 更早的里程碑，归档了 v1) v1
  const list = [
    mk(6),
    mk(5, { milestone: true, milestone_title: '第二阶段：检索优化', archived_count: 2 }),
    mk(4, { archived_into: 'ver_5' }),
    mk(3, { archived_into: 'ver_5' }),
    mk(2, { milestone: true, milestone_title: '第一阶段：语气优化', archived_count: 1 }),
    mk(1, { archived_into: 'ver_2' }),
  ]
  const rowsShown = () => screen.getAllByTestId(/^version-row-/).map((r) => r.getAttribute('data-testid')!.slice(12))

  it('默认只显示里程碑与未归档的版本；里程碑显示阶段名与已归档个数', () => {
    setup(false, list)
    expect(rowsShown()).toEqual(['6', '5', '2'])
    expect(screen.getByTestId('milestone-badge-5')).toHaveTextContent('★ 第二阶段：检索优化')
    expect(screen.getByTestId('milestone-toggle-5')).toHaveTextContent('已归档 2 个版本')
    expect(screen.getByTestId('milestone-toggle-2')).toHaveTextContent('已归档 1 个版本')
  })

  it('点击里程碑的「已归档」展开 / 收起其下版本，展开的版本行可「移出归档」', async () => {
    const api = setup(false, list)
    await userEvent.click(screen.getByTestId('milestone-toggle-5'))
    expect(rowsShown()).toEqual(['6', '5', '4', '3', '2'])
    await userEvent.click(screen.getByTestId('more-v5'))
    expect(screen.queryByTestId('unarchive-v5')).toBeNull()
    await userEvent.click(screen.getByTestId('more-v4'))
    expect(screen.queryByTestId('set-milestone-v4')).toBeNull() // 已归档的不能再设为阶段性成果
    await userEvent.click(screen.getByTestId('unarchive-v4'))
    await waitFor(() => expect(api.find('POST', /\/versions\/ver_4\/unarchive$/)).toHaveLength(1))
    await userEvent.click(screen.getByTestId('milestone-toggle-5'))
    expect(rowsShown()).toEqual(['6', '5', '2'])
  })

  it('「显示全部（含已归档）」把所有里程碑展开；没有归档时不出现该开关', async () => {
    setup(false, list)
    await userEvent.click(screen.getByTestId('show-all-versions'))
    expect(rowsShown()).toEqual(['6', '5', '4', '3', '2', '1'])
  })

  it('没有任何归档时不显示「显示全部」开关', () => {
    setup(false)
    expect(screen.queryByTestId('show-all-versions')).toBeNull()
  })

  it('里程碑可全部移出归档、取消阶段性成果', async () => {
    const api = setup(false, list)
    await userEvent.click(screen.getByTestId('more-v5'))
    await userEvent.click(screen.getByTestId('unarchive-all-v5'))
    await waitFor(() => expect(api.find('POST', /\/versions\/ver_5\/unarchive$/)).toHaveLength(1))
    await userEvent.click(screen.getByTestId('more-v2'))
    await userEvent.click(screen.getByTestId('remove-milestone-v2'))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '取消阶段性成果' }))
    await waitFor(() => expect(api.find('DELETE', /\/versions\/ver_2\/milestone$/)).toHaveLength(1))
  })

  it('设为阶段性成果：弹窗默认勾选此前未归档的过程版本，提交时带阶段名和勾选的版本', async () => {
    const api = setup(false, [
      mk(5),
      mk(4, { milestone: true, milestone_title: '旧阶段', archived_count: 1 }),
      mk(3),
      mk(2),
      mk(1, { archived_into: 'ver_4' }),
    ])
    await userEvent.click(screen.getByTestId('more-v5'))
    await userEvent.click(screen.getByTestId('set-milestone-v5'))
    const box = await screen.findByTestId('milestone-candidates')
    // v4 是里程碑、v1 已归档：只有 v3 v2 可归档，默认全选
    expect(within(box).getAllByRole('checkbox').map((c) => c.getAttribute('aria-label'))).toEqual(['归档 v3', '归档 v2'])
    expect(within(box).getByLabelText('归档 v3')).toBeChecked()
    expect(within(box).getByLabelText('归档 v2')).toBeChecked()
    await userEvent.click(within(box).getByLabelText('归档 v2'))
    await userEvent.type(screen.getByLabelText('阶段名'), ' 第二阶段 ')
    await userEvent.click(screen.getByTestId('milestone-submit'))
    await waitFor(() => expect(api.find('POST', /\/versions\/ver_5\/milestone$/)).toHaveLength(1))
    expect(api.find('POST', /milestone$/)[0].body).toEqual({ title: '第二阶段', version_ids: ['ver_3'] })
  })
})
