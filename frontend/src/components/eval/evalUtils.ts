import type {
  EvalBudget,
  EvalGateResult,
  EvalItemError,
  EvalItemOut,
  EvalProgressOut,
  EvalRunOut,
  EvalStatsOut,
  EvalRunStatusOut,
  EvaluatorInput,
  EvaluatorOut,
  TraceAssertInput,
} from '@/api/evalTypes'
import { formatCost, formatPercent, formatTokens } from '@/lib/format'

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
/** unscored：已执行完，但没有可用的门禁评分（例如等待人工评审、评审出错） */
export type ItemVerdict = 'pending' | 'passed' | 'not_passed' | 'unscored' | 'failed' | 'cancelled'

export function itemVerdict(it: Pick<EvalItemOut, 'status' | 'passed'>): ItemVerdict {
  if (it.status === 'failed') return 'failed'
  if (it.status === 'cancelled') return 'cancelled'
  if (it.status === 'done') return it.passed === true ? 'passed' : it.passed === false ? 'not_passed' : 'unscored'
  return 'pending'
}

export const VERDICT_LABEL: Record<ItemVerdict, string> = {
  pending: '执行中',
  passed: '通过',
  not_passed: '未通过',
  unscored: '待评分',
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

// ───────────────────────────── 运行控制 ─────────────────────────────

export interface RunActions {
  canPause: boolean
  canResume: boolean
  canRetryFailed: boolean
  canRescore: boolean
  canCancel: boolean
}

/** 当前状态下哪些操作可用（与后端 pause / resume / retry-failed / rescore / cancel 的状态要求一致） */
export function runActions(status: EvalRunStatusOut, failedCount: number): RunActions {
  const finished = status === 'completed' || status === 'completed_with_errors'
  return {
    canPause: status === 'queued' || status === 'running',
    canResume: status === 'paused',
    canRetryFailed: failedCount > 0 && (finished || status === 'running' || status === 'paused'),
    canRescore: finished,
    canCancel: isRunActive(status),
  }
}

/** 预算的一句话描述：`¥2.0000 / 100k tokens`；都没设置返回 null */
export function describeBudget(b: EvalBudget | undefined | null): string | null {
  const parts: string[] = []
  if (b?.max_cost !== null && b?.max_cost !== undefined) parts.push(formatCost(b.max_cost))
  if (b?.max_tokens !== null && b?.max_tokens !== undefined) parts.push(`${formatTokens(b.max_tokens)} tokens`)
  return parts.length > 0 ? parts.join(' / ') : null
}

/** 暂停状态的提示文案；非暂停返回 null */
export function pauseNotice(
  status: EvalRunStatusOut,
  reason: string | null | undefined,
  budget: EvalBudget | undefined | null,
  spentCost: number | undefined | null,
): string | null {
  if (status !== 'paused') return null
  if (reason === 'budget') {
    const b = describeBudget(budget)
    const spent = spentCost !== null && spentCost !== undefined ? `，已新增成本 ${formatCost(spentCost)}` : ''
    return `评测因超出预算自动暂停${b ? `（预算 ${b}${spent}）` : ''}。调高预算后可继续，已完成的结果会保留。`
  }
  return '评测已暂停：不再开始新的用例，正在执行的会跑完。点击「继续」恢复。'
}

/** 把输入框文本转成预算数值：空 = 不设置（undefined）；非法返回 'invalid' */
export function parseBudgetInput(s: string, integer: boolean): number | undefined | 'invalid' {
  const t = s.trim()
  if (t === '') return undefined
  const n = Number(t)
  if (!Number.isFinite(n) || n < 0 || (integer && !Number.isInteger(n))) return 'invalid'
  return n
}

/** 重新评分结果的一句话说明（按 evaluator_hash 增量） */
export function describeRescore(r: { rescored_items: number; evaluators_changed: string[]; rebased: boolean }): string {
  if (r.rescored_items === 0) return '所有评估器的配置都没有变化，没有需要重新评分的用例。'
  const changed = r.evaluators_changed.length > 0 ? `评估器 ${r.evaluators_changed.join('、')} 的配置有变化，` : '重新计算出错或过期的评分，'
  return `${changed}正在为 ${r.rescored_items} 个用例重新评分；未变化的评估器沿用原结果，不会重新调用被测 agent。`
}

/** 门禁结论的颜色 */
export function gateVariant(r: Pick<EvalGateResult, 'passed'>): 'success' | 'danger' {
  return r.passed ? 'success' : 'danger'
}

// ───────────────────────────── 评估器表单 ─────────────────────────────

export const DEFAULT_JUDGE_PROMPT = 'asset://prompt/judge-default@latest'

export type EvaluatorFormType = EvaluatorInput['type']

export const EVALUATOR_TYPE_LABEL: Record<EvaluatorFormType, string> = {
  exact_match: '精确匹配',
  contains: '包含',
  regex: '正则',
  json_schema: 'JSON Schema',
  trace: '行为断言',
  python: 'Python 自定义',
  llm_judge: 'LLM 评审',
  human: '人工评审',
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
  /** json_schema：JSON Schema 文本；extract 为从回复中提取 JSON 的方式 */
  schemaText: string
  extract: 'auto' | 'codeblock'
  /** trace 断言：工具名每行一个，其余为数字（留空 = 不断言） */
  toolCalled: string
  toolNotCalled: string
  maxSteps: string
  maxLatencyMs: string
  maxTokens: string
  /** python：`module:function` 与超时（秒） */
  entry: string
  timeoutS: string
  provider: string
  model: string
  prompt: string | null
  scaleMin: string
  scaleMax: string
  passThreshold: string
  /** llm_judge：同一条回复评审的次数，取平均 */
  samples: string
  /** human：评审标准，标注页常驻显示 */
  guideline: string
  /** 表单不编辑、但需要原样带回去的字段（例如 python 的 workdir、llm_judge 的 params） */
  extra: Record<string, unknown>
}

let uidSeq = 0
const KEY_BASE: Record<EvaluatorFormType, string> = {
  exact_match: 'exact',
  contains: 'contains',
  regex: 'regex',
  json_schema: 'schema',
  trace: 'trace',
  python: 'python',
  llm_judge: 'judge',
  human: 'human',
}

export const DEFAULT_SCHEMA_TEXT = '{\n  "type": "object"\n}'

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
    // 人工评审默认不参与通过判定（与后端一致），需要时手动勾选
    gate: type !== 'human',
    values: '',
    normalize: ['strip'],
    mode: 'all',
    negate: false,
    caseSensitive: true,
    pattern: '',
    flags: [],
    schemaText: DEFAULT_SCHEMA_TEXT,
    extract: 'auto',
    toolCalled: '',
    toolNotCalled: '',
    maxSteps: '',
    maxLatencyMs: '',
    maxTokens: '',
    entry: '',
    timeoutS: '30',
    provider: '',
    model: '',
    prompt: DEFAULT_JUDGE_PROMPT,
    scaleMin: '1',
    scaleMax: '5',
    passThreshold: '',
    samples: '1',
    guideline: '',
    extra: {},
    ...defaults,
  }
}

