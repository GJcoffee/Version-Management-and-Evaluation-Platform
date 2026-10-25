import { describe, expect, it } from 'vitest'
import type { AgentSpec } from '@/api/types'
import { countSpecChanges, diffChangeCount, flattenSpec, pickChangeCount, unpinSpec } from './specDiff'

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

describe('pickChangeCount', () => {
  const server = { summary: { add: 1, remove: 0, replace: 2, asset_changed: 1 } }
  it('diffChangeCount 是 summary 各项之和', () => {
    expect(diffChangeCount(server)).toBe(4)
    expect(diffChangeCount({ summary: { add: 0, remove: 0, replace: 0, asset_changed: 0 } })).toBe(0)
  })
  it('没有基础版本：全部为新增', () => {
    expect(pickChangeCount({ hasBase: false, saved: true, server, local: 7 })).toEqual({ count: null, approx: false })
  })
  it('编辑器内容已保存且有服务端 diff：用服务端结果，不标约', () => {
    expect(pickChangeCount({ hasBase: true, saved: true, server, local: 9 })).toEqual({ count: 4, approx: false })
  })
  it('有未保存修改：回退为前端估算并标约（服务端结果即使存在也不用）', () => {
    expect(pickChangeCount({ hasBase: true, saved: false, server, local: 9 })).toEqual({ count: 9, approx: true })
  })
  it('已保存但服务端结果还没到 / 失败：先用前端估算；估算也没有时为 undefined', () => {
    expect(pickChangeCount({ hasBase: true, saved: true, server: undefined, local: 3 })).toEqual({ count: 3, approx: true })
    expect(pickChangeCount({ hasBase: true, saved: true, server: undefined, local: undefined })).toEqual({
      count: undefined,
      approx: false,
    })
  })
})
