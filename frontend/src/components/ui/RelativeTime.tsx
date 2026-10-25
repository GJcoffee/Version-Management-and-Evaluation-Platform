import { useEffect, useState } from 'react'
import { formatDateTime, formatRelativeTime } from '@/lib/format'

/** 全局共享一个 30s 定时器，所有 RelativeTime 一起刷新 */
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | null = null

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  if (timer === null) timer = setInterval(() => listeners.forEach((l) => l()), 30_000)
  return () => {
    listeners.delete(fn)
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }
}

/** 相对时间（中文），悬停显示完整本地时间 */
export function RelativeTime({ value, className }: { value: string | null | undefined; className?: string }) {
  const [, setTick] = useState(0)
  useEffect(() => subscribe(() => setTick((t) => t + 1)), [])
  if (!value) return <span className={className}>-</span>
  return (
    <time dateTime={value} title={formatDateTime(value)} className={className}>
      {formatRelativeTime(value)}
    </time>
  )
}
