import type {
  EvalItemError,
  EvalItemOut,
  EvalProgressOut,
  EvalRunOut,
  EvalRunStatusOut,
  EvaluatorInput,
  EvaluatorOut,
} from '@/api/evalTypes'
import { formatPercent } from '@/lib/format'

export const EVAL_STATUS_LABEL: Record<EvalRunStatusOut, string> = {
  queued: '排队中',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
  completed_with_errors: '部分失败',
  cancelled: '已取消',
  failed: '失败',
}

export const EVAL_STATUS_VARIANT = {
  queued: 'secondary',
  running: 'info',
  paused: 'warning',
  completed: 'success',
  completed_with_errors: 'warning',
  cancelled: 'secondary',
  failed: 'danger',
} as const

/** 列表页的状态筛选项（value 直接作为 `status` 查询参数） */
export const EVAL_STATUS_FILTERS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'queued,running,paused', label: '进行中' },
  { value: 'completed', label: '已完成' },
  { value: 'completed_with_errors', label: '部分失败' },
  { value: 'cancelled', label: '已取消' },
  { value: 'failed', label: '失败' },
]

export function isRunActive(status: EvalRunStatusOut): boolean {
  return status === 'queued' || status === 'running' || status === 'paused'
}

/** 已处理的 Item 数：完成、失败、取消都算（取消的不再处理） */
export function processedCount(p: EvalProgressOut | undefined | null): number {
  if (!p) return 0
  return (p.done ?? 0) + (p.failed ?? 0) + (p.cancelled ?? 0)
}

/** 进度 0..1；total 为 0 时返回 0 */
export function progressRatio(p: EvalProgressOut | undefined | null): number {
  if (!p || !p.total) return 0
  return Math.min(1, processedCount(p) / p.total)
}

/** 版本显示：v13（没有序号时退回 id 缩写） */
export function runVersionLabel(r: Pick<EvalRunOut, 'version_seq' | 'version_id'>): string {
  return r.version_seq !== null && r.version_seq !== undefined ? `v${r.version_seq}` : r.version_id.slice(-6)
}

export function runDatasetLabel(r: Pick<EvalRunOut, 'dataset_name' | 'dataset_rev'>): string {
  if (!r.dataset_name) return '-'
  return r.dataset_rev !== null && r.dataset_rev !== undefined ? `${r.dataset_name}@${r.dataset_rev}` : r.dataset_name
}

export function runTitle(r: Pick<EvalRunOut, 'name' | 'id' | 'agent_name' | 'version_seq' | 'version_id'>): string {
  return r.name?.trim() || `${r.agent_name ?? 'agent'} ${runVersionLabel(r)}`
}

/** 对比时谁在左：同一个 agent 且版本不同，旧版本在左；否则先创建的在左 */
export function orderForCompare<T extends Pick<EvalRunOut, 'agent_id' | 'version_seq' | 'created_at'>>(a: T, b: T): [T, T] {
  if (a.agent_id === b.agent_id && a.version_seq !== null && b.version_seq !== null && a.version_seq !== b.version_seq) {
    return a.version_seq < b.version_seq ? [a, b] : [b, a]
  }
  return a.created_at <= b.created_at ? [a, b] : [b, a]
}

export function compareHref(left: string, right: string): string {
  return `/evals/compare?left=${left}&right=${right}`
}

/** Item 的结论：执行中 / 通过 / 未通过 / 失败 */
export type ItemVerdict = 'pending' | 'passed' | 'not_passed' | 'failed' | 'cancelled'

export function itemVerdict(it: Pick<EvalItemOut, 'status' | 'passed'>): ItemVerdict {
  if (it.status === 'failed') return 'failed'
  if (it.status === 'cancelled') return 'cancelled'
  if (it.status === 'done') return it.passed === true ? 'passed' : 'not_passed'
  return 'pending'
}

export const VERDICT_LABEL: Record<ItemVerdict, string> = {
  pending: '执行中',
  passed: '通过',
  not_passed: '未通过',
  failed: '失败',
  cancelled: '已取消',
}

export function itemErrorText(e: EvalItemOut['error'] | EvalItemError | undefined): string | null {
  if (!e) return null
  if (typeof e === 'string') return e
  return [e.code, e.message].filter(Boolean).join('：') || '执行失败'
}

/** 把分数 0..1 显示为两位小数 */
export function formatScore(s: number | null | undefined): string {
  return s === null || s === undefined || Number.isNaN(s) ? '-' : s.toFixed(2)
}

/** 差值带符号：+0.12 / -0.30 / 0.00 */
export function formatSigned(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '-'
  const s = n.toFixed(digits)
  return n > 0 && Number(s) !== 0 ? `+${s}` : s
}

export function formatSignedPercent(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '-'
  const s = (n * 100).toFixed(digits)
  return n > 0 && Number(s) !== 0 ? `+${s}%` : `${s}%`
}

export type DeltaTone = 'good' | 'bad' | 'neutral'

/** 指标变化的好坏：越高越好（通过率）或越低越好（延迟、成本、tokens）。|Δ| 为 0 时中性 */
export function deltaTone(delta: number | null | undefined, higherIsBetter: boolean): DeltaTone {
  if (delta === null || delta === undefined || Number.isNaN(delta) || delta === 0) return 'neutral'
  return delta > 0 === higherIsBetter ? 'good' : 'bad'
}

export const TONE_CLASS: Record<DeltaTone, string> = {
  good: 'text-success',
  bad: 'text-danger',
  neutral: 'text-muted-foreground',
}

/** 分数徽章配色：用于单个评估器的分数 */
export function scoreTone(passed: boolean | null | undefined): 'success' | 'danger' | 'secondary' {
  return passed === true ? 'success' : passed === false ? 'danger' : 'secondary'
}

