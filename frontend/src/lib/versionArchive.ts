import type { VersionSummary } from '@/api/types'

/** 里程碑（阶段性成果）的标识文字，如 `★ 第一阶段：语气优化` */
export function milestoneText(v: Pick<VersionSummary, 'milestone' | 'milestone_title'>): string {
  return v.milestone ? `★ ${v.milestone_title || '阶段性成果'}` : ''
}

/** 版本选择器的下拉文字：`v3`、`v3 · 标签`、`v3 · ★ 阶段名` 依次拼接 */
export function versionOptionText(v: VersionSummary, extra: string[] = []): string {
  return [`v${v.seq}`, v.tags.join(','), milestoneText(v), ...extra].filter(Boolean).join(' · ')
}

/**
 * 选择器里可选的版本：默认只含里程碑与未归档的版本，`includeArchived` 时含全部。
 * `keep` 里的版本（当前已选 / 预选的）即使已归档也保留，避免选中项凭空消失。
 */
export function selectableVersions<T extends Pick<VersionSummary, 'id' | 'archived_into'>>(
  versions: T[],
  includeArchived: boolean,
  keep: ReadonlyArray<string | null | undefined> = [],
): T[] {
  return includeArchived ? versions : versions.filter((v) => !v.archived_into || keep.includes(v.id))
}
