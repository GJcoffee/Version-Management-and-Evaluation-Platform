import { create } from 'zustand'

export type ToastVariant = 'default' | 'success' | 'error' | 'warning'

export interface ToastItem {
  id: number
  variant: ToastVariant
  title: string
  description?: string
  /** 毫秒；0 表示不自动关闭 */
  duration: number
}

interface ToastState {
  toasts: ToastItem[]
  push: (t: Omit<ToastItem, 'id' | 'duration'> & { duration?: number }) => number
  dismiss: (id: number) => void
  clear: () => void
}

let seq = 0

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (t) => {
    const id = ++seq
    const duration = t.duration ?? (t.variant === 'error' ? 6000 : 3500)
    set((s) => ({ toasts: [...s.toasts, { ...t, id, duration }].slice(-5) }))
    if (duration > 0) setTimeout(() => get().dismiss(id), duration)
    return id
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  clear: () => set({ toasts: [] }),
}))

type Opts = { description?: string; duration?: number }

/** 在组件内外都可直接调用：toast.success('已保存') */
export const toast = {
  show: (title: string, o: Opts = {}) => useToastStore.getState().push({ variant: 'default', title, ...o }),
  success: (title: string, o: Opts = {}) => useToastStore.getState().push({ variant: 'success', title, ...o }),
  error: (title: string, o: Opts = {}) => useToastStore.getState().push({ variant: 'error', title, ...o }),
  warning: (title: string, o: Opts = {}) => useToastStore.getState().push({ variant: 'warning', title, ...o }),
}
