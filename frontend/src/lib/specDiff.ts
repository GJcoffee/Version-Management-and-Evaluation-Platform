/**
 * 草稿相对基础版本的改动数。
 *
 * 首选服务端的 `GET /versions/diff`（右侧传草稿 id，口径与版本 diff 页一致：按身份键匹配列表、子树新增 / 删除算一处），
 * 但它只能看到已保存的草稿内容；编辑器里有未保存的修改时，回退为这里的前端计算（`countSpecChanges`，按叶子路径统计，
 * 口径略有不同，所以界面上标"约"）。
 */
import type { AgentSpec, VersionDiff } from '@/api/types'

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

/** 服务端 diff 的改动数（summary 各项之和） */
export function diffChangeCount(diff: Pick<VersionDiff, 'summary'>): number {
  return Object.values(diff.summary).reduce((a, b) => a + b, 0)
}

export interface ChangeCount {
  /** null：没有基础版本（全部为新增）；undefined：暂时算不出来 */
  count: number | null | undefined
  /** 前端估算（含未保存修改 / 服务端结果不可用），口径与版本 diff 页略有差异 */
  approx: boolean
}

/**
 * 选择状态栏要显示的改动数。
 * @param server 服务端 diff（仅在编辑器内容与已保存草稿一致时才有意义）
 * @param local 前端按干跑校验预览算出的改动数（预览不可用时为 undefined）
 */
export function pickChangeCount(opts: {
  hasBase: boolean
  saved: boolean
  server: Pick<VersionDiff, 'summary'> | undefined
  local: number | undefined
}): ChangeCount {
  if (!opts.hasBase) return { count: null, approx: false }
  if (opts.saved && opts.server) return { count: diffChangeCount(opts.server), approx: false }
  return { count: opts.local, approx: opts.local !== undefined }
}
