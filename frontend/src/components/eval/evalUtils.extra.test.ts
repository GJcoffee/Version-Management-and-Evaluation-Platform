import { describe, expect, it } from 'vitest'
import type { EvaluatorOut } from '@/api/evalTypes'
import {
  buildRescoreConfigs,
  describeBudget,
  describeRescore,
  evaluatorFromOut,
  evaluatorToInput,
  formatPValue,
  interpretCi,
  interpretSignTest,
  newEvaluatorForm,
  parseBudgetInput,
  parseSchemaText,
  passRateTone,
  pauseNotice,
  pendingHumanCount,
  runActions,
  sortedPassAtK,
  sortedTagRows,
  validateEvaluators,
} from './evalUtils'

describe('评估器表单：新增类型', () => {
  it('json_schema：必须是合法 JSON 对象；请求体里 schema 是对象', () => {
    const f = newEvaluatorForm('json_schema', [])
    expect(f.key).toBe('schema')
    expect(validateEvaluators([f]).byUid).toEqual({})
    expect(validateEvaluators([{ ...f, schemaText: '{oops' }]).byUid[f.uid]).toMatch(/不是合法的 JSON/)
    expect(validateEvaluators([{ ...f, schemaText: '[1]' }]).byUid[f.uid]).toMatch(/必须是 JSON 对象/)
    expect(parseSchemaText('  ').error).toMatch(/请填写/)
    const input = evaluatorToInput({ ...f, schemaText: '{"type":"object","required":["a"]}', extract: 'codeblock' })
    expect(input).toEqual({ key: 'schema', gate: true, type: 'json_schema', schema: { type: 'object', required: ['a'] }, extract: 'codeblock' })
  })

  it('trace：至少一项断言；数字必须是非负整数；只写出填了的断言', () => {
    const f = newEvaluatorForm('trace', [])
    expect(validateEvaluators([f]).byUid[f.uid]).toMatch(/至少需要设置一项断言/)
    expect(validateEvaluators([{ ...f, maxSteps: '-1' }]).byUid[f.uid]).toMatch(/非负|不小于 0/)
    expect(validateEvaluators([{ ...f, maxSteps: '1.5' }]).byUid[f.uid]).toMatch(/整数/)
    const ok = { ...f, toolCalled: 'query_order\n knowledge_search \n', toolNotCalled: 'delete_order', maxSteps: '5', maxLatencyMs: '' }
    expect(validateEvaluators([ok]).byUid).toEqual({})
    expect(evaluatorToInput(ok)).toEqual({
      key: 'trace',
      gate: true,
      type: 'trace',
      assert: { tool_called: ['query_order', 'knowledge_search'], tool_not_called: ['delete_order'], max_steps: 5 },
    })
  })

  it('python：入口必须是 module:function；超时范围', () => {
    const f = newEvaluatorForm('python', [])
    expect(validateEvaluators([f]).byUid[f.uid]).toMatch(/module:function/)
    expect(validateEvaluators([{ ...f, entry: 'mod' }]).byUid[f.uid]).toMatch(/module:function/)
    expect(validateEvaluators([{ ...f, entry: 'mod:' }]).byUid[f.uid]).toMatch(/module:function/)
    expect(validateEvaluators([{ ...f, entry: 'mod:fn', timeoutS: '0' }]).byUid[f.uid]).toMatch(/超时/)
    const ok = { ...f, entry: ' my_evals:check ', timeoutS: '12' }
    expect(validateEvaluators([ok]).byUid).toEqual({})
    expect(evaluatorToInput(ok)).toEqual({ key: 'python', gate: true, type: 'python', entry: 'my_evals:check', timeout_s: 12 })
  })

  it('llm_judge：samples 1–10 的整数；大于 1 才写进请求体', () => {
    const j = { ...newEvaluatorForm('llm_judge', []), provider: 'p', model: 'm' }
    expect(validateEvaluators([j]).byUid).toEqual({})
    expect(validateEvaluators([{ ...j, samples: '0' }]).byUid[j.uid]).toMatch(/采样次数/)
    expect(validateEvaluators([{ ...j, samples: '11' }]).byUid[j.uid]).toMatch(/采样次数/)
    expect(validateEvaluators([{ ...j, samples: '2.5' }]).byUid[j.uid]).toMatch(/采样次数/)
    expect('samples' in evaluatorToInput(j)).toBe(false)
    expect(evaluatorToInput({ ...j, samples: '3' })).toMatchObject({ type: 'llm_judge', samples: 3 })
  })

  it('human：默认不参与判定；量程与评审标准', () => {
    const f = newEvaluatorForm('human', [])
    expect(f.gate).toBe(false)
    expect(validateEvaluators([f]).byUid).toEqual({})
    expect(validateEvaluators([{ ...f, scaleMin: '5', scaleMax: '1' }]).byUid[f.uid]).toMatch(/量程/)
    expect(validateEvaluators([{ ...f, passThreshold: '9' }]).byUid[f.uid]).toMatch(/阈值/)
    expect(evaluatorToInput({ ...f, guideline: '看礼貌', passThreshold: '4', gate: true })).toEqual({
      key: 'human',
      gate: true,
      type: 'human',
      scale: [1, 5],
      guideline: '看礼貌',
      pass_threshold: 4,
    })
    expect('guideline' in evaluatorToInput(f)).toBe(false)
  })

  it('同类型多个评估器的 key 依次为 trace、trace2', () => {
    const a = newEvaluatorForm('trace', [])
    const b = newEvaluatorForm('trace', [a.key])
    expect([a.key, b.key]).toEqual(['trace', 'trace2'])
  })
})

