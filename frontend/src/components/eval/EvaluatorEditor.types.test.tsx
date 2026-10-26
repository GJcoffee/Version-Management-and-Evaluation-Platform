import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EvaluatorEditor } from './EvaluatorEditor'
import { evaluatorToInput, validateEvaluators, type EvaluatorForm } from './evalUtils'

afterEach(() => vi.unstubAllGlobals())

let latest: EvaluatorForm[] = []
function Harness() {
  const [v, setV] = useState<EvaluatorForm[]>([])
  latest = v
  return <EvaluatorEditor value={v} onChange={setV} showErrors />
}
function setup() {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Harness />
    </QueryClientProvider>,
  )
}
const paste = async (el: HTMLElement, text: string) => {
  await userEvent.click(el)
  await userEvent.paste(text)
}

describe('EvaluatorEditor：json_schema / trace / python / llm_judge samples / human', () => {
  it('八种评估器都有添加入口', () => {
    setup()
    for (const t of ['exact_match', 'contains', 'regex', 'json_schema', 'trace', 'python', 'llm_judge', 'human']) {
      expect(screen.getByTestId(`add-evaluator-${t}`)).toBeInTheDocument()
    }
  })

  it('json_schema：JSON 不合法时提示，修正后生成 schema 对象', async () => {
    setup()
    await userEvent.click(screen.getByTestId('add-evaluator-json_schema'))
    const card = screen.getByTestId('evaluator-schema')
    const area = within(card).getByLabelText('JSON Schema')
    await userEvent.clear(area)
    await paste(area, '{"type": ')
    expect(within(card).getByText(/不是合法的 JSON/)).toBeInTheDocument()
    await userEvent.clear(area)
    await paste(area, '{"type":"object","required":["name"]}')
    expect(within(card).queryByText(/不是合法的 JSON/)).not.toBeInTheDocument()
    await userEvent.selectOptions(within(card).getByLabelText('提取方式'), 'codeblock')
    expect(evaluatorToInput(latest[0])).toMatchObject({ type: 'json_schema', schema: { type: 'object', required: ['name'] }, extract: 'codeblock' })
  })

  it('trace：没有断言时提示；填写工具与上限后生成 assert', async () => {
    setup()
    await userEvent.click(screen.getByTestId('add-evaluator-trace'))
    const card = screen.getByTestId('evaluator-trace')
    expect(within(card).getByText('至少需要设置一项断言')).toBeInTheDocument()
    await userEvent.type(within(card).getByLabelText('必须调用的工具'), 'query_order')
    await userEvent.type(within(card).getByLabelText('步数上限'), '4')
    expect(within(card).queryByText('至少需要设置一项断言')).not.toBeInTheDocument()
    expect(evaluatorToInput(latest[0])).toMatchObject({ type: 'trace', assert: { tool_called: ['query_order'], max_steps: 4 } })
  })

  it('python：入口格式校验', async () => {
    setup()
    await userEvent.click(screen.getByTestId('add-evaluator-python'))
    const card = screen.getByTestId('evaluator-python')
    expect(within(card).getByText(/module:function 格式/, { selector: 'p.text-danger' })).toBeInTheDocument()
    await userEvent.type(within(card).getByLabelText('入口函数'), 'my_evals:check')
    expect(within(card).queryByText(/module:function 格式/, { selector: 'p.text-danger' })).not.toBeInTheDocument()
    expect(evaluatorToInput(latest[0])).toMatchObject({ type: 'python', entry: 'my_evals:check', timeout_s: 30 })
  })

  it('llm_judge：samples 默认 1，可改为多次采样', async () => {
    setup()
    await userEvent.click(screen.getByTestId('add-evaluator-llm_judge'))
    const card = screen.getByTestId('evaluator-judge')
    const samples = within(card).getByLabelText('采样次数')
    expect(samples).toHaveValue(1)
    await userEvent.clear(samples)
    await userEvent.type(samples, '3')
    expect(latest[0].samples).toBe('3')
    await userEvent.clear(samples)
    await userEvent.type(samples, '20')
    const withProvider = { ...latest[0], provider: 'p', model: 'm' }
    expect(latest[0].samples).toBe('20')
    expect(validateEvaluators([withProvider]).byUid[withProvider.uid]).toMatch(/采样次数/)
  })

  it('human：评审标准、量程；默认不参与通过判定', async () => {
    setup()
    await userEvent.click(screen.getByTestId('add-evaluator-human'))
    const card = screen.getByTestId('evaluator-human')
    expect(within(card).getByRole('checkbox', { name: '参与通过判定' })).not.toBeChecked()
    await userEvent.type(within(card).getByLabelText('评审标准'), '是否礼貌')
    await userEvent.clear(within(card).getByLabelText('量程最大值'))
    await userEvent.type(within(card).getByLabelText('量程最大值'), '10')
    expect(evaluatorToInput(latest[0])).toEqual({ key: 'human', gate: false, type: 'human', scale: [1, 10], guideline: '是否礼貌' })
  })
})
