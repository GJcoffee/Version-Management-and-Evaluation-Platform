import { cn } from '@/lib/cn'
import { formatDateTime } from '@/lib/format'

/** 完整本地日期与时刻：YYYY-MM-DD HH:mm:ss。 */
export function DateTime({ value, className }: { value: string | null | undefined; className?: string }) {
  const text = formatDateTime(value)
  if (!value || text === '-') return <span className={className}>-</span>
  return (
    <time dateTime={value} title={text} className={cn('whitespace-nowrap tabular-nums', className)}>
      {text}
    </time>
  )
}
