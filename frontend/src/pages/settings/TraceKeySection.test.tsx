import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import type { TraceKeyConnection } from '@/api/queries/keyConnections'
import { TraceKeySection } from './TraceKeySection'

const connection: TraceKeyConnection = {
  id: 'key_test', name: '观测密钥', origin: 'https://web.example', top_url: 'https://top.example:30040',
  workspace_id: 'workspace', credential_source: 'key', revision: 2, enabled: true,
  status: 'active', last_checked_at: null, last_error: null,
}

function setup(rows: TraceKeyConnection[] = []) {
  vi.spyOn(api, 'get').mockResolvedValue(rows as never)
  const post = vi.spyOn(api, 'post').mockResolvedValue(connection)
  const put = vi.spyOn(api, 'put').mockResolvedValue(connection)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <TraceKeySection />
  </QueryClientProvider>)
  return { post, put }
}

describe('执行链路密钥连接', () => {
  afterEach(() => vi.restoreAllMocks())

  it('新建连接保存账号密钥且不回显正文', async () => {
    const { post } = setup()
    await userEvent.click(screen.getByRole('button', { name: '新建密钥连接' }))
    const dialog = within(screen.getByRole('dialog'))
    for (const [label, value] of [
      ['连接名称', '观测'], ['站点地址', connection.origin], ['密钥接口地址', connection.top_url],
      ['工作空间编号', 'workspace'], ['访问密钥标识', 'private-ak'], ['访问密钥正文', 'private-sk'],
    ]) await userEvent.type(dialog.getByLabelText(new RegExp(`^${label}`)), value)
    expect(dialog.getByLabelText(/^访问密钥标识/)).toHaveAttribute('type', 'password')
    expect(dialog.getByLabelText(/^访问密钥正文/)).toHaveAttribute('type', 'password')
    expect(dialog.queryByLabelText(/^用户名/)).not.toBeInTheDocument()
    await userEvent.click(dialog.getByRole('button', { name: '保存并检查' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/adapters/key-connections', { body: {
      name: '观测', origin: connection.origin, top_url: connection.top_url, workspace_id: 'workspace',
      credential_source: 'key', access_key_id: 'private-ak', access_key_secret: 'private-sk',
    } }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByDisplayValue('private-sk')).not.toBeInTheDocument()
  })

  it('编辑时密钥保持空白，不输入就保留原值', async () => {
    const { put } = setup([connection])
    await userEvent.click(await screen.findByRole('button', { name: '编辑' }))
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByLabelText('访问密钥正文')).toHaveValue('')
    await userEvent.type(dialog.getByLabelText('访问密钥标识'), 'partial')
    expect(dialog.getByRole('button', { name: '保存并检查' })).toBeDisabled()
    await userEvent.clear(dialog.getByLabelText('访问密钥标识'))
    await userEvent.click(dialog.getByRole('button', { name: '保存并检查' }))
    await waitFor(() => expect(put).toHaveBeenCalledWith('/adapters/key-connections/key_test', {
      body: { name: '观测密钥', expected_revision: 2 },
    }))
  })

  it('检查与停用操作使用所选连接，保留修订约束', async () => {
    const { post, put } = setup([connection])
    await userEvent.click(await screen.findByRole('button', { name: '检查连接' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/adapters/key-connections/key_test/check'))
    await userEvent.click(screen.getByRole('button', { name: '停用' }))
    await waitFor(() => expect(put).toHaveBeenCalledWith('/adapters/key-connections/key_test', {
      body: { expected_revision: 2, enabled: false },
    }))
  })
})
