/** 数值格式化工具。空值（null/undefined/NaN）统一显示为 "-"。 */

const EMPTY = '-'

function isEmpty(v: number | null | undefined): v is null | undefined {
  return v === null || v === undefined || Number.isNaN(v)
}

/** 去掉小数末尾多余的 0：1.0 -> "1"，1.25 -> "1.25" */
function trim(n: number, digits: number): string {
  return Number(n.toFixed(digits)).toString()
}

/** token 数：999 -> "999"，1234 -> "1.2k"，1_500_000 -> "1.5M" */
export function formatTokens(n: number | null | undefined): string {
  if (isEmpty(n)) return EMPTY
  const abs = Math.abs(n)
  if (abs < 1000) return String(Math.round(n))
  if (abs < 1_000_000) return `${trim(n / 1000, 1)}k`
  if (abs < 1_000_000_000) return `${trim(n / 1_000_000, 1)}M`
  return `${trim(n / 1_000_000_000, 1)}B`
}

/** 成本（单位：元），固定 4 位小数：0.0123 -> "¥0.0123" */
export function formatCost(yuan: number | null | undefined): string {
  if (isEmpty(yuan)) return EMPTY
  return `¥${yuan.toFixed(4)}`
}

/** 耗时（毫秒）：850 -> "850ms"，1234 -> "1.2s"，65000 -> "1m5s" */
export function formatDuration(ms: number | null | undefined): string {
  if (isEmpty(ms)) return EMPTY
  if (ms < 0) return EMPTY
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const totalSec = Math.round(ms / 1000)
  if (totalSec < 3600) return `${Math.floor(totalSec / 60)}m${totalSec % 60}s`
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  return `${h}h${m}m`
}

/** 字节数：1536 -> "1.5 KB" */
export function formatBytes(bytes: number | null | undefined): string {
  if (isEmpty(bytes)) return EMPTY
  if (bytes < 1024) return `${Math.round(bytes)} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`
}

/** 比率 0.8234 -> "82.3%" */
export function formatPercent(ratio: number | null | undefined, digits = 1): string {
  if (isEmpty(ratio)) return EMPTY
  return `${(ratio * 100).toFixed(digits)}%`
}

/** 相对时间（中文）：刚刚 / 5 分钟前 / 2 小时前 / 3 天前，超过 30 天显示日期 */
export function formatRelativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return EMPTY
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return EMPTY
  const diff = Math.round((now - t) / 1000)
  if (diff < 0) return '刚刚'
  if (diff < 10) return '刚刚'
  if (diff < 60) return `${diff} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return formatDateTime(iso)
}

/** 本地时间 YYYY-MM-DD HH:mm:ss */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return EMPTY
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return EMPTY
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
