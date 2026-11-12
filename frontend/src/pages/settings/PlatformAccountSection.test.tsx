import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import type { PlatformAccount } from '@/api/queries/accounts'
import { PlatformAccountSection } from './PlatformAccountSection'

const account: PlatformAccount = {
  id: 'acc_test', name: '观测账号', origin: 'https://accounts.example', username: 'tester',
  adapter_id: 'hiagent', tenant_name: '', enabled: true, status: 'verification_required',
  config_revision: 2, session_revision: 3, last_checked_at: null,
  last_error: '站点要求额外验证', next_check_at: null,
}

function setup(rows: PlatformAccount[] = []) {
  vi.spyOn(api, 'get').mockResolvedValue(rows as never)
  const post = vi.spyOn(api, 'post').mockResolvedValue({ ...account, status: 'active', last_error: null })
  const put = vi.spyOn(api, 'put').mockResolvedValue(account)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <PlatformAccountSection />
  </QueryClientProvider>)
  return { post, put }
}

describe('Trace 账号管理', () => {
  afterEach(() => vi.restoreAllMocks())

  it('创建账号发送站点租户和密码，保存后清空密码输入', async () => {
    const { post } = setup()
    await userEvent.click(screen.getByRole('button', { name: '新增账号' }))
    const dialog = within(screen.getByRole('dialog'))
    await userEvent.type(dialog.getByLabelText(/^账号名称/), 'Trace')
    await userEvent.type(dialog.getByLabelText(/^站点地址/), 'https://accounts.example')
    await userEvent.type(dialog.getByLabelText('租户名称'), 'tenant')
    await userEvent.type(dialog.getByLabelText(/^用户名/), 'tester')
    const password = dialog.getByLabelText(/^密码/)
    expect(password).toHaveAttribute('type', 'password')
    await userEvent.type(password, 'secret-password')
    await userEvent.click(dialog.getByRole('button', { name: '保存账号' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/adapters/accounts', { body: {
      adapter_id: 'hiagent', name: 'Trace', origin: 'https://accounts.example', tenant_name: 'tenant',
      username: 'tester', password: 'secret-password',
    } }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByDisplayValue('secret-password')).not.toBeInTheDocument()
  })

  it('编辑密码留空，保留原密码并带上配置修订', async () => {
    const { put } = setup([account])
    await userEvent.click(await screen.findByRole('button', { name: '编辑' }))
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByLabelText('密码')).toHaveValue('')
    expect(dialog.getByLabelText(/^站点地址/)).toBeDisabled()
    await userEvent.click(dialog.getByRole('button', { name: '保存账号' }))
    await waitFor(() => expect(put).toHaveBeenCalledWith('/adapters/accounts/acc_test', {
      body: { name: '观测账号', expected_revision: 2 },
    }))
  })

  it('验证失败状态提供补充登录态入口，发送前保留为密码输入', async () => {
    const { post } = setup([account])
    expect(await screen.findByText('需要额外验证')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '补充登录态' }))
    const dialog = within(screen.getByRole('dialog'))
    const input = dialog.getByLabelText('Cookie／CSRF JSON')
    expect(input).toHaveAttribute('type', 'password')
    await userEvent.click(input)
    await userEvent.paste('{"Cookie":"sid=manual","X-Csrf-Token":"csrf"}')
    await userEvent.click(dialog.getByRole('button', { name: '核验并保存' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/adapters/accounts/acc_test/session', {
      body: { headers: '{"Cookie":"sid=manual","X-Csrf-Token":"csrf"}' },
    }))
  })
})
