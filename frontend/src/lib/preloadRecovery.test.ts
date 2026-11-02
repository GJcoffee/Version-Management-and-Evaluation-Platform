import { afterEach, describe, expect, it, vi } from 'vitest'
import { recoverFromPreloadError } from './preloadRecovery'

afterEach(() => vi.unstubAllGlobals())

function setup() {
  const entries = new Map<string, string>()
  const storage = {
    getItem: vi.fn((key: string) => entries.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => entries.set(key, value)),
  }
  const reload = vi.fn()
  vi.stubGlobal('window', { sessionStorage: storage, location: { reload } })
  return { storage, reload }
}

function failure(module: string) {
  return Object.assign(new Event('vite:preloadError', { cancelable: true }), {
    payload: new Error(`Failed to fetch dynamically imported module: /assets/${module}.js`),
  })
}

describe('部署后模块加载恢复', () => {
  it('拦截旧模块失败并刷新页面，同一错误不会再次刷新', () => {
    const { reload } = setup()
    const first = failure('AssetsPage-old')
    recoverFromPreloadError(first)
    expect(first.defaultPrevented).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)

    const repeated = failure('AssetsPage-old')
    recoverFromPreloadError(repeated)
    expect(repeated.defaultPrevented).toBe(false)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('后续部署的不同模块错误仍可恢复', () => {
    const { reload } = setup()
    recoverFromPreloadError(failure('AssetsPage-old'))
    recoverFromPreloadError(failure('AssetsPage-new'))
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('存储不可用时保留原错误且不循环刷新', () => {
    const { storage, reload } = setup()
    storage.setItem.mockImplementation(() => { throw new Error('Storage disabled') })
    const event = failure('AssetsPage-old')
    recoverFromPreloadError(event)
    expect(event.defaultPrevented).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })
})
