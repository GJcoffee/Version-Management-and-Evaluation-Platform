import { describe, expect, it } from 'vitest'
import type { SpecChange, VersionAssetRef } from '@/api/types'
import { findAssetForPath, groupChanges, groupOfPath } from './VersionDiffPage'

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
      { spec_path: 'tools[0]', kind: 'tool', name: 'faq', rev: 2, asset_revision_id: 'arv_t' },
      { spec_path: 'knowledge.inject[0]', kind: 'knowledge', name: 'faq', rev: 3, asset_revision_id: 'arv_k' },
    ]
    expect(findAssetForPath(assets, 'prompts.system')?.asset_revision_id).toBe('arv_p')
    expect(findAssetForPath(assets, 'tools[faq]')?.asset_revision_id).toBe('arv_t')
    expect(findAssetForPath(assets, 'knowledge.inject[faq]')?.asset_revision_id).toBe('arv_k')
    expect(findAssetForPath(assets, 'tools[knowledge_search].with.sources[faq]')?.asset_revision_id).toBe('arv_k')
    expect(findAssetForPath(assets, 'tools[none]')).toBeUndefined()
  })
})
