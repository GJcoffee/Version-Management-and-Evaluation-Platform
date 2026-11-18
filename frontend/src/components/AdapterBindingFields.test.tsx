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

vi.mock('@/api/queries/keyConnections', () => ({ useTraceKeyConnections: () => ({ data: [
  { id: 'key_trace', name: '观测密钥', enabled: true, status: 'active', origin: 'https://trace.example',
    top_url: 'https://top.example:30040', workspace_id: 'workspace' },
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

  it('选择密钥连接时填写对应站点与工作空间并清除网页登录引用', async () => {
    render(<QueryClientProvider client={new QueryClient()}><Form /></QueryClientProvider>)
    await userEvent.selectOptions(screen.getByLabelText('平台接入'), 'hiagent@1')
    await userEvent.selectOptions(screen.getByLabelText(/^凭据引用/), 'adapter:test')
    await userEvent.selectOptions(screen.getByLabelText('Trace 账号'), 'acc_trace')
    await userEvent.selectOptions(screen.getByLabelText('执行链路密钥连接'), 'key_trace')
    const binding = JSON.parse(screen.getByTestId('binding').textContent!)
    expect(binding.config).toMatchObject({ observe_key_ref: 'key_trace', observe_transport: 'hicli',
      observe_account_ref: null, observe_base_url: 'https://trace.example',
      observe_hicli_base_url: 'https://top.example:30040', workspace_id: 'workspace' })
    expect(binding.credential_ref).toBe('adapter:test')
    expect(screen.getByLabelText('Trace 账号')).toHaveValue('')
    await userEvent.selectOptions(screen.getByLabelText('Trace 账号'), 'acc_trace')
    const accountBinding = JSON.parse(screen.getByTestId('binding').textContent!)
    expect(accountBinding.config).toMatchObject({ observe_account_ref: 'acc_trace', observe_transport: 'web',
      observe_key_ref: null, observe_base_url: 'https://trace.example' })
    expect(accountBinding.credential_ref).toBe('adapter:test')
    expect(screen.getByLabelText('执行链路密钥连接')).toHaveValue('')
  })
})
