import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AdapterBinding } from '@/api/queries/adapters'
import { AdapterBindingFields } from './AdapterBindingFields'

vi.mock('@/api/queries/accounts', () => ({ usePlatformAccounts: () => ({ data: [
  { id: 'acc_trace', adapter_id: 'hiagent', name: 'Trace 账号', enabled: true, origin: 'https://trace.example' },
] }) }))

vi.mock('@/api/queries/adapters', () => ({
  useAdapters: () => ({ data: [{
    adapter: 'hiagent', major: 1, name: 'HiAgent', capabilities: ['streaming'],
    impl_digest: 'a'.repeat(64), sunset: null,
    config_schema: { required: ['app_id'], properties: {
      app_id: { type: 'string' }, timeout_s: { type: 'number', default: 300 },
    } },
  }] }),
  useAdapterCredentials: () => ({ data: [{ ref: 'adapter:test', name: '测试凭据', adapter_id: 'hiagent' }] }),
}))

function Form() {
  const [binding, setBinding] = useState<AdapterBinding | null>(null)
  return <>
    <AdapterBindingFields value={binding} onChange={setBinding} />
    <output data-testid="binding">{JSON.stringify(binding)}</output>
  </>
}

describe('适配器连接配置表单', () => {
  it('从注册表选择平台实现并保存凭据引用，取消选择时配置为空', async () => {
    const client = new QueryClient()
    render(<QueryClientProvider client={client}><Form /></QueryClientProvider>)
    await userEvent.selectOptions(screen.getByLabelText('平台接入'), 'hiagent@1')
    expect(screen.getByLabelText('timeout_s')).toHaveValue(300)
    await userEvent.type(screen.getByLabelText(/^app_id/), 'app-47')
    await userEvent.selectOptions(screen.getByLabelText(/^凭据引用/), 'adapter:test')
    const binding = JSON.parse(screen.getByTestId('binding').textContent!)
    expect(binding).toEqual({
      adapter: 'hiagent', major: 1, credential_ref: 'adapter:test',
      config: { app_id: 'app-47', timeout_s: 300 },
    })
    await userEvent.selectOptions(screen.getByLabelText('平台接入'), '')
    expect(screen.getByTestId('binding')).toHaveTextContent('null')
  })

  it('Trace 账号保存为配置引用，发布 API Key 引用保留', async () => {
    render(<QueryClientProvider client={new QueryClient()}><Form /></QueryClientProvider>)
    await userEvent.selectOptions(screen.getByLabelText('平台接入'), 'hiagent@1')
    await userEvent.selectOptions(screen.getByLabelText(/^凭据引用/), 'adapter:test')
    await userEvent.selectOptions(screen.getByLabelText('Trace 账号'), 'acc_trace')
    const binding = JSON.parse(screen.getByTestId('binding').textContent!)
    expect(binding.config.observe_account_ref).toBe('acc_trace')
    expect(binding.credential_ref).toBe('adapter:test')
  })
})
