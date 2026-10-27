import type { AssetKind, VersionAssetItem } from '@/api/types'

export interface AssetGroupDef {
  key: string
  label: string
  kinds: AssetKind[]
}

/** Agent「资产」标签的分组顺序；知识库组同时包含检索文本（knowledge）和知识库配置（kb_config） */
export const ASSET_GROUPS: AssetGroupDef[] = [
  { key: 'prompt', label: '提示词', kinds: ['prompt'] },
  { key: 'skill', label: 'Skill', kinds: ['skill'] },
  { key: 'mcp', label: 'MCP 插件', kinds: ['mcp'] },
  { key: 'tool', label: '工具', kinds: ['tool'] },
  { key: 'knowledge', label: '知识库', kinds: ['knowledge', 'kb_config'] },
  { key: 'database', label: '数据库', kinds: ['database'] },
  { key: 'app_config', label: '应用配置', kinds: ['app_config'] },
  { key: 'other', label: '其他文件', kinds: ['file', 'dataset'] },
]

/** 按类型分组，保持分组顺序；没有资产的分组不返回，组内按名称排序 */
export function groupVersionAssets(items: VersionAssetItem[]): Array<{ group: AssetGroupDef; items: VersionAssetItem[] }> {
  return ASSET_GROUPS.map((group) => ({
    group,
    items: items.filter((i) => group.kinds.includes(i.kind)).sort((a, b) => a.name.localeCompare(b.name)),
  })).filter((g) => g.items.length > 0)
}

/** 「最近变化」的文字：首次出现，或 `r3 → r4` */
export function changeText(item: Pick<VersionAssetItem, 'rev' | 'previous_rev'>): string {
  return item.previous_rev === null ? `r${item.rev}（首次出现）` : `r${item.previous_rev} → r${item.rev}`
}
