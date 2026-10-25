import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SpecForm } from './SpecForm'

vi.mock('@/api/queries/providers', () => ({
  useProviders: () => ({
    data: [
      { id: 'prv_1', name: 'fake', models: [{ name: 'm1' }, { name: 'm2' }] },
      { id: 'prv_2', name: 'other', models: [{ name: 'o1' }] },
      { id: 'prv_3', name: 'bare', models: [] },
    ],
  }),
}))
vi.mock('@/components/AssetRefPicker', () => ({
  AssetRefPicker: ({ value, placeholder }: { value: string | null; placeholder?: string }) => (
    <div data-testid="picker">{value ?? placeholder}</div>
  ),
  AssetRefListEditor: ({ value }: { value: string[] }) => <div data-testid="reflist">{value.join(',')}</div>,
}))

const config = {
  schema: 'evalp/v1',
  kind: 'config',
  model: { provider: 'fake', name: 'm1', params: { temperature: 0.3, seed: null } },
  prompts: { system: 'asset://prompt/sys@latest', variables: { company: 'X' } },
  tools: ['asset://tool/a@2', { builtin: 'knowledge_search', with: { sources: ['asset://knowledge/faq@latest'], top_k: 5 } }],
  knowledge: { inject: [] },
  runtime: { max_steps: 12, parallel_tool_calls: true },
}

describe('SpecForm（配置型）', () => {
  it('展示 spec 里的值', () => {
    render(<SpecForm spec={config} kind="config" onEdit={() => undefined} />)
    expect(screen.getByLabelText(/Provider/)).toHaveValue('fake')
    expect(screen.getByLabelText('temperature')).toHaveValue('0.3')
    expect(screen.getAllByTestId('picker').some((e) => e.textContent === 'asset://prompt/sys@latest')).toBe(true)
    expect(screen.getByText(/knowledge_search/)).toBeInTheDocument()
  })

  it('修改数字产生对应路径的变更；清空删除该键', async () => {
    const onEdit = vi.fn()
    render(<SpecForm spec={config} kind="config" onEdit={onEdit} />)
    const t = screen.getByLabelText('temperature')
    await userEvent.clear(t)
    expect(onEdit).toHaveBeenLastCalledWith([[['model', 'params', 'temperature'], undefined]])
    await userEvent.type(t, '0.7')
    expect(onEdit).toHaveBeenLastCalledWith([[['model', 'params', 'temperature'], 0.7]])
  })

  it('切换 Provider 时，原模型不在新列表里则换成新 Provider 的第一个模型', async () => {
    const onEdit = vi.fn()
    render(<SpecForm spec={config} kind="config" onEdit={onEdit} />)
    await userEvent.selectOptions(screen.getByLabelText(/Provider/), 'other')
    expect(onEdit).toHaveBeenLastCalledWith([
      [['model', 'provider'], 'other'],
      [['model', 'name'], 'o1'],
    ])
  })

  it('Provider 没有登记模型时，模型改为文本输入', () => {
    render(<SpecForm spec={{ ...config, model: { provider: 'bare', name: 'x' } }} kind="config" onEdit={() => undefined} />)
    expect(screen.getByLabelText(/^模型/).tagName).toBe('INPUT')
  })

  it('取消勾选 knowledge_search 会从 tools 中移除该项', async () => {
    const onEdit = vi.fn()
    render(<SpecForm spec={config} kind="config" onEdit={onEdit} />)
    await userEvent.click(screen.getByLabelText(/内置工具 knowledge_search/))
    expect(onEdit).toHaveBeenLastCalledWith([[['tools'], ['asset://tool/a@2']]])
  })

  it('disabled 时控件不可编辑', () => {
    render(<SpecForm spec={config} kind="config" disabled onEdit={() => undefined} />)
    expect(screen.getByLabelText('temperature')).toBeDisabled()
    expect(screen.getByLabelText(/Provider/)).toBeDisabled()
  })
})

describe('SpecForm（代码型）', () => {
  const code = {
    schema: 'evalp/v1',
    kind: 'code',
    transport: 'subprocess',
    subprocess: { command: ['python', '-m', 'x'], cwd: '.' },
    source: { repo: '.', capture_git: false },
    params: { top_k: 3 },
  }
  it('subprocess：命令按行展示，修改回写为数组', async () => {
    const onEdit = vi.fn()
    render(<SpecForm spec={code} kind="code" onEdit={onEdit} />)
    const cmd = screen.getByLabelText(/command/)
    expect(cmd).toHaveValue('python\n-m\nx')
    await userEvent.type(cmd, 'y')
    expect(onEdit).toHaveBeenLastCalledWith([[['subprocess', 'command'], ['python', '-m', 'xy']]])
  })
  it('params 为 JSON 文本，非法 JSON 不回写并提示', async () => {
    const onEdit = vi.fn()
    render(<SpecForm spec={code} kind="code" onEdit={onEdit} />)
    const box = screen.getByDisplayValue(/top_k/)
    await userEvent.clear(box)
    await userEvent.type(box, '{{bad')
    expect(screen.getByText(/JSON 无效/)).toBeInTheDocument()
    expect(onEdit).not.toHaveBeenCalledWith([[['params'], expect.objectContaining({ bad: expect.anything() })]])
  })
})
