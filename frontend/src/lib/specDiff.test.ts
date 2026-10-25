import { describe, expect, it } from 'vitest'
import type { AgentSpec } from '@/api/types'
import { countSpecChanges, flattenSpec, unpinSpec } from './specDiff'

const pin = (name: string, rev: number, hash: string) => ({ $asset: { kind: 'tool' as const, name, rev, hash } })
const base = {
  schema: 'evalp/v1',
  kind: 'config',
  model: { provider: 'p', name: 'm', params: { temperature: 0.3 } },
  tools: [pin('a', 1, 'h1')],
} as unknown as AgentSpec

describe('countSpecChanges', () => {
  it('相同内容为 0；显示字段 name/description/notes 不计入', () => {
    expect(countSpecChanges(base, { ...base, name: 'x', notes: 'y' })).toBe(0)
  })
  it('标量修改、新增、删除各算一处', () => {
    const cur = { ...base, model: { provider: 'p', name: 'm2', params: { temperature: 0.7, seed: 1 } } }
    expect(countSpecChanges(base, cur)).toBe(3)
  })
  it('资产引用：哈希相同（即使修订号不同）视为未变；哈希不同算一处', () => {
    expect(countSpecChanges(base, { ...base, tools: [pin('a', 2, 'h1')] })).toBe(0)
    expect(countSpecChanges(base, { ...base, tools: [pin('a', 2, 'h2')] })).toBe(1)
  })
  it('列表长度变化', () => {
    expect(countSpecChanges(base, { ...base, tools: [pin('a', 1, 'h1'), pin('b', 1, 'h3')] })).toBe(1)
  })
})

describe('flattenSpec / unpinSpec', () => {
  it('空数组与空对象有占位值', () => {
    expect(flattenSpec({ a: [], b: {} })).toEqual({ a: '[]', b: '{}' })
  })
  it('unpinSpec 把固化引用换成 asset:// 字符串', () => {
    const out = unpinSpec(base) as unknown as { tools: string[] }
    expect(out.tools).toEqual(['asset://tool/a@1'])
  })
})
