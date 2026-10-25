/** 前端侧的 Spec 对比：统计草稿相对基础版本的改动数（后端 versions/diff 只接受版本，不接受草稿）。 */
import type { AgentSpec } from '@/api/types'

/** 不参与哈希、不存入版本的显示字段 */
const DISPLAY_KEYS = new Set(['name', 'description', 'notes'])

type Leaf = string | number | boolean | null

/** 展开成 path -> 叶子值。资产固化引用按 kind/name + 内容哈希比较（哈希相同视为未变化，与后端一致）。 */
export function flattenSpec(spec: unknown, prefix = '', out: Record<string, Leaf> = {}, top = true): Record<string, Leaf> {
  if (spec === null || typeof spec !== 'object') {
    out[prefix] = spec as Leaf
    return out
  }
  if (!Array.isArray(spec) && '$asset' in (spec as object)) {
    const a = (spec as { $asset: { kind: string; name: string; hash: string } }).$asset
    out[prefix] = `asset:${a.kind}/${a.name}#${a.hash}`
    return out
  }
  if (Array.isArray(spec)) {
    if (spec.length === 0) out[prefix] = '[]'
    spec.forEach((v, i) => flattenSpec(v, `${prefix}[${i}]`, out, false))
    return out
  }
  const entries = Object.entries(spec as Record<string, unknown>).filter(([k]) => !(top && DISPLAY_KEYS.has(k)))
  if (entries.length === 0 && prefix) out[prefix] = '{}'
  for (const [k, v] of entries) flattenSpec(v, prefix ? `${prefix}.${k}` : k, out, false)
  return out
}

/** 两个（都已固化的）Spec 之间有多少个叶子路径不同 */
export function countSpecChanges(base: AgentSpec | unknown, current: AgentSpec | unknown): number {
  const a = flattenSpec(base)
  const b = flattenSpec(current)
  let n = 0
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (!(k in a) || !(k in b) || a[k] !== b[k]) n++
  }
  return n
}

/** 把固化引用 `{ $asset }` 换成 `asset://kind/name@rev` 字符串，便于阅读和对比显示 */
export function unpinSpec<T>(spec: T): T {
  if (Array.isArray(spec)) return spec.map((v) => unpinSpec(v)) as unknown as T
  if (spec && typeof spec === 'object') {
    const o = spec as Record<string, unknown>
    if ('$asset' in o && Object.keys(o).length === 1) {
      const a = o.$asset as { kind: string; name: string; rev: number }
      return `asset://${a.kind}/${a.name}@${a.rev}` as unknown as T
    }
    return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, unpinSpec(v)])) as T
  }
  return spec
}