const splitLines = (s: string): string[] => s.split('\n').map((x) => x.trim()).filter((x) => x.length > 0)

/** 解析 JSON Schema 文本：必须是 JSON 对象 */
export function parseSchemaText(text: string): { value: Record<string, unknown> | null; error: string | null } {
  if (!text.trim()) return { value: null, error: '请填写 JSON Schema' }
  try {
    const v: unknown = JSON.parse(text)
    if (v === null || typeof v !== 'object' || Array.isArray(v)) return { value: null, error: 'JSON Schema 必须是 JSON 对象' }
    return { value: v as Record<string, unknown>, error: null }
  } catch (e) {
    return { value: null, error: `JSON Schema 不是合法的 JSON：${e instanceof Error ? e.message : String(e)}` }
  }
}

const isNonNegInt = (s: string) => /^\d+$/.test(s.trim())

/** 校验量程与通过阈值（llm_judge / human 共用）；返回错误文案或 null */
function validateScale(e: EvaluatorForm): string | null {
  const lo = Number(e.scaleMin)
  const hi = Number(e.scaleMax)
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo >= hi) return '量程的最小值必须小于最大值'
  if (e.passThreshold.trim() !== '') {
    const t = Number(e.passThreshold)
    if (!Number.isFinite(t) || t < lo || t > hi) return `通过阈值必须在 ${lo} 到 ${hi} 之间`
  }
  return null
}

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
    else if (e.type === 'json_schema') {
      const r = parseSchemaText(e.schemaText)
      if (r.error) byUid[e.uid] = r.error
    } else if (e.type === 'trace') {
      const numbers = [e.maxSteps, e.maxLatencyMs, e.maxTokens]
      if (numbers.some((n) => n.trim() !== '' && !isNonNegInt(n))) byUid[e.uid] = '步数 / 耗时 / tokens 上限必须是不小于 0 的整数'
      else if (splitLines(e.toolCalled).length + splitLines(e.toolNotCalled).length === 0 && numbers.every((n) => n.trim() === '')) {
        byUid[e.uid] = '至少需要设置一项断言'
      }
    } else if (e.type === 'python') {
      const [mod, sep, fn] = e.entry.trim().split(/(:)/)
      const t = Number(e.timeoutS)
      if (!mod || sep !== ':' || !fn) byUid[e.uid] = '入口必须是 module:function 格式'
      else if (!Number.isFinite(t) || t <= 0 || t > 3600) byUid[e.uid] = '超时必须在 0 到 3600 秒之间'
    } else if (e.type === 'llm_judge') {
      const samples = Number(e.samples)
      if (!e.provider || !e.model) byUid[e.uid] = '请选择评审用的 Provider 和模型'
      else if (!e.prompt) byUid[e.uid] = '请选择评审提示词'
      else if (!Number.isInteger(samples) || samples < 1 || samples > 10) byUid[e.uid] = '采样次数必须是 1 到 10 的整数'
      else {
        const err = validateScale(e)
        if (err) byUid[e.uid] = err
      }
    } else if (e.type === 'human') {
      const err = validateScale(e)
      if (err) byUid[e.uid] = err
    }
  }
  return { byUid, global: null }
}

