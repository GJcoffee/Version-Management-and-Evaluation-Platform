import type { ProviderCreate, ProviderModel, ProviderOut, ProviderUpdate } from '@/api/types'

/** Provider 对话框的表单逻辑（纯函数，便于单测） */

export interface ModelRow {
  rid: number
  name: string
  context_window: string
  price_in: string
  price_out: string
}

export interface ProviderFormState {
  name: string
  base_url: string
  /** 新密钥；空串表示不修改 */
  api_key: string
  /** 编辑时点击"清除密钥" */
  clearKey: boolean
  headers: Record<string, string>
  models: ModelRow[]
  rpm_limit: string
  tpm_limit: string
  max_concurrency: string
}

export type FormErrors = Record<string, string>

let ridSeq = 0
export const nextRid = () => ++ridSeq

const str = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v))

export function modelToRow(m: ProviderModel): ModelRow {
  return {
    rid: nextRid(),
    name: m.name,
    context_window: str(m.context_window),
    price_in: str(m.price_in),
    price_out: str(m.price_out),
  }
}

export function emptyModelRow(name = ''): ModelRow {
  return { rid: nextRid(), name, context_window: '', price_in: '', price_out: '' }
}

export function toFormState(p?: ProviderOut): ProviderFormState {
  return {
    name: p?.name ?? '',
    base_url: p?.base_url ?? '',
    api_key: '',
    clearKey: false,
    headers: { ...(p?.headers ?? {}) },
    models: (p?.models ?? []).map(modelToRow),
    rpm_limit: str(p?.rpm_limit),
    tpm_limit: str(p?.tpm_limit),
    max_concurrency: p ? String(p.max_concurrency) : '8',
  }
}

/** 空串 -> null；非法 -> NaN */
function parseNum(s: string, integer: boolean): number | null {
  const t = s.trim()
  if (t === '') return null
  const n = Number(t)
  if (!Number.isFinite(n)) return NaN
  if (integer && !Number.isInteger(n)) return NaN
  return n
}

export function validateForm(f: ProviderFormState): FormErrors {
  const e: FormErrors = {}
  if (!f.name.trim()) e.name = '请输入名称'
  if (!f.base_url.trim()) e.base_url = '请输入 Base URL'
  else if (!/^https?:\/\/\S+$/i.test(f.base_url.trim())) e.base_url = '需以 http:// 或 https:// 开头'

  for (const [key, label] of [
    ['rpm_limit', 'RPM'],
    ['tpm_limit', 'TPM'],
  ] as const) {
    const n = parseNum(f[key], true)
    if (n !== null && (Number.isNaN(n) || n < 1)) e[key] = `${label} 需为正整数，留空表示不限`
  }
  const mc = parseNum(f.max_concurrency, true)
  if (mc === null || Number.isNaN(mc) || mc < 1) e.max_concurrency = '最大并发需为正整数'

  const seen = new Set<string>()
  f.models.forEach((m, i) => {
    const name = m.name.trim()
    if (!name) e[`models.${i}.name`] = '模型名不能为空'
    else if (seen.has(name)) e[`models.${i}.name`] = '模型名重复'
    seen.add(name)
    const cw = parseNum(m.context_window, true)
    if (cw !== null && (Number.isNaN(cw) || cw < 1)) e[`models.${i}.context_window`] = '需为正整数'
    for (const k of ['price_in', 'price_out'] as const) {
      const n = parseNum(m[k], false)
      if (n !== null && (Number.isNaN(n) || n < 0)) e[`models.${i}.${k}`] = '需为非负数'
    }
  })
  return e
}

/** 表单行 -> API 的 models（空字段省略）。调用前须先通过 validateForm */
export function rowsToModels(rows: ModelRow[]): ProviderModel[] {
  return rows.map((r) => {
    const out: ProviderModel = { name: r.name.trim() }
    const cw = parseNum(r.context_window, true)
    const pi = parseNum(r.price_in, false)
    const po = parseNum(r.price_out, false)
    if (cw !== null) out.context_window = cw
    if (pi !== null) out.price_in = pi
    if (po !== null) out.price_out = po
    return out
  })
}

export function toCreateBody(f: ProviderFormState): ProviderCreate {
  const body: ProviderCreate = {
    name: f.name.trim(),
    base_url: f.base_url.trim(),
    headers: f.headers,
    models: rowsToModels(f.models),
    rpm_limit: parseNum(f.rpm_limit, true),
    tpm_limit: parseNum(f.tpm_limit, true),
    max_concurrency: parseNum(f.max_concurrency, true) ?? 8,
  }
  if (f.api_key) body.api_key = f.api_key
  return body
}

const norm = (m: ProviderModel) => ({
  name: m.name,
  context_window: m.context_window ?? null,
  price_in: m.price_in ?? null,
  price_out: m.price_out ?? null,
})
const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/**
 * 只包含发生变化的字段（PATCH 部分更新）。
 * api_key：填写了新值 -> 设置；点击"清除" -> 空串（删除密钥）；否则不发送。
 * 没有任何变化时返回空对象。
 */
export function toUpdateBody(f: ProviderFormState, p: ProviderOut): ProviderUpdate {
  const full = toCreateBody({ ...f, api_key: '' })
  const body: ProviderUpdate = {}
  if (full.name !== p.name) body.name = full.name
  if (full.base_url !== p.base_url) body.base_url = full.base_url
  if (f.api_key) body.api_key = f.api_key
  else if (f.clearKey && p.has_api_key) body.api_key = ''
  if (!sameJson(Object.entries(full.headers ?? {}).sort(), Object.entries(p.headers).sort())) body.headers = full.headers
  if (!sameJson((full.models ?? []).map(norm), p.models.map(norm))) body.models = full.models
  if (full.rpm_limit !== p.rpm_limit) body.rpm_limit = full.rpm_limit
  if (full.tpm_limit !== p.tpm_limit) body.tpm_limit = full.tpm_limit
  if (full.max_concurrency !== p.max_concurrency) body.max_concurrency = full.max_concurrency
  return body
}

/** 把测试连接返回的模型名追加到表单（已存在的忽略） */
export function appendModelNames(rows: ModelRow[], names: string[]): ModelRow[] {
  const have = new Set(rows.map((r) => r.name.trim()))
  const added = names.filter((n, i) => n && !have.has(n) && names.indexOf(n) === i).map((n) => emptyModelRow(n))
  return added.length ? [...rows, ...added] : rows
}
