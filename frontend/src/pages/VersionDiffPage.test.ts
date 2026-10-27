import { describe, expect, it } from 'vitest'
import type { SpecChange, VersionAssetRef } from '@/api/types'
import { assetChangeInfo, assetChangeText, findAssetForPath, findLeftAsset, groupChanges, groupOfPath } from './VersionDiffPage'

const ch = (path: string, op: SpecChange['op'] = 'replace'): SpecChange => ({ path, op })

describe('变更导航', () => {
  it('按第一段分组，保持出现顺序', () => {
    expect(groupOfPath('tools[query-order]')).toBe('tools')
    expect(groupOfPath('model.params.temperature')).toBe('model')
    const g = groupChanges([ch('model.name'), ch('tools[a]', 'asset_changed'), ch('model.params.top_p'), ch('runtime')])
    expect(g.map((x) => [x.group, x.items.length])).toEqual([['model', 2], ['tools', 1], ['runtime', 1]])
  })
  it('asset_changed 路径对应到版本资产清单', () => {
    const assets: VersionAssetRef[] = [
      { spec_path: 'prompts.system', kind: 'prompt', name: 'sys', rev: 1, asset_revision_id: 'arv_p' },
      { spec_path: 'tools[faq]', kind: 'tool', name: 'faq', rev: 2, asset_revision_id: 'arv_t' },
      { spec_path: 'knowledge.inject[faq]', kind: 'knowledge', name: 'faq', rev: 3, asset_revision_id: 'arv_k' },
      {
        spec_path: 'tools[knowledge_search].with.sources[faq]',
        kind: 'knowledge',
        name: 'faq',
        rev: 3,
        asset_revision_id: 'arv_s',
      },
    ]
    expect(findAssetForPath(assets, 'prompts.system')?.asset_revision_id).toBe('arv_p')
    expect(findAssetForPath(assets, 'tools[faq]')?.asset_revision_id).toBe('arv_t')
    expect(findAssetForPath(assets, 'knowledge.inject[faq]')?.asset_revision_id).toBe('arv_k')
    expect(findAssetForPath(assets, 'tools[knowledge_search].with.sources[faq]')?.asset_revision_id).toBe('arv_s')
    expect(findAssetForPath(assets, 'tools[none]')).toBeUndefined()
  })
})

describe('资产变更按类型与名称显示', () => {
  it('asset_changed：类型 + 名称 + 修订变化（不依赖 Spec 路径）', () => {
    const c: SpecChange = {
      path: 'snapshot.files[skill/crawl-engine]',
      op: 'asset_changed',
      asset: { kind: 'skill', name: 'crawl-engine' },
      left: { rev: 3 },
      right: { rev: 4 },
    }
    expect(assetChangeInfo(c)).toEqual({ kind: 'skill', name: 'crawl-engine', revs: 'r3 → r4' })
  })
  it('add / remove：取引用里的类型、名称与修订', () => {
    const pin = { kind: 'mcp', name: 'search', rev: 2 }
    expect(assetChangeInfo({ path: 'snapshot.files[mcp/search]', op: 'add', right: pin })).toEqual({ kind: 'mcp', name: 'search', revs: 'r2' })
    expect(assetChangeInfo({ path: 'snapshot.files[mcp/search]', op: 'remove', left: pin })).toEqual({ kind: 'mcp', name: 'search', revs: 'r2' })
  })
  it('普通字段的变更不是资产变更', () => {
    expect(assetChangeInfo({ path: 'params.top_k', op: 'replace', left: 1, right: 2 })).toBeNull()
    expect(assetChangeInfo({ path: 'params.x', op: 'add', right: 1 })).toBeNull()
  })
})

describe('资产改名', () => {
  const renamed: SpecChange = {
    path: 'snapshot.files[skill/new-name]',
    op: 'asset_changed',
    asset: { kind: 'skill', name: 'new-name', renamed_from: 'old-name' },
    left: { rev: 3 },
    right: { rev: 4 },
  }
  it('显示为「Skill 旧名 → 新名 · r3 → r4」', () => {
    const info = assetChangeInfo(renamed)
    expect(info).toEqual({ kind: 'skill', name: 'new-name', renamedFrom: 'old-name', revs: 'r3 → r4' })
    expect(assetChangeText(info!)).toBe('Skill old-name → new-name · r3 → r4')
  })
  it('没改名时不带旧名', () => {
    const info = assetChangeInfo({ ...renamed, asset: { kind: 'skill', name: 'new-name' } })!
    expect(assetChangeText(info)).toBe('Skill new-name · r3 → r4')
  })
  it('左侧版本里按类型 + 旧名找资产（路径上是新名，左侧找不到）', () => {
    const left: VersionAssetRef[] = [
      { spec_path: 'snapshot.files[skill/old-name]', kind: 'skill', name: 'old-name', rev: 3, asset_revision_id: 'arv_old' },
    ]
    expect(findAssetForPath(left, renamed.path)).toBeUndefined()
    expect(findLeftAsset(left, renamed)?.asset_revision_id).toBe('arv_old')
  })
})
