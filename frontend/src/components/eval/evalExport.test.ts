import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EvalItemDetailOut, EvalRunDetailOut } from '@/api/evalTypes'
import { parseCsv } from '@/lib/csv'
import { mockApi, type MockCall } from '@/test/mockApi'
import { downloadEvalExport, evalExportCsv, fetchEvalExport, type EvalExportData } from './evalExport'

const run: EvalRunDetailOut = {
  id: 'evr_export', batch_id: 'evb_1', name: '评测/"测试"', agent_id: 'agt_1', agent_name: 'bot',
  version_id: 'ver_1', version_seq: 18, dataset_rev_id: 'arv_1', dataset_name: '多轮用例', dataset_rev: 2,
  case_filter: {}, evaluators: [{ key: 'judge', type: 'human', gate: true }], repeats: 201,
  concurrency: 5, cache_policy: 'refresh', status: 'completed_with_errors', error: null,
  progress: { total: 201, done: 200, failed: 1 }, stats: null,
  created_at: '2026-10-29T12:00:00Z', started_at: '2026-10-29T12:00:01Z',
  finished_at: '2026-10-29T12:01:00Z', batch_runs: [],
}
const item = (i: number): EvalItemDetailOut => ({
  id: `evi_${i}`, run_id: run.id, case_id: 'cas_1', case_key: '多轮, "测试"', repeat_idx: i,
  input_preview: '输入预览', output_preview: '输出预览', expected: { outputType: 'confirm' }, tags: ['p0', '多轮'],
  status: 'done', passed: false, score: 0, cached: false, attempts: 1, error: null, latency_ms: 0,
  cost: 0, usage: { prompt_tokens: 0, completion_tokens: 0 }, session_id: `ses_${i}`,
  scores: [{
    evaluator_key: 'judge', status: 'done', score: 0, raw_score: 1, passed: false,
    reason: '包含"引号",\n以及多行', detail: { criteria: ['完整'] }, annotator: '评审人',
  }],
  output: '完整输出'.repeat(200) + '\n第二行,"原文"',
  turns: [
    { idx: 0, input: { text: '第一轮' }, output: '第一轮输出', status: 'completed' },
    { idx: 1, input: '第二轮', output: { answer: '第二轮输出' }, status: 'completed' },
  ],
  case_detail: { input: { turns: ['完整输入'.repeat(100), '第二轮'] }, rubric: { criteria: ['准确'] } },
})
const data = (items = [item(0)]): EvalExportData => ({ exported_at: '2026-10-29T13:00:00Z', run, items })

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('评测完整结果读取', () => {
  it('超过一页时读取全部 201 条，补齐完整输出、多轮与评分，保留重复执行顺序', async () => {
    const all = Array.from({ length: 201 }, (_, i) => item(i))
    const api = mockApi({
      'GET /api/v1/evals/evr_export': run,
      'GET /api/v1/evals/evr_export/items': (call: MockCall) => {
        const params = new URL(call.url, 'http://localhost').searchParams
        expect(params.get('limit')).toBe('200')
        expect(params.has('class') || params.has('tag') || params.has('q')).toBe(false)
        return params.get('cursor') === 'page2'
          ? { items: all.slice(200), next_cursor: null }
          : { items: all.slice(0, 200), next_cursor: 'page2' }
      },
      'GET /api/v1/evals/evr_export/items/evi_\\d+': (call: MockCall) => all[Number(call.url.split('evi_')[1])],
    })
    const progress = vi.fn()
    const result = await fetchEvalExport(run.id, new AbortController().signal, progress)
    expect(result.items).toEqual(all)
    expect(result.run).toEqual(run)
    expect(progress).toHaveBeenLastCalledWith(201, 201)
    expect(api.find('GET', /\/items\?/)).toHaveLength(2)
    expect(api.find('GET', /\/items\/evi_/)).toHaveLength(201)
    expect(api.calls.every((c) => c.method === 'GET')).toBe(true)
  })

  it('任一详情读取失败时拒绝导出，不能悄悄丢弃结果', async () => {
    mockApi({
      'GET /api/v1/evals/evr_export': run,
      'GET /api/v1/evals/evr_export/items': { items: [item(0)], next_cursor: null },
      'GET /api/v1/evals/evr_export/items/evi_0': {
        __status: 500, body: { error: { code: 'READ_FAILED', message: '读取失败' } },
      },
    })
    await expect(fetchEvalExport(run.id, new AbortController().signal, vi.fn())).rejects.toThrow('读取失败')
  })

  it('取消后不再继续请求用例', async () => {
    const api = mockApi({ 'GET /api/v1/evals/evr_export': run })
    const controller = new AbortController()
    controller.abort()
    await expect(fetchEvalExport(run.id, controller.signal, vi.fn())).rejects.toMatchObject({ name: 'AbortError' })
    expect(api.find('GET', /\/items/)).toHaveLength(0)
  })
})