describe('evaluatorFromOut：详情里的配置还原成表单', () => {
  const outs: EvaluatorOut[] = [
    { key: 'exact', type: 'exact_match', gate: true, values: ['a'], normalize: ['strip', 'lower'], field: 'regex:\\d+' },
    { key: 'kw', type: 'contains', gate: false, values: ['x', 'y'], mode: 'any', negate: true, case_sensitive: false },
    { key: 're', type: 'regex', gate: true, pattern: 'A\\d+', flags: ['i', 'm'] },
    { key: 'sc', type: 'json_schema', gate: true, schema: { type: 'object' }, extract: 'codeblock' },
    { key: 'tr', type: 'trace', gate: true, assert: { tool_called: ['a'], max_steps: 3 } },
    { key: 'py', type: 'python', gate: true, entry: 'm:f', timeout_s: 10, workdir: '/tmp/x' },
    { key: 'judge', type: 'llm_judge', gate: true, provider: 'p', model: 'm', prompt: 'asset://prompt/judge-default@2', scale: [1, 10], pass_threshold: 7, samples: 3, params: { temperature: 0.2 } },
    { key: 'hu', type: 'human', gate: false, scale: [0, 3], guideline: '标准', pass_threshold: null },
  ]

  it('每种类型都能还原，且再转成请求体后与原配置等价（含表单不编辑的字段）', () => {
    for (const o of outs) {
      const f = evaluatorFromOut(o)
      expect(f, o.type).not.toBeNull()
      const back = evaluatorToInput(f!) as unknown as Record<string, unknown>
      for (const [k, v] of Object.entries(o)) {
        if (v === null) continue
        expect(back[k], `${o.type}.${k}`).toEqual(k === 'flags' && Array.isArray(v) ? v.join('') : v)
      }
    }
  })

  it('不认识的类型返回 null', () => {
    expect(evaluatorFromOut({ key: 'x', type: 'magic' })).toBeNull()
  })

  it('buildRescoreConfigs：没改动的评估器原样带回，改动过的用表单生成', () => {
    const originals: EvaluatorOut[] = [
      { key: 'sc', type: 'json_schema', gate: true, schema: { type: 'number', minimum: 1.0 }, extract: 'auto' },
      { key: 'kw', type: 'contains', gate: true, values: ['a'], mode: 'all', negate: false, case_sensitive: true },
    ]
    const keys: string[] = []
    const baseline = originals.map((o) => {
      const f = evaluatorFromOut(o, keys)!
      keys.push(f.key)
      return f
    })
    const edited = baseline.map((f) => (f.key === 'kw' ? { ...f, values: 'a\nb' } : f))
    const out = buildRescoreConfigs(originals, baseline, edited)
    expect(out[0]).toBe(originals[0]) // 未改动：同一个对象，数值格式不受表单往返影响
    expect(out[1]).toMatchObject({ key: 'kw', type: 'contains', values: ['a', 'b'] })
    // 新增的评估器没有基线，用表单生成
    const added = newEvaluatorForm('regex', keys, { pattern: 'x' })
    expect(buildRescoreConfigs(originals, baseline, [...baseline, added])[2]).toMatchObject({ type: 'regex', pattern: 'x' })
  })
})

