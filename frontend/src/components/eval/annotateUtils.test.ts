import { describe, expect, it } from 'vitest'
import type { EvalItemOut, EvalScoreOut, EvaluatorOut } from '@/api/evalTypes'
import {
  annotateProgress,
  buildHumanEntries,
  digitToScore,
  humanEvaluators,
  initialDraft,
  isAnnotatable,
  isPendingHuman,
  scaleOptions,
  scoreInRange,
} from './annotateUtils'

const score = (over: Partial<EvalScoreOut>): EvalScoreOut => ({
  evaluator_key: 'human',
  status: 'pending_human',
  score: null,
  raw_score: null,
  passed: null,
  reason: null,
  ...over,
})
const item = (scores: EvalScoreOut[], status: EvalItemOut['status'] = 'done') => ({ scores, status }) as Pick<EvalItemOut, 'scores' | 'status'>

describe('humanEvaluators', () => {
  it('只取 human 类型；通过线缺省为量程 60% 处', () => {
    const evs: EvaluatorOut[] = [
      { key: 'exact', type: 'exact_match' },
      { key: 'h1', type: 'human', gate: true, scale: [1, 5], guideline: ' 看礼貌 ' },
      { key: 'h2', type: 'human', scale: [0, 10], pass_threshold: 8, guideline: '' },
    ]
    const hs = humanEvaluators(evs)
    expect(hs.map((h) => h.key)).toEqual(['h1', 'h2'])
    expect(hs[0]).toMatchObject({ scale: [1, 5], gate: true, passThreshold: 3.4, guideline: ' 看礼貌 ' })
    expect(hs[1]).toMatchObject({ scale: [0, 10], gate: false, passThreshold: 8, guideline: null })
  })
})

describe('量纲与数字键', () => {
  it('整数且不超过 11 档才渲染成按钮', () => {
    expect(scaleOptions([1, 5])).toEqual([1, 2, 3, 4, 5])
    expect(scaleOptions([0, 10])).toHaveLength(11)
    expect(scaleOptions([0, 11])).toBeNull()
    expect(scaleOptions([0, 1.5])).toBeNull()
  })

  it('数字键只在量纲内有效', () => {
    expect(digitToScore('4', [1, 5])).toBe(4)
    expect(digitToScore('0', [1, 5])).toBeNull()
    expect(digitToScore('9', [1, 5])).toBeNull()
    expect(digitToScore('a', [1, 5])).toBeNull()
    expect(digitToScore('0', [0, 3])).toBe(0)
    expect(digitToScore('2', [0, 1.5])).toBeNull()
  })

  it('范围校验', () => {
    expect(scoreInRange(3, [1, 5])).toBe(true)
    expect(scoreInRange(6, [1, 5])).toBe(false)
    expect(scoreInRange(Number.NaN, [1, 5])).toBe(false)
  })
})

describe('条目状态与草稿', () => {
  it('待评审 / 可评审判断', () => {
    const pending = item([score({})])
    const done = item([score({ status: 'done', raw_score: 4, reason: '好' })])
    const other = item([score({ evaluator_key: 'exact', status: 'done' })])
    expect(isPendingHuman(pending, ['human'])).toBe(true)
    expect(isPendingHuman(done, ['human'])).toBe(false)
    expect(isAnnotatable(done, ['human'])).toBe(true)
    expect(isAnnotatable(other, ['human'])).toBe(false)
    expect(isAnnotatable(item([score({})], 'failed'), ['human'])).toBe(false)
  })

  it('草稿初值：已评的沿用分数与备注，待评的为空', () => {
    expect(initialDraft(item([score({ status: 'done', raw_score: 4, reason: '好' })]), 'human')).toEqual({ score: 4, reason: '好' })
    expect(initialDraft(item([score({})]), 'human')).toEqual({ score: null, reason: '' })
  })

  it('提交条目：没打分的评估器不提交，备注和评审人为空不带', () => {
    const entries = buildHumanEntries('evi_1', { h1: { score: 4, reason: ' 不错 ' }, h2: { score: null, reason: '忽略' }, h3: { score: 0, reason: '' } }, ' 小王 ')
    expect(entries).toEqual([
      { item_id: 'evi_1', evaluator_key: 'h1', raw_score: 4, reason: '不错', annotator: '小王' },
      { item_id: 'evi_1', evaluator_key: 'h3', raw_score: 0, annotator: '小王' },
    ])
    expect(buildHumanEntries('evi_1', { h: { score: 1, reason: '' } }, '')[0]).toEqual({ item_id: 'evi_1', evaluator_key: 'h', raw_score: 1 })
  })
})

describe('进度', () => {
  it('已评 = 各 human 评估器的 n 之和，待评 = pending_human 之和', () => {
    const stats = { evaluators: { h1: { mean: 0.5, n: 3, pending_human: 2 }, h2: { mean: null, n: 0, pending_human: 5 }, exact: { mean: 1, n: 99 } } }
    expect(annotateProgress(stats, ['h1', 'h2'])).toEqual({ done: 3, pending: 7, total: 10 })
    expect(annotateProgress(null, ['h1'])).toBeNull()
  })
})
