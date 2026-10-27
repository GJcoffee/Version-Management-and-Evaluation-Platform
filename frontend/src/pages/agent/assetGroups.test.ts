import { describe, expect, it } from 'vitest'
import type { VersionAssetItem } from '@/api/types'
import { changeText, groupVersionAssets } from './assetGroups'

const item = (kind: VersionAssetItem['kind'], name: string, over: Partial<VersionAssetItem> = {}): VersionAssetItem => ({
  asset_id: `ast_${name}`,
  kind,
  name,
  rev: 1,
  asset_revision_id: `arv_${name}`,
  spec_paths: [],
  origin: null,
  changed_in_seq: 1,
  changed_in_version_id: 'ver_1',
  previous_rev: null,
  ...over,
})

describe('groupVersionAssets', () => {
  it('按 提示词 / Skill / MCP 插件 / 工具 / 知识库 / 数据库 / 应用配置 / 其他文件 的顺序分组，空组不返回，组内按名称排序', () => {
    const groups = groupVersionAssets([
      item('app_config', 'cfg'),
      item('file', 'logo'),
      item('skill', 'b-skill'),
      item('skill', 'a-skill'),
      item('prompt', 'sys'),
      item('database', 'db'),
      item('mcp', 'search'),
    ])
    expect(groups.map((g) => g.group.label)).toEqual(['提示词', 'Skill', 'MCP 插件', '数据库', '应用配置', '其他文件'])
    expect(groups[1].items.map((i) => i.name)).toEqual(['a-skill', 'b-skill'])
  })
  it('知识库组同时包含检索文本知识库和知识库配置；配置型 agent 的工具有自己的组', () => {
    const groups = groupVersionAssets([item('kb_config', 'kb'), item('knowledge', 'faq'), item('tool', 'query')])
    expect(groups.map((g) => [g.group.label, g.items.map((i) => i.kind)])).toEqual([
      ['工具', ['tool']],
      ['知识库', ['knowledge', 'kb_config']],
    ])
  })
})

describe('changeText', () => {
  it('首次出现 / 修订变化', () => {
    expect(changeText({ rev: 1, previous_rev: null })).toBe('r1（首次出现）')
    expect(changeText({ rev: 4, previous_rev: 3 })).toBe('r3 → r4')
  })
})
