import { useEffect, useState } from 'react'

/** 窄屏断点（≤800px）；与 tailwind.config.ts 中的 `narrow` 屏幕保持一致 */
export const NARROW_QUERY = '(max-width: 800px)'

function match(query: string): boolean {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
  } catch {
    return false
  }
}

/** 订阅 CSS 媒体查询；不支持 matchMedia 的环境（如 jsdom）恒为 false */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => match(query))
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(query)
    const update = () => setMatches(mql.matches)
    update()
    mql.addEventListener('change', update)
    return () => mql.removeEventListener('change', update)
  }, [query])
  return matches
}

/** 当前是否为窄屏（≤800px） */
export function useIsNarrow(): boolean {
  return useMediaQuery(NARROW_QUERY)
}
