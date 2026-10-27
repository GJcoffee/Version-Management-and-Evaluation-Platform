import { describe, expect, it } from 'vitest'
import { ASSET_KINDS, ASSET_KIND_LABEL, formatAssetRef, isAssetRefString, isSnapshotAsset, parseAssetRef } from './assetRef'

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
  it('快照入库的新类型（含带下划线的 kb_config / app_config）', () => {
    expect(parseAssetRef('asset://skill/crawl-engine@4')).toEqual({ kind: 'skill', name: 'crawl-engine', rev: 4 })
    expect(parseAssetRef('asset://app_config/bot-app-config@latest')).toEqual({ kind: 'app_config', name: 'bot-app-config', rev: 'latest' })
    for (const k of ASSET_KINDS) expect(ASSET_KIND_LABEL[k]).toBeTruthy()
    expect(ASSET_KIND_LABEL.mcp).toBe('MCP 插件')
  })
  it('来自快照的资产只读', () => {
    const rev = (meta: Record<string, unknown>) => ({ latest: { meta } }) as Parameters<typeof isSnapshotAsset>[0]
    expect(isSnapshotAsset(rev({ origin: 'snapshot' }))).toBe(true)
    expect(isSnapshotAsset(rev({ variables: [] }))).toBe(false)
    expect(isSnapshotAsset({ latest: null })).toBe(false)
  })
  it('格式化往返', () => {
    const r = { kind: 'tool' as const, name: 'a-b', rev: 5 }
    expect(parseAssetRef(formatAssetRef(r))).toEqual(r)
  })
})
