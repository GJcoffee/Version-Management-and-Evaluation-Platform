import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Agent, Conventions } from '@/api/types'
import { mockApi } from '@/test/mockApi'
import { ConventionsTab, sameConventions, toConventions } from './ConventionsTab'

afterEach(() => vi.unstubAllGlobals())

const NONE: Conventions = { tag_from_param: null, require_notes: false, require_source_id: [], snapshot_readonly: false }
const agent = (conventions: Conventions = NONE): Agent => ({
  id: 'agt_1',
  name: 'bot',
  kind: 'code',
  description: null,
  archived: false,
  created_at: '',
  updated_at: '',
  latest_version: null,
  tags: [],
  conventions,
})

function setup(a: Agent, routes: Parameters<typeof mockApi>[0] = {}) {
  const api = mockApi(routes)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ConventionsTab agent={a} />
    </QueryClientProvider>,
  )
  return api
}

describe('规范表单取值', () => {
  it('参数名去掉空白，空表示不开启；类型顺序不影响是否有改动', () => {
    expect(toConventions({ ...NONE, tag_from_param: '  hiagent_version ' }).tag_from_param).toBe('hiagent_version')
    expect(toConventions({ ...NONE, tag_from_param: '   ' }).tag_from_param).toBeNull()
    const a = { ...NONE, require_source_id: ['skill', 'mcp'] as Conventions['require_source_id'] }
    expect(sameConventions(a, { ...a, require_source_id: ['mcp', 'skill'] })).toBe(true)
    expect(sameConventions(a, NONE)).toBe(false)
    expect(sameConventions({ ...NONE, tag_from_param: ' x ' }, { ...NONE, tag_from_param: 'x' })).toBe(true)
  })
})

describe('ConventionsTab', () => {
  it('显示当前规范；没有改动时保存按钮不可用', () => {
    setup(agent({ tag_from_param: 'hiagent_version', require_notes: true, require_source_id: ['skill'], snapshot_readonly: false }))
    expect(screen.getByLabelText(/版本标签取自参数/)).toHaveValue('hiagent_version')
    expect(screen.getByRole('checkbox', { name: /发布说明必填/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Skill' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'MCP 插件' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('修改后保存：整体提交 conventions', async () => {
    const saved = { tag_from_param: 'hiagent_version', require_notes: true, require_source_id: ['skill', 'mcp'], snapshot_readonly: true }
    const api = setup(agent(), { 'PATCH /api/v1/agents/agt_1': { ...agent(saved as Conventions) } })
    await userEvent.type(screen.getByLabelText(/版本标签取自参数/), ' hiagent_version ')
    await userEvent.click(screen.getByRole('checkbox', { name: /发布说明必填/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Skill' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'MCP 插件' }))
    await userEvent.click(screen.getByRole('checkbox', { name: /快照资产只读/ }))
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(api.find('PATCH', /\/agents\/agt_1$/)).toHaveLength(1))
    expect(api.find('PATCH', /\/agents\/agt_1$/)[0].body).toEqual({ conventions: saved })
  })

  it('服务端拒绝时在表单内显示原因', async () => {
    setup(agent(), {
      'PATCH /api/v1/agents/agt_1': { __status: 400, body: { error: { code: 'VALIDATION_ERROR', message: '参数名不合法', details: {} } } },
    })
    await userEvent.click(screen.getByRole('checkbox', { name: /发布说明必填/ }))
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('参数名不合法')
  })
})
