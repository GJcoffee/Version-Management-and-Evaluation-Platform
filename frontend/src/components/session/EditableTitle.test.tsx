import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EditableTitle, SESSION_TITLE_MAX } from './EditableTitle'

describe('EditableTitle', () => {
  it('点铅笔进入编辑，Enter 保存去空白后的标题并退出编辑', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<EditableTitle value="旧标题" onSave={onSave} />)
    expect(screen.getByTestId('session-title')).toHaveTextContent('旧标题')
    await userEvent.click(screen.getByRole('button', { name: '编辑标题' }))
    const input = screen.getByTestId('session-title-input')
    expect(input).toHaveValue('旧标题')
    await userEvent.clear(input)
    await userEvent.type(input, '  新标题 {Enter}')
    expect(onSave).toHaveBeenCalledWith('新标题')
    await waitFor(() => expect(screen.queryByTestId('session-title-input')).not.toBeInTheDocument())
  })

  it('空白或超长不调用 onSave，并提示错误', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<EditableTitle value="a" onSave={onSave} />)
    await userEvent.click(screen.getByRole('button', { name: '编辑标题' }))
    const input = screen.getByTestId('session-title-input')
    await userEvent.clear(input)
    await userEvent.type(input, '   {Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent('不能为空')
    await userEvent.clear(input)
    await userEvent.click(input)
    await userEvent.paste('x'.repeat(SESSION_TITLE_MAX + 1))
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent(String(SESSION_TITLE_MAX))
    expect(onSave).not.toHaveBeenCalled()
  })

  it('Esc 取消不保存；保存失败时保持编辑状态', async () => {
    const onSave = vi.fn().mockRejectedValue(new Error('boom'))
    render(<EditableTitle value="a" onSave={onSave} />)
    await userEvent.click(screen.getByRole('button', { name: '编辑标题' }))
    await userEvent.type(screen.getByTestId('session-title-input'), 'b{Escape}')
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByTestId('session-title')).toHaveTextContent('a')

    await userEvent.click(screen.getByRole('button', { name: '编辑标题' }))
    await userEvent.type(screen.getByTestId('session-title-input'), 'c{Enter}')
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('ac'))
    expect(screen.getByTestId('session-title-input')).toBeInTheDocument()
  })

  it('disabled 时不显示编辑入口', () => {
    render(<EditableTitle value="a" onSave={vi.fn()} disabled />)
    expect(screen.queryByRole('button', { name: '编辑标题' })).not.toBeInTheDocument()
  })
})
