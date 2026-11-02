import { api } from '@/api/client'
import type { EvalItemDetailOut, EvalItemOut, EvalRunDetailOut } from '@/api/evalTypes'
import type { Page } from '@/api/types'
import { formatDateTime } from '@/lib/format'
import { EVAL_STATUS_LABEL, isRemoteItem, itemVerdict, runDatasetLabel, runTitle, runVersionLabel, VERDICT_LABEL } from './evalUtils'

export type EvalExportFormat = 'csv' | 'json'

export interface EvalExportData {
  exported_at: string
  run: EvalRunDetailOut
  items: EvalItemDetailOut[]
}

/** 独立读取全部分页与完整详情，不受页面筛选、已加载页数及查询缓存影响。 */
export async function fetchEvalExport(
  runId: string,
  signal: AbortSignal,
  onProgress: (loaded: number, total: number) => void,
): Promise<EvalExportData> {
  signal.throwIfAborted()
  const run = await api.get<EvalRunDetailOut>(`/evals/${runId}`, { signal })
  const items: EvalItemDetailOut[] = []
  let cursor: string | undefined
  const cursors = new Set<string>()
  onProgress(0, run.progress.total)
  do {
    signal.throwIfAborted()
    const page = await api.get<Page<EvalItemOut>>(`/evals/${runId}/items`, {
      query: { limit: 200, cursor },
      signal,
    })
    // 限制并行详情请求，保留接口的用例 / 重复执行顺序。
    for (let offset = 0; offset < page.items.length; offset += 5) {
      signal.throwIfAborted()
      const details = await Promise.all(
        page.items.slice(offset, offset + 5).map((item) =>
          api.get<EvalItemDetailOut>(`/evals/${runId}/items/${item.id}`, { signal }),
        ),
      )
      items.push(...details)
      onProgress(items.length, run.progress.total)
    }
    cursor = page.next_cursor ?? undefined
    if (cursor) {
      if (cursors.has(cursor)) throw new Error('用例分页异常，请重试导出')
      cursors.add(cursor)
    }
  } while (cursor)
  signal.throwIfAborted()
  return { exported_at: new Date().toISOString(), run, items }
}

function csvCell(value: unknown): string {
  let text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)
  // 用例文本作为单元格内容，避免 Excel 把用户输入 / 模型输出当作公式执行。
  if (typeof value === 'string' && (/^\s*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text))) text = `'${text}`
  return `"${text.replace(/"/g, '""')}"`
}

export function evalExportCsv({ exported_at, run, items }: EvalExportData): string {
  const evaluatorKeys = [...new Set([
    ...run.evaluators.map((e) => e.key),
    ...items.flatMap((item) => item.scores.map((s) => s.evaluator_key)),
  ])]
  const headers = [
    '评测ID', '评测名称', 'Agent', '版本', '数据集', '评测状态', '创建时间', '开始时间', '结束时间', '导出时间',
    '汇总指标(JSON)', '用例执行ID', '用例标识', '重复序号', '执行状态', '判定', '完整输入(JSON)', '完整输出',
    '期望输出', '评分标准', '多轮对话(JSON)', '标签(JSON)', '综合评分', '缓存命中', '尝试次数', '错误',
    '耗时(ms)', '成本(元)', '输入tokens', '输出tokens', '会话ID', '评估器评分(JSON)',
    ...evaluatorKeys.flatMap((key) => [
      `${key}：分数`, `${key}：原始分数`, `${key}：是否通过`, `${key}：状态`, `${key}：理由`,
    ]),
  ]
  const meta = [
    run.id, runTitle(run), run.agent_name ?? run.agent_id, runVersionLabel(run), runDatasetLabel(run),
    EVAL_STATUS_LABEL[run.status], formatDateTime(run.created_at), formatDateTime(run.started_at),
    formatDateTime(run.finished_at), formatDateTime(exported_at), run.stats,
  ]
  const rows = items.map((item) => [
    ...meta, item.id, item.case_key, item.repeat_idx + 1, item.status, VERDICT_LABEL[itemVerdict(item)],
    item.case_detail.input, item.output, item.expected, item.case_detail.rubric, item.turns, item.tags,
    item.score, item.cached, item.attempts, item.error, item.latency_ms, isRemoteItem(item) ? null : item.cost,
    item.usage?.prompt_tokens, item.usage?.completion_tokens, item.session_id, item.scores,
    ...evaluatorKeys.flatMap((key) => {
      const score = item.scores.find((s) => s.evaluator_key === key)
      return [score?.score, score?.raw_score, score?.passed, score?.status, score?.reason]
    }),
  ])
  // UTF-8 BOM 便于 Excel 正确显示中文；RFC 4180 引号保留逗号、换行和双引号。
  return '\uFEFF' + [headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n'
}

export function downloadEvalExport(data: EvalExportData, format: EvalExportFormat): void {
  const text = format === 'csv' ? evalExportCsv(data) : JSON.stringify(data, null, 2)
  const blob = new Blob([text], { type: format === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8' })
  const title = runTitle(data.run).replace(/[<>:"/\\|?*]|\p{Cc}/gu, '_').slice(0, 80)
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `评测-${title}-${data.run.id}.${format}`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // 留出浏览器开始读取下载内容的时间。
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
