import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { mockApi } from '@/test/mockApi'
import { TraceRefreshDialog } from './TraceRefreshDialog'

afterEach(() => vi.unstubAllGlobals())

function setup() {
  mockApi({
    'GET /api/v1/adapter-environments': {
      production: { configured: true, revision: 14 },
      test: { configured: true, revision: 3 },
    },
    'GET /api/v1/adapters/credentials': [
      { ref: 'adapter:new', name: '更新的 Cookie', adapter_id: 'hiagent' },
      { ref: 'adapter:other', name: '其他平台', adapter_id: 'other' },
    ],
  })
  const submit = vi.fn()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}>
    <TraceRefreshDialog platform="hiagent" environment="production" pending={false}
      onSubmit={submit} onClose={vi.fn()} />
  </QueryClientProvider></MemoryRouter>)
  return submit
}

it('默认使用最新生产存档，直接选择新凭据，过滤其他平台凭据', async () => {
  const submit = setup()
  await screen.findByText('使用存档 #14 的观测配置与凭据')
  await waitFor(() => expect(screen.getByRole('button', { name: '获取 Trace' })).toBeEnabled())
  expect(screen.queryByText('其他平台')).toBeNull()
  await userEvent.selectOptions(screen.getByLabelText('查询凭据'), 'adapter:new')
  await userEvent.click(screen.getByRole('button', { name: '获取 Trace' }))
  expect(submit).toHaveBeenCalledWith({
    config_source: 'current', environment: 'production', credential_ref: 'adapter:new',
  })
})

it('选择原配置时不传环境，并保留更新凭据入口', async () => {
  const submit = setup()
  await userEvent.selectOptions(screen.getByLabelText('查询配置'), 'original')
  await userEvent.click(screen.getByRole('button', { name: '获取 Trace' }))
  expect(submit).toHaveBeenCalledWith({ config_source: 'original' })
})
