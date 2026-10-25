import { ApiError } from '@/api/client'

export interface FormattedError {
  message: string
  /** 逐条明细（字段错误、数据集行错误等） */
  lines: string[]
}

/** 把 ApiError 整理成可展示的主消息 + 明细（details.errors: [{path|line, message}]） */
export function formatApiError(e: unknown, fallback = '操作失败'): FormattedError {
  if (!(e instanceof ApiError)) return { message: e instanceof Error ? e.message : fallback, lines: [] }
  const lines: string[] = []
  const errs = (e.details as { errors?: unknown } | null)?.errors
  if (Array.isArray(errs)) {
    for (const it of errs.slice(0, 20)) {
      if (!it || typeof it !== 'object') continue
      const o = it as { path?: string; line?: number; message?: string }
      const where = o.line !== undefined ? `第 ${o.line} 行` : o.path
      const line = where ? `${where}：${o.message ?? ''}` : (o.message ?? '')
      // 主消息里已经包含的明细不重复显示（如 DATASET_PARSE_ERROR）
      if (!e.message.includes(line)) lines.push(line)
    }
    const total = (e.details as { total_errors?: number }).total_errors
    if (total && total > errs.length) lines.push(`…共 ${total} 处错误`)
  }
  return { message: e.message, lines }
}
