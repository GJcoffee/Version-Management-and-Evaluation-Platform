import { describe, expect, it } from 'vitest'
import {
  deltaTone,
  evaluatorToInput,
  formatSigned,
  itemVerdict,
  newEvaluatorForm,
  nextEvaluatorKey,
  orderForCompare,
  processedCount,
  progressRatio,
  validateEvaluators,
} from './evalUtils'
import { versionRefsFromSelection } from './NewEvalDialog'
import { itemMatchesClass } from './EvalItemsTable'
import type { EvalItemOut } from '@/api/evalTypes'

describe('进度', () => {
  it('已处理数包含完成、失败、取消；比率不超过 1', () => {
    expect(processedCount({ total: 10, done: 3, failed: 1, cancelled: 2 })).toBe(6)
    expect(progressRatio({ total: 10, done: 3, failed: 1 })).toBeCloseTo(0.4)
    expect(progressRatio({ total: 0, done: 0, failed: 0 })).toBe(0)
    expect(progressRatio({ total: 2, done: 5, failed: 0 })).toBe(1)
    expect(progressRatio(null)).toBe(0)
  })
})

describe('对比辅助', () => {
  it('同 agent 旧版本在左', () => {
    const a = { agent_id: 'a', version_seq: 13, created_at: '2026-01-01' }
    const b = { agent_id: 'a', version_seq: 12, created_at: '2026-01-02' }
    expect(orderForCompare(a, b)).toEqual([b, a])
  })
  it('延迟 / 成本越低越好，通过率越高越好', () => {
    expect(deltaTone(-120, false)).toBe('good')
    expect(deltaTone(0.4, false)).toBe('bad')
    expect(deltaTone(0.02, true)).toBe('good')
    expect(deltaTone(-0.02, true)).toBe('bad')
    expect(deltaTone(0, true)).toBe('neutral')
    expect(deltaTone(null, true)).toBe('neutral')
  })
  it('带符号格式', () => {
    expect(formatSigned(0.123)).toBe('+0.12')
    expect(formatSigned(-0.3)).toBe('-0.30')
    expect(formatSigned(0.001)).toBe('0.00')
    expect(formatSigned(null)).toBe('-')
  })
})

const item = (over: Partial<EvalItemOut>): EvalItemOut => ({
  id: 'i',
  run_id: 'r',
  case_id: 'c',
  case_key: 'k',
  repeat_idx: 0,
  input_preview: null,
  output_preview: null,
  expected: null,
  tags: [],
  status: 'done',
  passed: true,
  score: 1,
  cached: false,
  attempts: 1,
  error: null,
  latency_ms: null,
  cost: null,
  usage: null,
  session_id: null,
  scores: [],
  ...over,
})

describe('用例结论与筛选', () => {
  it('结论', () => {
    expect(itemVerdict(item({}))).toBe('passed')
    expect(itemVerdict(item({ passed: false }))).toBe('not_passed')
    expect(itemVerdict(item({ status: 'failed', passed: null }))).toBe('failed')
    expect(itemVerdict(item({ status: 'running', passed: null }))).toBe('pending')
  })
  it('实时更新后按筛选隐藏不符合的行', () => {
    expect(itemMatchesClass(item({ passed: false }), 'passed')).toBe(false)
    expect(itemMatchesClass(item({ passed: false }), 'failed')).toBe(true)
    expect(itemMatchesClass(item({ status: 'failed', passed: null }), 'error')).toBe(true)
    expect(itemMatchesClass(item({ status: 'running', passed: null }), 'passed')).toBe(false)
    expect(itemMatchesClass(item({ status: 'running', passed: null }), '')).toBe(true)
  })
})

describe('评估器表单', () => {
  it('key 不重复', () => {
    expect(nextEvaluatorKey('exact_match', [])).toBe('exact')
    expect(nextEvaluatorKey('exact_match', ['exact', 'exact2'])).toBe('exact3')
  })

  it('校验：至少一个、必填项、key 唯一、量程', () => {
    expect(validateEvaluators([]).global).toBeTruthy()
    const re = newEvaluatorForm('regex', [])
    expect(validateEvaluators([re]).byUid[re.uid]).toMatch(/正则/)
    const j = newEvaluatorForm('llm_judge', [])
    expect(validateEvaluators([j]).byUid[j.uid]).toMatch(/Provider/)
    const j2 = { ...j, provider: 'fake', model: 'm', passThreshold: '9' }
    expect(validateEvaluators([j2]).byUid[j.uid]).toMatch(/阈值/)
    const a = newEvaluatorForm('exact_match', [])
    const b = { ...newEvaluatorForm('contains', ['exact']), key: 'exact' }
    expect(validateEvaluators([a, b]).byUid[b.uid]).toMatch(/重复/)
    expect(validateEvaluators([a]).byUid).toEqual({})
  })

  it('转成请求体', () => {
    expect(evaluatorToInput({ ...newEvaluatorForm('exact_match', []), values: ' a \n\nb', normalize: ['strip', 'lower'] })).toEqual({
      key: 'exact',
      gate: true,
      type: 'exact_match',
      values: ['a', 'b'],
      normalize: ['strip', 'lower'],
    })
    expect(evaluatorToInput({ ...newEvaluatorForm('regex', []), pattern: 'A\\d+', flags: ['i', 'm'] })).toMatchObject({ type: 'regex', pattern: 'A\\d+', flags: 'im' })
    const judge = evaluatorToInput({ ...newEvaluatorForm('llm_judge', []), provider: 'fake', model: 'fake-model', passThreshold: '4' })
    expect(judge).toEqual({
      key: 'judge',
      gate: true,
      type: 'llm_judge',
      provider: 'fake',
      model: 'fake-model',
      prompt: 'asset://prompt/judge-default@latest',
      scale: [1, 5],
      pass_threshold: 4,
    })
    const noThreshold = evaluatorToInput({ ...newEvaluatorForm('llm_judge', []), provider: 'fake', model: 'm' })
    expect('pass_threshold' in noThreshold).toBe(false)
  })
})

describe('版本引用', () => {
  it('版本 id、标签、latest 分别转成后端接受的引用', () => {
    expect(versionRefsFromSelection(['ver:ver_1', 'tag:prod', 'latest'], 'support')).toEqual(['ver_1', 'support:prod', 'support:latest'])
  })
})
