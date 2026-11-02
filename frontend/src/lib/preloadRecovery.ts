const RELOAD_KEY = 'evalp:preload-error'

/** 部署后旧模块失效时刷新入口；同一错误在当前标签页最多自动恢复一次。 */
export function recoverFromPreloadError(event: Event): void {
  const reason = (event as Event & { payload?: Error }).payload?.message ?? 'unknown'
  try {
    if (window.sessionStorage.getItem(RELOAD_KEY) === reason) return
    window.sessionStorage.setItem(RELOAD_KEY, reason)
  } catch {
    // 无法保存恢复记录时不自动刷新，避免形成刷新循环。
    return
  }
  event.preventDefault()
  window.location.reload()
}
