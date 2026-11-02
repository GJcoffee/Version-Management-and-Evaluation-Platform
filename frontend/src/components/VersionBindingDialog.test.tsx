import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { VersionBindingDialog } from './VersionBindingDialog'

describe('已有版本覆盖检查', () => {
  afterEach(() => vi.restoreAllMocks())

  it('检查覆盖不刷新当前绑定或丢弃候选配置，执行后固定新修订', async () => {
    const get = vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/adapters') return [{
        id: 'hiagent', name: 'HiAgent', version: '1.0.0', capabilities: ['streaming'],
        config_schema: { properties: { app_id: { type: 'string' } } },
      }] as never
      if (path === '/adapters/credentials') return [{
        ref: 'adapter:key', name: 'key', adapter_id: 'hiagent',
      }] as never
      return { version_id: 'ver_1', current: { revision: 0, binding: null },
        history: [{ revision: 0, binding: null }] } as never
    })
    const post = vi.spyOn(api, 'post').mockImplementation(async (_path, options) => {
      const body = options?.body as { dry_run: boolean }
      return [{ version_id: 'ver_1', applied: !body.dry_run }] as never
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}>
      <VersionBindingDialog ids={['ver_1']} onClose={() => undefined} />
    </QueryClientProvider>)
    await screen.findByRole('option', { name: 'HiAgent · 1.0.0' })
    await userEvent.selectOptions(screen.getByLabelText('调用接入'), 'hiagent@1.0.0')
    await userEvent.type(screen.getByLabelText('app_id'), 'candidate')
    await userEvent.selectOptions(screen.getByLabelText(/^凭据引用/), 'adapter:key')
    await userEvent.type(screen.getByLabelText(/^修改原因/), 'test')
    await userEvent.click(screen.getByText(/已检查旧入口/))
    const before = get.mock.calls.filter(([path]) => path.includes('adapter-binding')).length
    await userEvent.click(screen.getByRole('button', { name: '检查覆盖' }))
    await screen.findByText(/检查通过/)
    expect(screen.getByLabelText('app_id')).toHaveValue('candidate')
    expect(get.mock.calls.filter(([path]) => path.includes('adapter-binding'))).toHaveLength(before)
    await userEvent.click(screen.getByRole('button', { name: '执行覆盖' }))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2))
    const body = post.mock.calls[1][1]?.body as { binding: { config: { app_id: string } } }
    expect(body.binding.config.app_id).toBe('candidate')
    expect(screen.getByRole('button', { name: '执行覆盖' })).toBeDisabled()
  })
})
