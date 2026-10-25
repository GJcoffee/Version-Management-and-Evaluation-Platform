import type { AssetKind } from '@/api/types'

export const ASSET_KINDS: AssetKind[] = ['prompt', 'tool', 'knowledge', 'dataset', 'file']

export const ASSET_KIND_LABEL: Record<AssetKind, string> = {
  prompt: '提示词',
  tool: '工具',
  knowledge: '知识库',
  dataset: '数据集',
  file: '文件',
}

export interface ParsedAssetRef {
  kind: AssetKind
  name: string
  /** 'latest' 或具体修订号 */
  rev: 'latest' | number
}

const REF_RE = /^asset:\/\/(prompt|tool|knowledge|dataset|file)\/([^@\s]+)@(latest|\d+)$/

/** 解析 `asset://{kind}/{name}@{latest|rev}`；不是资产引用（如内联文本）返回 null */
export function parseAssetRef(value: unknown): ParsedAssetRef | null {
  if (typeof value === 'string') {
    const m = REF_RE.exec(value.trim())
    if (!m) return null
    return { kind: m[1] as AssetKind, name: m[2], rev: m[3] === 'latest' ? 'latest' : Number(m[3]) }
  }
  if (value && typeof value === 'object' && '$asset' in value) {
    const a = (value as { $asset: { kind: AssetKind; name: string; rev: number } }).$asset
    return { kind: a.kind, name: a.name, rev: a.rev }
  }
  return null
}

export function formatAssetRef(r: ParsedAssetRef): string {
  return `asset://${r.kind}/${r.name}@${r.rev}`
}

/** 是否为草稿里的字符串引用（不含固化形式） */
export function isAssetRefString(value: unknown): value is string {
  return typeof value === 'string' && REF_RE.test(value.trim())
}