const optInt = (s: string): number | undefined => (s.trim() === '' ? undefined : Number(s))

/** 编辑状态 -> 请求体里的评估器配置 */
export function evaluatorToInput(e: EvaluatorForm): EvaluatorInput {
  const base = { ...e.extra, key: e.key.trim(), gate: e.gate }
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
    case 'json_schema':
      return { ...base, type: 'json_schema', schema: parseSchemaText(e.schemaText).value ?? {}, extract: e.extract }
    case 'trace': {
      const assert: TraceAssertInput = {}
      const called = splitLines(e.toolCalled)
      const notCalled = splitLines(e.toolNotCalled)
      if (called.length > 0) assert.tool_called = called
      if (notCalled.length > 0) assert.tool_not_called = notCalled
      const steps = optInt(e.maxSteps)
      const latency = optInt(e.maxLatencyMs)
      const tokens = optInt(e.maxTokens)
      if (steps !== undefined) assert.max_steps = steps
      if (latency !== undefined) assert.max_latency_ms = latency
      if (tokens !== undefined) assert.max_tokens = tokens
      return { ...base, type: 'trace', assert }
    }
    case 'python':
      return { ...base, type: 'python', entry: e.entry.trim(), timeout_s: Number(e.timeoutS) }
    case 'llm_judge': {
      const out: Extract<EvaluatorInput, { type: 'llm_judge' }> = {
        ...base,
        type: 'llm_judge',
        provider: e.provider,
        model: e.model,
        prompt: e.prompt ?? DEFAULT_JUDGE_PROMPT,
        scale: [Number(e.scaleMin), Number(e.scaleMax)],
      }
      // 配置整体替换，省略即默认 1 次；只有多次采样才写出
      if (Number(e.samples) > 1) out.samples = Number(e.samples)
      if (e.passThreshold.trim() !== '') out.pass_threshold = Number(e.passThreshold)
      return out
    }
    case 'human': {
      const out: Extract<EvaluatorInput, { type: 'human' }> = { ...base, type: 'human', scale: [Number(e.scaleMin), Number(e.scaleMax)] }
      if (e.guideline.trim()) out.guideline = e.guideline
      if (e.passThreshold.trim() !== '') out.pass_threshold = Number(e.passThreshold)
      return out
    }
  }
}

