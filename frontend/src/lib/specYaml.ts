/**
 * 草稿编辑器的 YAML 工具：Spec <-> YAML 文本、按路径定位行号、局部修改（保留注释和排版）。
 * 文本（而非解析后的对象）是编辑器的唯一事实来源，这样 YAML 暂时写错时用户输入不会丢失。
 */
import {
  Document,
  LineCounter,
  isMap,
  isScalar,
  isSeq,
  parseDocument,
  stringify,
  type Node,
  type Pair,
} from 'yaml'
import type { CodeMarker } from '@/components/ui/CodeEditor'
import { parseAssetRef } from './assetRef'

export type PathSeg = string | number

/** Spec 对象 -> YAML 文本（不折行，多行字符串用块标量） */
export function specToYaml(spec: unknown): string {
  return stringify(spec, { lineWidth: 0, indent: 2 })
}

export interface YamlProblem {
  message: string
  line: number
  column: number
  endLine: number
  endColumn: number
}

export type ParsedYaml =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; errors: YamlProblem[] }

/** 解析 YAML：顶层必须是映射（空文本视为空对象）；语法错误带行列 */
export function parseSpecYaml(text: string): ParsedYaml {
  const lc = new LineCounter()
  const doc = parseDocument(text, { lineCounter: lc, prettyErrors: false })
  if (doc.errors.length > 0) {
    return {
      ok: false,
      errors: doc.errors.map((e) => {
        const [a, b] = e.pos
        const s = lc.linePos(a)
        const t = lc.linePos(Math.max(a, b))
        return {
          message: e.message.split('\n')[0],
          line: s.line,
          column: s.col,
          endLine: t.line,
          endColumn: t.col,
        }
      }),
    }
  }
  const value = doc.toJS({ maxAliasCount: 100 }) as unknown
  if (value === null || value === undefined) return { ok: true, value: {} }
  if (typeof value !== 'object' || Array.isArray(value)) {
    return {
      ok: false,
      errors: [{ message: '顶层必须是映射（key: value）', line: 1, column: 1, endLine: 1, endColumn: 2 }],
    }
  }
  return { ok: true, value: value as Record<string, unknown> }
}

/**
 * 把 `model.params.temperature`、`tools[0]`、`tools.0.with`、`tools[query-order]` 拆成片段。
 * 方括号内的内容整体作为一个片段（可以是下标，也可以是资产名等身份键）。
 */
export function parsePath(path: string): string[] {
  const out: string[] = []
  let cur = ''
  for (let i = 0; i < path.length; i++) {
    const ch = path[i]
    if (ch === '.') {
      if (cur) out.push(cur)
      cur = ''
    } else if (ch === '[') {
      if (cur) out.push(cur)
      cur = ''
      const end = path.indexOf(']', i)
      const stop = end === -1 ? path.length : end
      out.push(path.slice(i + 1, stop))
      i = stop
    } else {
      cur += ch
    }
  }
  if (cur) out.push(cur)
  return out
}

function keyOf(pair: Pair): unknown {
  return isScalar(pair.key) ? pair.key.value : pair.key
}

/** 序列元素是否匹配身份键：资产引用的资产名、内置工具名、固化引用的资产名 */
function itemMatchesIdentity(item: unknown, id: string): boolean {
  if (isScalar(item)) {
    const ref = parseAssetRef(item.value)
    return ref ? ref.name === id : item.value === id
  }
  if (isMap(item)) {
    const builtin = item.get('builtin')
    if (builtin === id) return true
    const pin = item.get('$asset')
    if (isMap(pin) && pin.get('name') === id) return true
  }
  return false
}

/**
 * 路径 -> 1 起始的行号。找不到完整路径时退回到最深的已匹配祖先（例如必填字段缺失时指向其父对象）。
 * 无法定位任何节点时返回 null。
 */
export function lineOfPath(text: string, path: string): number | null {
  const lc = new LineCounter()
  const doc = parseDocument(text, { lineCounter: lc, prettyErrors: false })
  if (!doc.contents) return null
  const toLine = (offset: number | undefined) => (offset === undefined ? null : lc.linePos(offset).line)

  let node: Node | null = doc.contents as Node
  let best: number | null = null
  for (const seg of parsePath(path)) {
    if (isMap(node)) {
      const pair: Pair | undefined =
        node.items.find((p) => String(keyOf(p)) === seg) ??
        // `snapshot.files[skill/crawl-engine]`：键是文件路径，值是资产引用，按引用的 类型/名称 匹配
        node.items.find((p) => {
          const ref = isScalar(p.value) ? parseAssetRef(p.value.value) : null
          return ref !== null && `${ref.kind}/${ref.name}` === seg
        })
      if (!pair) break
      const keyNode = pair.key as Node | undefined
      best = toLine(keyNode?.range?.[0]) ?? best
      node = (pair.value as Node | null) ?? null
    } else if (isSeq(node)) {
      let item: unknown
      if (/^\d+$/.test(seg)) item = node.items[Number(seg)]
      else item = node.items.find((it) => itemMatchesIdentity(it, seg))
      if (!item) break
      const n = item as Node
      best = toLine(n.range?.[0]) ?? best
      node = n
    } else {
      break
    }
    if (!node) break
  }
  if (best === null && doc.contents.range) best = toLine(doc.contents.range[0])
  return best
}

/** 把 validate 返回的 errors / warnings 映射为编辑器标注 */
export function issuesToMarkers(
  text: string,
  issues: Array<{ path: string; message: string }>,
  severity: 'error' | 'warning',
): CodeMarker[] {
  return issues.map((it) => ({
    line: lineOfPath(text, it.path) ?? 1,
    message: it.path ? `${it.path}：${it.message}` : it.message,
    severity,
  }))
}

/** YAML 语法错误 -> 编辑器标注 */
export function yamlProblemsToMarkers(problems: YamlProblem[]): CodeMarker[] {
  return problems.map((p) => ({
    line: p.line,
    column: p.column,
    endLine: p.endLine,
    endColumn: Math.max(p.endColumn, p.column + 1),
    message: p.message,
    severity: 'error' as const,
  }))
}

/**
 * 在 YAML 文本上局部修改一个路径（保留注释与排版）。value 为 undefined 时删除该键。
 * 文本有语法错误时原样返回。
 */
export function setInYaml(text: string, path: PathSeg[], value: unknown): string {
  const doc = parseDocument(text, { prettyErrors: false })
  if (doc.errors.length > 0) return text
  if (!doc.contents) {
    // 空文档：建一个空映射
    const fresh = new Document({})
    if (value !== undefined) fresh.setIn(path, value)
    return fresh.toString({ lineWidth: 0 })
  }
  if (value === undefined) doc.deleteIn(path)
  else doc.setIn(path, value)
  return doc.toString({ lineWidth: 0, indent: 2 })
}

/** 读取路径上的值（用于表单展示），缺失返回 undefined */
export function getIn(obj: unknown, path: PathSeg[]): unknown {
  let cur: unknown = obj
  for (const seg of path) {
    if (cur === null || cur === undefined || typeof cur !== 'object') return undefined
    cur = (cur as Record<string | number, unknown>)[seg]
  }
  return cur
}
