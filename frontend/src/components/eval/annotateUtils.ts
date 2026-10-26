import type { EvalHumanScoreEntry, EvalItemOut, EvalScoreOut, EvalStatsOut, EvaluatorOut } from '@/api/evalTypes'

/** 人工评审评估器的配置（来自评测详情里的 evaluators） */
export interface HumanEvaluatorConfig {
  key: string
  scale: [number, number]
  guideline: string | null
  /** 通过线（量纲内的原始分）；未设置时取量纲 60% 处 */
  passThreshold: number
  gate: boolean
}

export function humanEvaluators(evaluators: readonly EvaluatorOut[]): HumanEvaluatorConfig[] {
  return evaluators
    .filter((e) => e.type === 'human')
    .map((e) => {
      const sc = Array.isArray(e.scale) && e.scale.length === 2 ? (e.scale as number[]) : [1, 5]
      const lo = Number(sc[0])
      const hi = Number(sc[1])
      const th = typeof e.pass_threshold === 'number' ? e.pass_threshold : lo + 0.6 * (hi - lo)
      return {
        key: e.key,
        scale: [lo, hi],
        guideline: typeof e.guideline === 'string' && e.guideline.trim() ? e.guideline : null,
        passThreshold: th,
        gate: e.gate === true,
      }
    })
}

/** 量纲是不超过 11 档的整数时，返回每一档（渲染成按钮、支持数字键）；否则返回 null（用数字输入框） */
export function scaleOptions(scale: readonly [number, number]): number[] | null {
  const [lo, hi] = scale
  if (!Number.isInteger(lo) || !Number.isInteger(hi) || hi <= lo || hi - lo > 10) return null
  return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
}

/** 数字键 -> 分数：只有 0–9 且落在整数量纲内才有效 */
export function digitToScore(key: string, scale: readonly [number, number]): number | null {
  if (!/^[0-9]$/.test(key)) return null
  const opts = scaleOptions(scale)
  const n = Number(key)
  return opts && opts.includes(n) ? n : null
}

export function humanScoreOf(item: Pick<EvalItemOut, 'scores'>, key: string): EvalScoreOut | undefined {
  return item.scores.find((s) => s.evaluator_key === key && (s.status === 'pending_human' || s.status === 'done'))
}

/** 条目是否还有等待人工打分的评分 */
export function isPendingHuman(item: Pick<EvalItemOut, 'scores'>, keys: readonly string[]): boolean {
  return item.scores.some((s) => keys.includes(s.evaluator_key) && s.status === 'pending_human')
}

/** 条目是否参与人工评审（有 human 评估器的评分行，待评或已评） */
export function isAnnotatable(item: Pick<EvalItemOut, 'scores' | 'status'>, keys: readonly string[]): boolean {
  return item.status === 'done' && item.scores.some((s) => keys.includes(s.evaluator_key) && (s.status === 'pending_human' || s.status === 'done'))
}

export interface ScoreDraft {
  score: number | null
  reason: string
}

/** 草稿的初值：已经打过分的沿用原分数与备注 */
export function initialDraft(item: Pick<EvalItemOut, 'scores'>, key: string): ScoreDraft {
  const s = humanScoreOf(item, key)
  return s && s.status === 'done' ? { score: s.raw_score, reason: s.reason ?? '' } : { score: null, reason: '' }
}

/** 草稿 -> 提交条目：没选分数的评估器不提交；备注为空不带 */
export function buildHumanEntries(itemId: string, drafts: Record<string, ScoreDraft>, annotator: string): EvalHumanScoreEntry[] {
  const out: EvalHumanScoreEntry[] = []
  for (const [key, d] of Object.entries(drafts)) {
    if (d.score === null) continue
    const e: EvalHumanScoreEntry = { item_id: itemId, evaluator_key: key, raw_score: d.score }
    if (d.reason.trim()) e.reason = d.reason.trim()
    if (annotator.trim()) e.annotator = annotator.trim()
    out.push(e)
  }
  return out
}

/** 打分是否在量纲内（后端也会校验） */
export function scoreInRange(score: number, scale: readonly [number, number]): boolean {
  return Number.isFinite(score) && score >= scale[0] && score <= scale[1]
}

export interface AnnotateProgress {
  done: number
  pending: number
  total: number
}

/** 人工评审进度：已打分 / 总评分数（各 human 评估器的 n 与 pending_human 之和）。评测未结束、没有统计时为 null */
export function annotateProgress(stats: Pick<EvalStatsOut, 'evaluators'> | null | undefined, keys: readonly string[]): AnnotateProgress | null {
  if (!stats) return null
  let done = 0
  let pending = 0
  for (const k of keys) {
    const e = stats.evaluators?.[k]
    if (!e) continue
    done += e.n ?? 0
    pending += e.pending_human ?? 0
  }
  return { done, pending, total: done + pending }
}