/** 每种类型表单自己编辑的字段；其余字段进 `extra`，原样带回 */
const HANDLED_FIELDS: Record<EvaluatorFormType, string[]> = {
  exact_match: ['values', 'normalize'],
  contains: ['values', 'mode', 'negate', 'case_sensitive'],
  regex: ['pattern', 'flags'],
  json_schema: ['schema', 'extract'],
  trace: ['assert'],
  python: ['entry', 'timeout_s'],
  llm_judge: ['provider', 'model', 'prompt', 'scale', 'pass_threshold', 'samples'],
  human: ['scale', 'guideline', 'pass_threshold'],
}

const strLines = (v: unknown): string => (Array.isArray(v) ? v.map(String).join('\n') : typeof v === 'string' ? v : '')
const numStr = (v: unknown): string => (typeof v === 'number' ? String(v) : '')

/**
 * 把详情里返回的评估器配置还原成编辑状态（用于「重新评分」时在现有配置上修改）。
 * 表单不认识的类型返回 null。
 */
export function evaluatorFromOut(o: EvaluatorOut, existingKeys: readonly string[] = []): EvaluatorForm | null {
  if (!(o.type in EVALUATOR_TYPE_LABEL)) return null
  const type = o.type as EvaluatorFormType
  const handled = new Set([...HANDLED_FIELDS[type], 'key', 'type', 'gate'])
  const extra: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(o)) if (!handled.has(k) && v !== null && v !== undefined) extra[k] = v
  const form = newEvaluatorForm(type, existingKeys, { key: o.key, gate: o.gate ?? type !== 'human', extra })
  const scale = Array.isArray(o.scale) ? (o.scale as unknown[]) : null
  if (scale && scale.length === 2) {
    form.scaleMin = String(scale[0])
    form.scaleMax = String(scale[1])
  }
  switch (type) {
    case 'exact_match':
      form.values = strLines(o.values)
      form.normalize = Array.isArray(o.normalize) ? (o.normalize as string[]) : []
      break
    case 'contains':
      form.values = strLines(o.values)
      form.mode = o.mode === 'any' ? 'any' : 'all'
      form.negate = o.negate === true
      form.caseSensitive = o.case_sensitive !== false
      break
    case 'regex':
      form.pattern = typeof o.pattern === 'string' ? o.pattern : ''
      form.flags = Array.isArray(o.flags) ? (o.flags as string[]) : typeof o.flags === 'string' ? o.flags.split('') : []
      break
    case 'json_schema':
      form.schemaText = JSON.stringify(o.schema ?? {}, null, 2)
      form.extract = o.extract === 'codeblock' ? 'codeblock' : 'auto'
      break
    case 'trace': {
      const a = (o.assert ?? {}) as Record<string, unknown>
      form.toolCalled = strLines(a.tool_called)
      form.toolNotCalled = strLines(a.tool_not_called)
      form.maxSteps = numStr(a.max_steps)
      form.maxLatencyMs = numStr(a.max_latency_ms)
      form.maxTokens = numStr(a.max_tokens)
      break
    }
    case 'python':
      form.entry = typeof o.entry === 'string' ? o.entry : ''
      form.timeoutS = numStr(o.timeout_s) || '30'
      break
    case 'llm_judge':
      form.provider = typeof o.provider === 'string' ? o.provider : ''
      form.model = typeof o.model === 'string' ? o.model : ''
      form.prompt = typeof o.prompt === 'string' ? o.prompt : DEFAULT_JUDGE_PROMPT
      form.passThreshold = numStr(o.pass_threshold)
      form.samples = numStr(o.samples) || '1'
      break
    case 'human':
      form.guideline = typeof o.guideline === 'string' ? o.guideline : ''
      form.passThreshold = numStr(o.pass_threshold)
      break
  }
  return form
}

/** 一句话描述评估器（用于报告页头部） */
export function describeEvaluator(e: EvaluatorOut): string {
  const label = (EVALUATOR_TYPE_LABEL as Record<string, string>)[e.type] ?? e.type
  return `${e.key}（${label}）`
}

/**
 * 重新评分要提交的评估器配置：没改动的评估器原样带回服务端保存的配置（避免表单往返造成的数值格式差异
 * 使 evaluator_hash 变化而被误判为「有变化」），改动过的用表单生成。
 */
