import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import type { AdapterBinding, AdapterEnvironments, EnvironmentRevision, EnvironmentState } from '@/api/queries/adapters'
import { AdapterSection } from './AdapterSection'

const binding = (published: string): AdapterBinding => ({
  adapter: 'hiagent', major: 1, credential_ref: 'adapter:key',
  config: { base_url: 'https://hiagent.example', app_id: 'app-1', user_id: 'u', published_version: published },
})
const state = (environment: 'production' | 'test', revision: number, published: string | null): EnvironmentState => ({
  environment, configured: published !== null, revision, binding: published ? binding(published) : null,
  fingerprint: published ? `fp-${published}` : null, source: published ? 'push' : null,
  reason: published ? `说明 ${published}` : null, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
})
const revision = (n: number, published: string, source: EnvironmentRevision['source'], current = false): EnvironmentRevision => ({
  revision: n, current, binding: binding(published), fingerprint: `fp-${published}`, source,
  reason: `说明 ${published}`, origin: null, created_at: '2026-11-01T00:00:00Z',
})

function setup(envs: AdapterEnvironments, histories?: { production: EnvironmentRevision[]; test: EnvironmentRevision[] }) {
  const get = vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/adapter-environments') return envs as never
    if (path === '/adapter-environments/production/revisions') {
      return (histories?.production ?? [revision(2, '48', 'push', true), revision(1, '47', 'migration')]) as never
    }
    if (path === '/adapter-environments/test/revisions') return (histories?.test ?? [revision(3, '49', 'edit', true)]) as never
    if (path === '/adapters') return [{
      adapter: 'hiagent', major: 1, name: 'HiAgent', capabilities: ['streaming'],
      impl_digest: 'a'.repeat(64), sunset: null,
      config_schema: { properties: { published_version: { type: 'string' } } },
    }] as never
    if (path === '/adapters/credentials') return [{ ref: 'adapter:key', name: 'key', adapter_id: 'hiagent' }] as never
    if (path === '/adapters/accounts') return [] as never
    throw new Error(`unexpected GET ${path}`)
  })
  const change = { applied: true, environment: 'production', before: envs.production, target: envs.production }
  const post = vi.spyOn(api, 'post').mockResolvedValue(change as never)
  const put = vi.spyOn(api, 'put').mockResolvedValue({ ...change, environment: 'test' } as never)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AdapterSection />
  </QueryClientProvider>)
  return { get, post, put }
}

const differing: AdapterEnvironments = {
  production: state('production', 2, '48'), test: state('test', 3, '49'), test_selectable: true,
}

