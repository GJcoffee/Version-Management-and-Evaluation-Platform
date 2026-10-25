import { create } from 'zustand'
import { safeGet, safeSet } from '@/lib/safeStorage'

export type Theme = 'light' | 'dark'
export const THEME_STORAGE_KEY = 'evalp.theme'

function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/** 读取已保存的偏好；没有（或存储不可用）时跟随系统 */
export function readStoredTheme(): Theme {
  const v = safeGet(THEME_STORAGE_KEY)
  return v === 'light' || v === 'dark' ? v : systemTheme()
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.dataset.theme = theme
}

interface ThemeState {
  theme: Theme
  setTheme: (t: Theme) => void
  toggle: () => void
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: readStoredTheme(),
  setTheme: (theme) => {
    safeSet(THEME_STORAGE_KEY, theme)
    applyTheme(theme)
    set({ theme })
  },
  toggle: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark'),
}))