describe('运行控制', () => {
  it('各状态下可用的操作', () => {
    expect(runActions('running', 0)).toEqual({ canPause: true, canResume: false, canRetryFailed: false, canRescore: false, canCancel: true })
    expect(runActions('queued', 0).canPause).toBe(true)
    expect(runActions('paused', 2)).toEqual({ canPause: false, canResume: true, canRetryFailed: true, canRescore: false, canCancel: true })
    expect(runActions('completed_with_errors', 3)).toEqual({ canPause: false, canResume: false, canRetryFailed: true, canRescore: true, canCancel: false })
    expect(runActions('completed', 0)).toMatchObject({ canRetryFailed: false, canRescore: true })
    // 已取消 / 失败的评测不能重跑失败项或重新评分（后端会拒绝）
    expect(runActions('cancelled', 5)).toEqual({ canPause: false, canResume: false, canRetryFailed: false, canRescore: false, canCancel: false })
    expect(runActions('failed', 5).canRetryFailed).toBe(false)
  })

  it('预算描述与暂停提示', () => {
    expect(describeBudget({ max_cost: 2, max_tokens: 100000 })).toBe('¥2.0000 / 100k tokens')
    expect(describeBudget({ max_cost: null, max_tokens: null })).toBeNull()
    expect(describeBudget(undefined)).toBeNull()
    expect(pauseNotice('running', null, undefined, 0)).toBeNull()
    expect(pauseNotice('paused', 'manual', undefined, 0)).toMatch(/已暂停/)
    const t = pauseNotice('paused', 'budget', { max_cost: 1 }, 1.2345)!
    expect(t).toMatch(/超出预算自动暂停/)
    expect(t).toContain('¥1.0000')
    expect(t).toContain('¥1.2345')
  })

  it('预算输入：空 = 不设置，非法输入报 invalid', () => {
    expect(parseBudgetInput('', false)).toBeUndefined()
    expect(parseBudgetInput(' 1.5 ', false)).toBe(1.5)
    expect(parseBudgetInput('0', false)).toBe(0)
    expect(parseBudgetInput('-1', false)).toBe('invalid')
    expect(parseBudgetInput('abc', false)).toBe('invalid')
    expect(parseBudgetInput('1.5', true)).toBe('invalid')
    expect(parseBudgetInput('1000', true)).toBe(1000)
  })

  it('重新评分结果的说明', () => {
    expect(describeRescore({ rescored_items: 0, evaluators_changed: [], rebased: false })).toMatch(/没有变化/)
    const t = describeRescore({ rescored_items: 12, evaluators_changed: ['judge', 'kw'], rebased: false })
    expect(t).toContain('judge、kw')
    expect(t).toContain('12 个用例')
    expect(describeRescore({ rescored_items: 3, evaluators_changed: [], rebased: false })).toMatch(/出错或过期/)
  })
})

describe('报告统计辅助', () => {
  it('pass@k 按 k 数值排序（"10" 在 "3" 之后）', () => {
    expect(sortedPassAtK({ '10': 0.99, '1': 0.5, '3': 0.8 })).toEqual([
      [1, 0.5],
      [3, 0.8],
      [10, 0.99],
    ])
    expect(sortedPassAtK(undefined)).toEqual([])
  })

  it('按标签分组：最弱的在前，没有通过率的最后', () => {
    const rows = sortedTagRows({ p0: { pass_rate: 0.9, cases: 5 }, p1: { pass_rate: 0.4, cases: 3 }, none: { pass_rate: null, cases: 1 }, p2: { pass_rate: 0.4, cases: 9 } })
    expect(rows.map((r) => r.tag)).toEqual(['p1', 'p2', 'p0', 'none'])
    expect(rows[0]).toEqual({ tag: 'p1', passRate: 0.4, cases: 3 })
  })

  it('通过率配色', () => {
    expect([0.95, 0.8, 0.6, 0.2, null].map(passRateTone)).toEqual(['success', 'success', 'warning', 'danger', 'muted'])
  })

  it('等待人工评审的评分数是各评估器之和', () => {
    expect(pendingHumanCount({ evaluators: { a: { mean: null, pending_human: 2 }, b: { mean: 1 }, c: { mean: null, pending_human: 3 } } })).toBe(5)
    expect(pendingHumanCount(null)).toBe(0)
  })
})

describe('对比的统计结论', () => {
  it('p 值格式', () => {
    expect(formatPValue(0.0004)).toBe('<0.001')
    expect(formatPValue(0.03449)).toBe('0.034')
    expect(formatPValue(1)).toBe('1.000')
    expect(formatPValue(null)).toBe('-')
  })

  it('置信区间解读：区间不含 0 才有统计依据', () => {
    expect(interpretCi([0.02, 0.2]).tone).toBe('good')
    expect(interpretCi([-0.3, -0.01]).tone).toBe('bad')
    expect(interpretCi([-0.1, 0.2])).toMatchObject({ tone: 'neutral', text: expect.stringContaining('包含 0') })
    expect(interpretCi(null).text).toMatch(/太少/)
  })

  it('符号检验解读', () => {
    expect(interpretSignTest(0.01, 9, 1).tone).toBe('good')
    expect(interpretSignTest(0.01, 1, 9).tone).toBe('bad')
    expect(interpretSignTest(0.4, 5, 4).tone).toBe('neutral')
    expect(interpretSignTest(null, 0, 0).text).toMatch(/无法做符号检验/)
  })
})