describe('设置 · 适配器', () => {
  afterEach(() => vi.restoreAllMocks())

  it('历史乱序时默认取最大存档号，生产与测试选择独立', async () => {
    setup(differing, {
      production: [revision(1, '47', 'migration'), revision(2, '48', 'push', true)],
      test: [revision(2, '48', 'edit'), revision(3, '49', 'edit', true)],
    })
    const production = await screen.findByTestId('env-history-production')
    const test = screen.getByTestId('env-history-test')
    await within(production).findByText('当前')
    expect(within(production).getAllByRole('row')[1]).toHaveTextContent('#2')
    expect(within(test).getAllByRole('row')[1]).toHaveTextContent('#3')
    await userEvent.click(within(production).getByRole('button', { name: /生产存档历史/ }))
    await userEvent.selectOptions(within(production).getByLabelText('选择生产存档'), '1')
    expect(within(test).getByRole('button')).toHaveAttribute('aria-expanded', 'false')
    expect(within(test).getAllByRole('row')[1]).toHaveTextContent('#3')
    await userEvent.click(within(test).getByRole('button'))
    await userEvent.selectOptions(within(test).getByLabelText('选择测试存档'), '2')
    expect(within(production).getAllByRole('row')[1]).toHaveTextContent('#1')
  })

  it('没有存档时显示空状态，展开也不出现选择器', async () => {
    setup(differing, { production: [], test: [] })
    const history = await screen.findByTestId('env-history-production')
    expect(await within(history).findByText('还没有存档。')).toBeInTheDocument()
    await userEvent.click(within(history).getByRole('button'))
    expect(within(history).queryByRole('combobox')).toBeNull()
  })

  it('生产 / 测试两张卡片显示适配器、关键配置、存档号，下面是两个环境的存档历史', async () => {
    setup(differing)
    const production = await screen.findByTestId('env-card-production')
    expect(within(production).getByTestId('env-revision-production')).toHaveTextContent('存档 #2')
    expect(production).toHaveTextContent('HiAgent · 实现 v1 · 发布版本 48 · app app-1')
    expect(production).toHaveTextContent('https://hiagent.example')
    expect(screen.getByTestId('env-card-test')).toHaveTextContent('发布版本 49')
    const history = await screen.findByTestId('env-history-production')
    await within(history).findByText('推送')
    expect(within(history).getByText('当前')).toBeInTheDocument()
    expect(within(history).getAllByRole('row')).toHaveLength(2)
    expect(within(history).queryByText('迁移')).toBeNull()
    const toggle = within(history).getByRole('button', { name: /生产存档历史/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    await userEvent.selectOptions(within(history).getByLabelText('选择生产存档'), '1')
    expect(within(history).getByText('迁移')).toBeInTheDocument()
    expect(within(history).queryByText('当前')).toBeNull()
    expect(within(history).getAllByRole('row')).toHaveLength(2)
    await userEvent.click(toggle)
    expect(within(history).queryByLabelText('选择生产存档')).toBeNull()
    expect(within(history).getByText('当前')).toBeInTheDocument()
    expect(within(history).queryByText('迁移')).toBeNull()
    expect(await within(screen.getByTestId('env-history-test')).findByText('编辑')).toBeInTheDocument()
  })

  it('测试可编辑：保存带上当前存档号和说明，只改测试', async () => {
    const { put } = setup(differing)
    await userEvent.click(await screen.findByTestId('edit-test'))
    const editor = screen.getByTestId('test-editor')
    const published = within(editor).getByLabelText('published_version')
    await userEvent.clear(published)
    await userEvent.type(published, '50')
    expect(within(editor).getByRole('button', { name: '保存到测试' })).toBeDisabled()
    await userEvent.type(within(editor).getByLabelText(/^存档说明/), '升级发布版本')
    await userEvent.click(within(editor).getByRole('button', { name: '保存到测试' }))
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1))
    expect(put.mock.calls[0][0]).toBe('/adapter-environments/test')
    const body = put.mock.calls[0][1]?.body as { binding: AdapterBinding; expected_revision: number; reason: string }
    expect(body.expected_revision).toBe(3)
    expect(body.reason).toBe('升级发布版本')
    expect(body.binding.config.published_version).toBe('50')
    await waitFor(() => expect(screen.queryByTestId('test-editor')).toBeNull())
  })

  it('推送到生产：确认后带上验证过的测试存档号', async () => {
    const { post } = setup(differing)
    await userEvent.click(await screen.findByTestId('open-push'))
    await userEvent.type(screen.getByLabelText('备注'), '验证通过')
    await userEvent.click(screen.getByTestId('confirm-push'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(post.mock.calls[0][0]).toBe('/adapter-environments/production/push')
    expect(post.mock.calls[0][1]?.body).toEqual({ test_revision: 3, reason: '验证通过' })
  })

  it('测试未配置或与生产相同时不能推送', async () => {
    setup({ production: state('production', 2, '48'), test: state('test', 0, null), test_selectable: false })
    expect(await screen.findByTestId('open-push')).toBeDisabled()
    expect(screen.getByTestId('env-card-test')).toHaveTextContent('尚未配置')
    expect(screen.getByTestId('edit-test')).toHaveTextContent('配置')
  })

  it('生产回滚：选择历史存档，调用回滚接口', async () => {
    const { post } = setup(differing)
    await userEvent.click(await screen.findByTestId('open-rollback'))
    const target = await screen.findByLabelText('回到存档')
    expect(within(target).getAllByRole('option')).toHaveLength(1)  // 当前存档不能作为回滚目标
    await userEvent.click(screen.getByTestId('confirm-rollback'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(post.mock.calls[0][0]).toBe('/adapter-environments/production/rollback')
    expect(post.mock.calls[0][1]?.body).toEqual({ revision: 1, reason: undefined })
  })
})
