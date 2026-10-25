import { useEffect, useState } from 'react'

/** 值变化后延迟 `delay` 毫秒才更新（用于搜索框等） */
export function useDebouncedValue<T>(value: T, delay = 300): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return v
}