export function passRateText(r: Pick<EvalRunOut, 'stats'>): string {
  return r.stats ? formatPercent(r.stats.pass_rate, 1) : '-'
}

// ───────────────────────────── 评估器表单 ─────────────────────────────

export const DEFAULT_JUDGE_PROMPT = 'asset://prompt/judge-default@latest'

export type EvaluatorFormType = EvaluatorInput['type']

export const EVALUATOR_TYPE_LABEL: Record<EvaluatorFormType, string> = {
  exact_match: '精确匹配',
  contains: '包含',
  regex: '正则',
  llm_judge: 'LLM 评审',
}

/** 评估器的编辑状态（全部用字符串 / 布尔，便于受控表单；提交时再转成请求体） */
export interface EvaluatorForm {
  /** 前端自己的稳定标识，用作 React key */
  uid: string
  type: EvaluatorFormType
  key: string
  gate: boolean
  /** exact_match / contains：每行一个，留空则与用例的 expected 比较 */
  values: string
  normalize: string[]
  mode: 'all' | 'any'
  negate: boolean
  caseSensitive: boolean
  pattern: string
  flags: string[]
  provider: string
  model: string
  prompt: string | null
  scaleMin: string
  scaleMax: string
  passThreshold: string
}

let uidSeq = 0
const KEY_BASE: Record<EvaluatorFormType, string> = { exact_match: 'exact', contains: 'contains', regex: 'regex', llm_judge: 'judge' }

/** 生成不与已有重复的 key：exact、exact2、exact3… */
export function nextEvaluatorKey(type: EvaluatorFormType, existing: readonly string[]): string {
  const base = KEY_BASE[type]
  if (!existing.includes(base)) return base
  let n = 2
  while (existing.includes(`${base}${n}`)) n += 1
  return `${base}${n}`
}

export function newEvaluatorForm(type: EvaluatorFormType, existingKeys: readonly string[], defaults?: Partial<EvaluatorForm>): EvaluatorForm {
  uidSeq += 1
  return {
    uid: `ev${uidSeq}`,
    type,
    key: nextEvaluatorKey(type, existingKeys),
    gate: true,
    values: '',
    normalize: ['strip'],
    mode: 'all',
    negate: false,
    caseSensitive: true,
    pattern: '',
    flags: [],
    provider: '',
    model: '',
    prompt: DEFAULT_JUDGE_PROMPT,
    scaleMin: '1',
    scaleMax: '5',
    passThreshold: '',
    ...defaults,
  }
}

const splitLines = (s: string): string[] => s.split('\n').map((x) => x.trim()).filter((x) => x.length > 0)

/** 校验评估器列表，返回 `uid -> 错误` 与整体错误 */
export function validateEvaluators(list: readonly EvaluatorForm[]): { byUid: Record<string, string>; global: string | null } {
  const byUid: Record<string, string> = {}
  if (list.length === 0) return { byUid, global: '请至少添加一个评估器' }
  const seen = new Set<string>()
  for (const e of list) {
    const key = e.key.trim()
    if (!key) byUid[e.uid] = '请填写评估器 key'
    else if (!/^[A-Za-z0-9_.-]+$/.test(key)) byUid[e.uid] = 'key 只能包含字母、数字、_ . -'
    else if (seen.has(key)) byUid[e.uid] = 'key 重复'
    seen.add(key)
    if (byUid[e.uid]) continue
    if (e.type === 'regex' && !e.pattern.trim()) byUid[e.uid] = '请填写正则表达式'
    else if (e.type === 'llm_judge') {
      const lo = Number(e.scaleMin)
      const hi = Number(e.scaleMax)
      if (!e.provider || !e.model) byUid[e.uid] = '请选择评审用的 Provider 和模型'
      else if (!e.prompt) byUid[e.uid] = '请选择评审提示词'
      else if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo >= hi) byUid[e.uid] = '量程的最小值必须小于最大值'
      else if (e.passThreshold.trim() !== '') {
        const t = Number(e.passThreshold)
        if (!Number.isFinite(t) || t < lo || t > hi) byUid[e.uid] = `通过阈值必须在 ${lo} 到 ${hi} 之间`
      }
    }
  }
  return { byUid, global: null }
}

/** 编辑状态 -> 请求体里的评估器配置 */
export function evaluatorToInput(e: EvaluatorForm): EvaluatorInput {
  const base = { key: e.key.trim(), gate: e.gate }
  switch (e.type) {
    case 'exact_match':
      return { ...base, type: 'exact_match', values: splitLines(e.values), normalize: e.normalize }
    case 'contains':
      return {
        ...base,
        type: 'contains',
        values: splitLines(e.values),
        mode: e.mode,
        negate: e.negate,
        case_sensitive: e.caseSensitive,
      }
    case 'regex':
      return { ...base, type: 'regex', pattern: e.pattern, flags: e.flags.join('') }
    case 'llm_judge': {
      const out: EvaluatorInput = {
        ...base,
        type: 'llm_judge',
        provider: e.provider,
        model: e.model,
        prompt: e.prompt ?? DEFAULT_JUDGE_PROMPT,
        scale: [Number(e.scaleMin), Number(e.scaleMax)],
      }
      if (e.passThreshold.trim() !== '') out.pass_threshold = Number(e.passThreshold)
      return out
    }
  }
}

/** 一句话描述评估器（用于报告页头部） */
export function describeEvaluator(e: EvaluatorOut): string {
  const label = (EVALUATOR_TYPE_LABEL as Record<string, string>)[e.type] ?? e.type
  return `${e.key}（${label}）`
}
