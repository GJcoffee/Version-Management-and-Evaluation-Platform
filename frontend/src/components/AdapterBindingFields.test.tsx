import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AdapterBinding } from '@/api/queries/adapters'
import { AdapterBindingFields } from './AdapterBindingFields'

vi.mock('@/api/queries/adapters', () => ({
  useAdapters: () => ({ data: [{
    id: 'hiagent', name: 'HiAgent', version: '1.0.0', capabilities: ['streaming'],
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

describe('统一调用接入表单', () => {
  it('从注册表选择具体版本并保存凭据引用，清空时恢复原运行方式', async () => {
    const client = new QueryClient()
    render(<QueryClientProvider client={client}><Form /></QueryClientProvider>)
    await userEvent.selectOptions(screen.getByLabelText('调用接入'), 'hiagent@1.0.0')
    expect(screen.getByLabelText('timeout_s')).toHaveValue(300)
    await userEvent.type(screen.getByLabelText(/^app_id/), 'app-47')
    await userEvent.selectOptions(screen.getByLabelText(/^凭据引用/), 'adapter:test')
    const binding = JSON.parse(screen.getByTestId('binding').textContent!)
    expect(binding).toEqual({
      id: 'hiagent', version: '1.0.0', credential_ref: 'adapter:test',
      config: { app_id: 'app-47', timeout_s: 300 },
    })
    await userEvent.selectOptions(screen.getByLabelText('调用接入'), '')
    expect(screen.getByTestId('binding')).toHaveTextContent('null')
  })
})
