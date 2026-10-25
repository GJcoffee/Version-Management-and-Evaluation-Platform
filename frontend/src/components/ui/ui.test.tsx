import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'
import { Dialog } from './Dialog'
import { JsonViewer } from './JsonViewer'
import { KeyValueEditor } from './KeyValueEditor'
import { shortenId } from './IdText'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './Tabs'
import { toast, useToastStore } from '@/stores/toast'

describe('Dialog', () => {
  it('Esc 关闭，open=false 时不渲染', async () => {
    const onOpenChange = vi.fn()
    const { rerender } = render(
      <Dialog open onOpenChange={onOpenChange} title="标题">
        <button>内容</button>
      </Dialog>,
    )
    expect(screen.getByRole('dialog', { name: '标题' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(onOpenChange).toHaveBeenCalledWith(false)
    rerender(
      <Dialog open={false} onOpenChange={onOpenChange} title="标题" />,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('ConfirmDialog', () => {
  it('确认成功后关闭；失败时保持打开', async () => {
    const onOpenChange = vi.fn()
    const ok = vi.fn().mockResolvedValue(undefined)
    const { unmount } = render(<ConfirmDialog open onOpenChange={onOpenChange} title="删除？" onConfirm={ok} confirmText="删除" />)
    await userEvent.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    unmount()

    const onOpenChange2 = vi.fn()
    const fail = vi.fn().mockRejectedValue(new Error('x'))
    render(<ConfirmDialog open onOpenChange={onOpenChange2} title="删除？" onConfirm={fail} confirmText="删除" />)
    await userEvent.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(fail).toHaveBeenCalled())
    expect(onOpenChange2).not.toHaveBeenCalledWith(false)
  })
})

describe('KeyValueEditor', () => {
  function Harness({ onValue }: { onValue: (v: Record<string, string>) => void }) {
    const [v, setV] = useState<Record<string, string>>({ a: '1' })
    return (
      <KeyValueEditor
        value={v}
        onChange={(n) => {
          setV(n)
          onValue(n)
        }}
      />
    )
  }
  it('增删改行并忽略空键', async () => {
    const onValue = vi.fn()
    render(<Harness onValue={onValue} />)
    await userEvent.click(screen.getByRole('button', { name: '添加' }))
    expect(screen.getAllByLabelText('键')).toHaveLength(2)
    // 空键行不进入结果；编辑过程中该行不会被外部同步清掉
    expect(onValue).not.toHaveBeenCalled()
    await userEvent.type(screen.getAllByLabelText('键')[1], 'b')
    await userEvent.type(screen.getAllByLabelText('值')[1], '2')
    expect(onValue).toHaveBeenLastCalledWith({ a: '1', b: '2' })
    await userEvent.click(screen.getAllByRole('button', { name: '删除此行' })[0])
    expect(onValue).toHaveBeenLastCalledWith({ b: '2' })
  })
})

describe('JsonViewer', () => {
  it('默认展开一层，可折叠', async () => {
    render(<JsonViewer value={{ name: 'x', nested: { deep: 1 } }} />)
    expect(screen.getByText('"x"')).toBeInTheDocument()
    expect(screen.queryByText('"deep"')).toBeNull() // 第二层默认折叠
    await userEvent.click(screen.getByRole('button', { name: /"nested"/ }))
    expect(screen.getByText('"deep"')).toBeInTheDocument()
  })
})

describe('Tabs', () => {
  it('切换内容', async () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">内容A</TabsContent>
        <TabsContent value="b">内容B</TabsContent>
      </Tabs>,
    )
    expect(screen.getByText('内容A')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'B' }))
    expect(screen.getByText('内容B')).toBeInTheDocument()
    expect(screen.queryByText('内容A')).toBeNull()
  })
})

describe('shortenId', () => {
  it('缩短 ULID 与 hash，短值原样返回', () => {
    expect(shortenId('agt_01J8ZQ4K2MABCDEFGHJKMNPQRS')).toBe('agt_…JKMNPQRS')
    expect(shortenId('agt_short')).toBe('agt_short')
    expect(shortenId('a'.repeat(64))).toBe('aaaaaaaa')
  })
})

describe('toast store', () => {
  it('push / dismiss', () => {
    useToastStore.getState().clear()
    const id = toast.success('ok', { duration: 0 })
    expect(useToastStore.getState().toasts.map((t) => t.title)).toEqual(['ok'])
    useToastStore.getState().dismiss(id)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
