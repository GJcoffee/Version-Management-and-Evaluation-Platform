import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import type { Conventions } from '@/api/types'
import { PublishDialog } from './PublishDialog'

const NONE: Conventions = { tag_from_param: null, require_notes: false, require_source_id: [], snapshot_readonly: false }

function setup(conventions: Conventions, params?: Record<string, unknown>, onPublish = vi.fn(async () => {})) {
  render(
    <PublishDialog
      open
      onOpenChange={() => {}}
      onPublish={onPublish}
      conventions={conventions}
      paramValues={params}
      warnings={[]}
      changeSummary="x"
    />,
  )
  return onPublish
}

describe('PublishDialog 与发布规范', () => {
  it('没有规范时备注和标签都可选', async () => {
    const onPublish = setup(NONE)
    await userEvent.click(screen.getByRole('button', { name: '发布' }))
    expect(onPublish).toHaveBeenCalledWith({ notes: '', tag: '' })
  })

  it('require_notes：备注为空时不能发布', async () => {
    const onPublish = setup({ ...NONE, require_notes: true })
    expect(screen.getByRole('button', { name: '发布' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/备注/), '调低温度')
    await userEvent.click(screen.getByRole('button', { name: '发布' }))
    expect(onPublish).toHaveBeenCalledWith({ notes: '调低温度', tag: '' })
  })

  it('tag_from_param：标签固定为 v{参数值} 且只读', async () => {
    const onPublish = setup({ ...NONE, tag_from_param: 'hiagent_version' }, { hiagent_version: '0.5.3' })
    const tag = screen.getByLabelText(/标签/)
    expect(tag).toHaveValue('v0.5.3')
    expect(tag).toHaveAttribute('readonly')
    await userEvent.click(screen.getByRole('button', { name: '发布' }))
    expect(onPublish).toHaveBeenCalledWith({ notes: '', tag: 'v0.5.3' })
  })

  it('tag_from_param：草稿 params 里没有该参数时提示并禁止发布', () => {
    setup({ ...NONE, tag_from_param: 'hiagent_version' }, {})
    expect(screen.getByText(/草稿的 params 里没有这个参数/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发布' })).toBeDisabled()
  })

  it('服务端拒绝（CONVENTION_VIOLATION）时把说明显示在对话框里', async () => {
    const msg = 'agent「bot」的发布规范不允许这次发布：\n标签 v0.5.3 已存在于 v7'
    const onPublish = vi.fn(async () => {
      throw new ApiError(409, 'CONVENTION_VIOLATION', msg, {})
    })
    setup({ ...NONE, tag_from_param: 'hiagent_version' }, { hiagent_version: '0.5.3' }, onPublish)
    await userEvent.click(screen.getByRole('button', { name: '发布' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('标签 v0.5.3 已存在于 v7')
  })
})
