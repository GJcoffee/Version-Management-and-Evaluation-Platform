import { describe, expect, it } from 'vitest'
import { formatAssetRef, isAssetRefString, parseAssetRef } from './assetRef'

describe('assetRef', () => {
  it('解析 latest 与具体修订', () => {
    expect(parseAssetRef('asset://prompt/cs-system@latest')).toEqual({ kind: 'prompt', name: 'cs-system', rev: 'latest' })
    expect(parseAssetRef('asset://tool/query-order@3')).toEqual({ kind: 'tool', name: 'query-order', rev: 3 })
  })
  it('内联文本和非法写法返回 null', () => {
    expect(parseAssetRef('你是一个助手')).toBeNull()
    expect(parseAssetRef('asset://bogus/x@1')).toBeNull()
    expect(parseAssetRef('asset://prompt/x')).toBeNull()
    expect(isAssetRefString('hello')).toBe(false)
  })
  it('固化形式', () => {
    expect(parseAssetRef({ $asset: { kind: 'knowledge', name: 'faq', rev: 2, hash: 'h' } })).toEqual({ kind: 'knowledge', name: 'faq', rev: 2 })
  })
  it('格式化往返', () => {
    const r = { kind: 'tool' as const, name: 'a-b', rev: 5 }
    expect(parseAssetRef(formatAssetRef(r))).toEqual(r)
  })
})
