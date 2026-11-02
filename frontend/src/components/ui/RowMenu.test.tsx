import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RowMenu } from './RowMenu'

describe('RowMenu', () => {
  it('点开后列出操作，选中一项执行并关闭；Esc 关闭', async () => {
    const tag = vi.fn()
    render(<RowMenu testId="more" items={[{ label: '打标签', onSelect: tag }, { label: '创建草稿', onSelect: vi.fn() }]} />)
    expect(screen.queryByRole('menu')).toBeNull()
    await userEvent.click(screen.getByTestId('more'))
    expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual(['打标签', '创建草稿'])
    await userEvent.click(screen.getByRole('menuitem', { name: '打标签' }))
    expect(tag).toHaveBeenCalledOnce()
    expect(screen.queryByRole('menu')).toBeNull()
    await userEvent.click(screen.getByTestId('more'))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
