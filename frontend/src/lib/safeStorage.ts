/** localStorage 的安全封装：隐私模式或被禁用时静默失败，页面仍可正常渲染。 */
export function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* 忽略 */
  }
}
