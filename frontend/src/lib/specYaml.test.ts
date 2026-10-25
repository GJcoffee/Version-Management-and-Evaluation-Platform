import { describe, expect, it } from 'vitest'
import { getIn, issuesToMarkers, lineOfPath, parsePath, parseSpecYaml, setInYaml, specToYaml, yamlProblemsToMarkers } from './specYaml'

const YAML = `schema: evalp/v1
kind: config
model:
  provider: fake
  name: fake-chat
  params:
    temperature: 0.3
prompts:
  system: |-
    第一行
    第二行
  variables:
    company: 示例公司
tools:
  - asset://tool/query-order@latest
  - builtin: knowledge_search
    with:
      sources:
        - asset://knowledge/faq@latest
      top_k: 5
runtime:
  max_steps: 12
`

describe('parsePath', () => {
  it('点号、下标与方括号身份键', () => {
    expect(parsePath('model.params.temperature')).toEqual(['model', 'params', 'temperature'])
    expect(parsePath('tools[0]')).toEqual(['tools', '0'])
    expect(parsePath('tools.0.with')).toEqual(['tools', '0', 'with'])
    expect(parsePath('tools[knowledge_search].with.sources[faq]')).toEqual(['tools', 'knowledge_search', 'with', 'sources', 'faq'])
    expect(parsePath('tools[my.tool]')).toEqual(['tools', 'my.tool'])
  })
})

describe('lineOfPath：错误路径映射到 YAML 行号', () => {
  it('嵌套键指向键所在行', () => {
    expect(lineOfPath(YAML, 'model.provider')).toBe(4)
    expect(lineOfPath(YAML, 'model.params.temperature')).toBe(7)
    expect(lineOfPath(YAML, 'runtime.max_steps')).toBe(22)
  })
  it('数组下标与身份键', () => {
    expect(lineOfPath(YAML, 'tools[0]')).toBe(15)
    expect(lineOfPath(YAML, 'tools.1')).toBe(16)
    expect(lineOfPath(YAML, 'tools[query-order]')).toBe(15)
    expect(lineOfPath(YAML, 'tools[knowledge_search].with.top_k')).toBe(20)
    expect(lineOfPath(YAML, 'tools[knowledge_search].with.sources[faq]')).toBe(19)
  })
  it('多行块标量指向其键所在行', () => {
    expect(lineOfPath(YAML, 'prompts.system')).toBe(9)
  })
  it('缺失的路径退回到最深的已存在祖先', () => {
    expect(lineOfPath(YAML, 'model.params.seed')).toBe(6)
    expect(lineOfPath(YAML, 'knowledge.inject')).toBe(1)
    expect(lineOfPath(YAML, 'tools[9]')).toBe(14)
  })
  it('空路径、空文档', () => {
    expect(lineOfPath(YAML, '')).toBe(1)
    expect(lineOfPath('', 'a.b')).toBeNull()
  })
})

describe('issuesToMarkers', () => {
  it('生成带行号和级别的标注', () => {
    const m = issuesToMarkers(YAML, [{ path: 'model.provider', message: 'Provider 不存在' }], 'error')
    expect(m).toEqual([{ line: 4, message: 'model.provider：Provider 不存在', severity: 'error' }])
    const w = issuesToMarkers(YAML, [{ path: 'nope', message: 'x' }], 'warning')
    expect(w[0].line).toBe(1)
    expect(w[0].severity).toBe('warning')
  })
})

describe('parseSpecYaml', () => {
  it('合法 YAML 返回对象', () => {
    const r = parseSpecYaml(YAML)
    expect(r.ok).toBe(true)
    if (r.ok) expect(getIn(r.value, ['model', 'name'])).toBe('fake-chat')
  })
  it('语法错误带行列', () => {
    const r = parseSpecYaml('a: 1\nb: [1, 2\nc: 3\n')
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(r.errors[0].line).toBeGreaterThanOrEqual(2)
      expect(yamlProblemsToMarkers(r.errors)[0].severity).toBe('error')
    }
  })
  it('顶层不是映射视为错误；空文本视为空对象', () => {
    expect(parseSpecYaml('- a\n- b').ok).toBe(false)
    const empty = parseSpecYaml('')
    expect(empty.ok && empty.value).toEqual({})
  })
})

describe('表单 <-> YAML 同步', () => {
  it('specToYaml 与 parseSpecYaml 往返一致', () => {
    const spec = { schema: 'evalp/v1', kind: 'config', model: { provider: 'p', name: 'm' }, prompts: { system: '你好\n世界' } }
    const r = parseSpecYaml(specToYaml(spec))
    expect(r.ok && r.value).toEqual(spec)
  })
  it('表单修改只改目标路径并保留注释', () => {
    const text = '# 头部注释\nmodel:\n  provider: a # 行尾注释\n  name: m\n'
    const next = setInYaml(text, ['model', 'provider'], 'b')
    expect(next).toContain('# 头部注释')
    expect(next).toContain('# 行尾注释')
    const r = parseSpecYaml(next)
    expect(r.ok && getIn(r.value, ['model'])).toEqual({ provider: 'b', name: 'm' })
  })
  it('值为 undefined 删除键；不存在的中间层自动创建', () => {
    const deleted = setInYaml(YAML, ['model', 'params', 'temperature'], undefined)
    expect(deleted).not.toContain('temperature')
    const created = setInYaml('kind: config\n', ['runtime', 'max_steps'], 5)
    const r = parseSpecYaml(created)
    expect(r.ok && getIn(r.value, ['runtime', 'max_steps'])).toBe(5)
  })
  it('整组替换数组', () => {
    const next = setInYaml(YAML, ['tools'], ['asset://tool/x@2'])
    const r = parseSpecYaml(next)
    expect(r.ok && getIn(r.value, ['tools'])).toEqual(['asset://tool/x@2'])
  })
  it('YAML 有语法错误时原样返回，不丢失用户输入', () => {
    const broken = 'model: [1, 2\n'
    expect(setInYaml(broken, ['model'], 'x')).toBe(broken)
  })
  it('空文档也能写入', () => {
    const r = parseSpecYaml(setInYaml('', ['kind'], 'config'))
    expect(r.ok && r.value).toEqual({ kind: 'config' })
  })
})
