import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NARROW_QUERY, useIsNarrow, useMediaQuery } from './useMediaQuery'

function stubMatchMedia(initial: boolean) {
  let matches = initial
  const listeners = new Set<() => void>()
  const mql = {
    get matches() {
      return matches
    },
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  }
  const fn = vi.fn(() => mql)
  vi.stubGlobal('matchMedia', fn)
  return {
    fn,
    set(v: boolean) {
      matches = v
      listeners.forEach((l) => l())
    },
    listeners,
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('useMediaQuery', () => {
  it('窄屏断点为 ≤800px', () => {
    expect(NARROW_QUERY).toBe('(max-width: 800px)')
  })

  it('没有 matchMedia（jsdom）时恒为 false', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(renderHook(() => useIsNarrow()).result.current).toBe(false)
  })

  it('返回初始匹配结果并随 change 事件更新，卸载后取消订阅', () => {
    const mm = stubMatchMedia(false)
    const { result, unmount } = renderHook(() => useMediaQuery('(max-width: 800px)'))
    expect(result.current).toBe(false)
    expect(mm.fn).toHaveBeenCalledWith('(max-width: 800px)')
    act(() => mm.set(true))
    expect(result.current).toBe(true)
    unmount()
    expect(mm.listeners.size).toBe(0)
  })
})
