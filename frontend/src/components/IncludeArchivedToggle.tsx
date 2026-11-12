import { useState } from 'react'
import type { VersionSummary } from '@/api/types'
import { selectableVersions } from '@/lib/versionArchive'

export interface ArchiveFilter {
  includeArchived: boolean
  setIncludeArchived: (v: boolean) => void
  /** 已归档的版本数；为 0 时不显示开关 */
  archivedCount?: number
  label?: string
}

/**
 * 版本选择器的「包括已归档」筛选：返回可选的版本和开关的属性。
 * `keep` 是当前已选 / 预选的版本 id，已归档也保留。
 */
export function useArchiveFilter<T extends Pick<VersionSummary, 'id' | 'archived_into'>>(
  versions: T[],
  keep: ReadonlyArray<string | null | undefined> = [],
): { shown: T[]; filter: ArchiveFilter } {
  const [includeArchived, setIncludeArchived] = useState(false)
  const archivedCount = versions.filter((v) => v.archived_into).length
  return {
    shown: selectableVersions(versions, includeArchived, keep),
    filter: { includeArchived, setIncludeArchived, archivedCount },
  }
}

/** 「包括已归档」开关：默认选择器只列里程碑与未归档的版本 */
export function IncludeArchivedToggle({ includeArchived, setIncludeArchived, archivedCount, label = '包括已归档' }: ArchiveFilter) {
  if (archivedCount === 0) return null
  return (
    <label className="inline-flex cursor-pointer items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
      <input
        type="checkbox"
        className="h-3.5 w-3.5 accent-primary"
        checked={includeArchived}
        onChange={(e) => setIncludeArchived(e.target.checked)}
        data-testid="include-archived"
      />
      {label}{archivedCount !== undefined ? `（${archivedCount}）` : ''}
    </label>
  )
}
