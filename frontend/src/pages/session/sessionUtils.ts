import type { Session, SessionMode, SessionStatus } from '@/api/types'

export const MODE_LABEL: Record<SessionMode, string> = {
  chat: '对话',
  replay: '重放',
  eval: '评测',
  judge: '评审',
}

export const STATUS_LABEL: Record<SessionStatus, string> = {
  idle: '空闲',
  running: '运行中',
  error: '出错',
}

/** 会话标题：标题缺省时（还没有发过消息）显示“新对话” */
export function sessionTitle(s: Pick<Session, 'title'>): string {
  const t = s.title?.trim()
  return t ? t : '新对话'
}

/** 累计 token：stats 里是 prompt / completion 两项 */
export function sessionTokens(s: Pick<Session, 'stats'>): number {
  return (s.stats.prompt_tokens ?? 0) + (s.stats.completion_tokens ?? 0)
}

/** 版本显示：v3（没有序号时退回 id 缩写） */
export function versionLabel(s: Pick<Session, 'version_seq' | 'version_id'>): string {
  return s.version_seq !== null && s.version_seq !== undefined ? `v${s.version_seq}` : s.version_id.slice(-6)
}