describe('CSV 评测明细', () => {
  it('中文 BOM、完整输出、结构化期望、多轮、评分理由、零值与 false 均被保留', () => {
    const text = evalExportCsv(data())
    expect(text.startsWith('\uFEFF')).toBe(true)
    const [headers, row] = parseCsv(text)
    const cells = Object.fromEntries(headers.map((h, i) => [h, row[i]]))
    expect(cells['完整输出']).toBe(item(0).output)
    expect(cells['用例标识']).toBe(item(0).case_key)
    expect(JSON.parse(cells['完整输入(JSON)'])).toEqual(item(0).case_detail.input)
    expect(JSON.parse(cells['期望输出'])).toEqual(item(0).expected)
    expect(JSON.parse(cells['多轮对话(JSON)'])).toEqual(item(0).turns)
    expect(JSON.parse(cells['评估器评分(JSON)'])).toEqual(item(0).scores)
    expect(cells['judge：理由']).toBe(item(0).scores[0].reason)
    expect(cells['judge：是否通过']).toBe('false')
    expect(cells['judge：分数']).toBe('0')
    expect(cells['重复序号']).toBe('1')
    expect(cells['成本(元)']).toBe('0')
    expect(cells['结束时间']).toMatch(/2026-10-29 \d{2}:01:00/)
    expect(headers).toHaveLength(row.length)
  })

  it('执行失败和待人工评分保留各自状态；公式文本以普通文本导出', () => {
    const failed = { ...item(0), status: 'failed' as const, passed: null, error: { message: '连接失败' } }
    const pending = {
      ...item(1), passed: null, output: '=HYPERLINK("test")',
      scores: [{ evaluator_key: 'judge', status: 'pending_human' as const, score: null, raw_score: null, passed: null, reason: null }],
    }
    const [headers, a, b] = parseCsv(evalExportCsv(data([failed, pending])))
    expect(a[headers.indexOf('判定')]).toBe('失败')
    expect(JSON.parse(a[headers.indexOf('错误')])).toEqual(failed.error)
    expect(b[headers.indexOf('判定')]).toBe('待评分')
    expect(b[headers.indexOf('judge：状态')]).toBe('pending_human')
    expect(b[headers.indexOf('judge：分数')]).toBe('')
    expect(b[headers.indexOf('完整输出')]).toBe(`'${pending.output}`)
  })

  it('没有用例时仍可生成 CSV 表头', () => {
    expect(parseCsv(evalExportCsv(data([])))).toHaveLength(1)
  })
})

describe('浏览器下载', () => {
  it('JSON 保留全部原始字段，文件名清理非法字符，并回收下载 URL', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const create = vi.fn<(blob: Blob) => string>().mockReturnValue('blob:export')
      const revoke = vi.fn()
      vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL: create, revokeObjectURL: revoke }))
      let filename = ''
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { filename = this.download })
      const source = data()
      downloadEvalExport(source, 'json')
      expect(filename).toBe('评测-评测__测试_-evr_export.json')
      const blob = create.mock.calls[0][0]
      expect(blob.type).toBe('application/json;charset=utf-8')
      const text = await new Promise<string>((resolve) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.readAsText(blob)
      })
      expect(JSON.parse(text)).toEqual(source)
      expect(document.querySelector('a[download]')).toBeNull()
      vi.advanceTimersByTime(1000)
      expect(revoke).toHaveBeenCalledWith('blob:export')
    } finally {
      vi.useRealTimers()
    }
  })
})