export function buildRescoreConfigs(originals: readonly EvaluatorOut[], baseline: readonly EvaluatorForm[], current: readonly EvaluatorForm[]): EvaluatorInput[] {
  return current.map((f) => {
    const i = baseline.findIndex((b) => b.uid === f.uid)
    if (i >= 0 && JSON.stringify(evaluatorToInput(f)) === JSON.stringify(evaluatorToInput(baseline[i]))) {
      return originals[i] as unknown as EvaluatorInput
    }
    return evaluatorToInput(f)
  })
}

// ───────────────────────────── 报告统计 ─────────────────────────────

/** pass@k 按 k 从小到大排序：`[["1", 0.82], ["3", 0.91]]` */
export function sortedPassAtK(p: Record<string, number> | undefined | null): Array<[number, number]> {
  return Object.entries(p ?? {})
    .map(([k, v]): [number, number] => [Number(k), v])
    .filter(([k, v]) => Number.isFinite(k) && typeof v === 'number')
    .sort((a, b) => a[0] - b[0])
}

export interface TagRow {
  tag: string
  passRate: number | null
  cases: number
}

/** 按标签分组的通过率，最弱的在前（便于发现薄弱环节）；没有通过率的排最后 */
export function sortedTagRows(byTag: Record<string, { pass_rate: number | null; cases: number }> | undefined | null): TagRow[] {
  return Object.entries(byTag ?? {})
    .map(([tag, v]) => ({ tag, passRate: v.pass_rate ?? null, cases: v.cases }))
    .sort((a, b) => {
      if (a.passRate === null && b.passRate === null) return a.tag.localeCompare(b.tag)
      if (a.passRate === null) return 1
      if (b.passRate === null) return -1
      return a.passRate - b.passRate || a.tag.localeCompare(b.tag)
    })
}

/** 通过率的颜色：≥80% 绿、≥50% 黄、其余红 */
export function passRateTone(r: number | null | undefined): 'success' | 'warning' | 'danger' | 'muted' {
  if (r === null || r === undefined || Number.isNaN(r)) return 'muted'
  return r >= 0.8 ? 'success' : r >= 0.5 ? 'warning' : 'danger'
}

// ───────────────────────────── 对比的统计结论 ─────────────────────────────

/** p 值显示：<0.001 / 0.034 / - */
export function formatPValue(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return '-'
  return p < 0.001 ? '<0.001' : p.toFixed(3)
}

export interface Significance {
  tone: DeltaTone
  text: string
}

/** 置信区间的解读：区间整体在 0 的一侧才算有统计依据；区间包含 0 不能说明两者一样，只是证据不足 */
export function interpretCi(ci: readonly [number, number] | null | undefined): Significance {
  if (!ci) return { tone: 'neutral', text: '可配对的用例太少，无法给出置信区间' }
  if (ci[0] > 0) return { tone: 'good', text: '区间整体大于 0：整体分数的提升有统计依据' }
  if (ci[1] < 0) return { tone: 'bad', text: '区间整体小于 0：整体分数的下降有统计依据' }
  return { tone: 'neutral', text: '区间包含 0：现有用例不足以断定整体有差异' }
}

/** 符号检验的解读（双侧，显著性水平 0.05） */
export function interpretSignTest(p: number | null | undefined, wins: number, losses: number): Significance {
  if (p === null || p === undefined) return { tone: 'neutral', text: '没有改善或退化的用例，无法做符号检验' }
  if (p < 0.05) return wins > losses ? { tone: 'good', text: '改善的用例显著多于退化的用例（p < 0.05）' } : { tone: 'bad', text: '退化的用例显著多于改善的用例（p < 0.05）' }
  return { tone: 'neutral', text: '改善与退化的用例数差异不显著（p ≥ 0.05）' }
}

/** 等待人工评审的评分总数（各 human 评估器的 pending_human 之和） */
export function pendingHumanCount(stats: Pick<EvalStatsOut, 'evaluators'> | null | undefined): number {
  return Object.values(stats?.evaluators ?? {}).reduce((n, e) => n + (e.pending_human ?? 0), 0)
}
