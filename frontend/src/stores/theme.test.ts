import { afterEach, describe, expect, it, vi } from 'vitest'
import { THEME_STORAGE_KEY, applyTheme, readStoredTheme, useThemeStore } from './theme'

describe('theme', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    document.documentElement.className = ''
  })

  it('读取已保存的偏好', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    expect(readStoredTheme()).toBe('dark')
  })

  it('localStorage 抛异常时不崩溃，回退到系统/浅色', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    expect(() => readStoredTheme()).not.toThrow()
    expect(['light', 'dark']).toContain(readStoredTheme())
  })

  it('toggle 切换主题、更新 class 并写入存储', () => {
    useThemeStore.getState().setTheme('light')
    useThemeStore.getState().toggle()
    expect(useThemeStore.getState().theme).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    useThemeStore.getState().toggle()
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('写入失败时仍能切换主题', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    expect(() => useThemeStore.getState().setTheme('dark')).not.toThrow()
    expect(useThemeStore.getState().theme).toBe('dark')
    applyTheme('light')
  })
})
