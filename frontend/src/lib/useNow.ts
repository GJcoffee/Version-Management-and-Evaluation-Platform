import { useEffect, useState } from 'react'

/** 返回当前时间戳（毫秒），`active` 为 true 时每 `intervalMs` 刷新一次（用于运行中的计时显示） */
export function useNow(active: boolean, intervalMs = 500): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [active, intervalMs])
  return now
}
