import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { Draft, Page, Version, VersionAssets, VersionDiff, VersionSummary, VersionTag } from '../types'
import { agentKeys } from './agents'
import { draftKeys } from './drafts'

export const versionKeys = {
  all: ['versions'] as const,
  list: (agentRef: string) => [...versionKeys.all, 'list', agentRef] as const,
  detail: (ref: string) => [...versionKeys.all, 'detail', ref] as const,
  diff: (l: string, r: string) => [...versionKeys.all, 'diff', l, r] as const,
}

/** 版本列表（新版本在前），支持"加载更多" */
export function useVersions(agentRef: string | undefined, limit = 50) {
  return useInfiniteQuery({
    queryKey: [...versionKeys.list(agentRef ?? ''), limit],
    queryFn: ({ pageParam }) =>
      api.get<Page<VersionSummary>>(`/agents/${encodeURIComponent(agentRef!)}/versions`, {
        query: { limit, cursor: pageParam },
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!agentRef,
  })
}

/** `ref` 可以是版本 id、`agent名:v12`、`agent名:标签` */
export function useVersion(ref: string | undefined | null) {
  return useQuery({
    queryKey: versionKeys.detail(ref ?? ''),
    queryFn: () => api.get<Version>(`/versions/${encodeURIComponent(ref!)}`),
    enabled: !!ref,
  })
}

/** 版本绑定的资产：所用修订、来源（只读标记）、最近一次变化发生在哪个版本 */
export function useVersionAssets(versionId: string | undefined | null) {
  return useQuery({
    queryKey: [...versionKeys.detail(versionId ?? ''), 'assets'] as const,
    queryFn: () => api.get<VersionAssets>(`/versions/${encodeURIComponent(versionId!)}/assets`),
    enabled: !!versionId,
  })
}

export function useVersionDiff(left: string | undefined | null, right: string | undefined | null) {
  return useQuery({
    queryKey: versionKeys.diff(left ?? '', right ?? ''),
    queryFn: () => api.get<VersionDiff>('/versions/diff', { query: { left: left!, right: right! } }),
    enabled: !!left && !!right,
    placeholderData: keepPreviousData,
  })
}

/**
 * 草稿（已保存的内容）相对基础版本的 diff：`right` 传草稿 id。`revision` 进入 queryKey，保存后重新请求。
 * `enabled` 为 false（如编辑器里有未保存的修改，服务端看不到）时不请求。
 */
export function useDraftDiff(baseVersionId: string | null | undefined, draftId: string, revision: number, enabled = true) {
  return useQuery({
    queryKey: [...versionKeys.all, 'draft-diff', baseVersionId ?? '', draftId, revision] as const,
    queryFn: () => api.get<VersionDiff>('/versions/diff', { query: { left: baseVersionId!, right: draftId } }),
    enabled: enabled && !!baseVersionId,
    retry: false,
  })
}

function useInvalidateVersions() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: versionKeys.all })
    qc.invalidateQueries({ queryKey: agentKeys.all })
    qc.invalidateQueries({ queryKey: ['sessions', 'list'] })
    qc.invalidateQueries({ queryKey: ['compare', 'list'] })
    qc.invalidateQueries({ queryKey: ['evals', 'list'] })
  }
}

export function useAddTag() {
  const invalidate = useInvalidateVersions()
  return useMutation({
    mutationFn: ({ versionId, tag }: { versionId: string; tag: string }) =>
      api.post<VersionTag>(`/versions/${versionId}/tags`, { body: { tag } }),
    onSuccess: invalidate,
  })
}

export function useRemoveTag() {
  const invalidate = useInvalidateVersions()
  return useMutation({
    mutationFn: ({ versionId, tag }: { versionId: string; tag: string }) =>
      api.delete(`/versions/${versionId}/tags/${encodeURIComponent(tag)}`),
    onSuccess: invalidate,
  })
}

/** 设为阶段性成果：`versionIds` 缺省 = 归档此前所有未归档的过程版本；`title` 为空串表示清空阶段名 */
export function useSetMilestone() {
  const invalidate = useInvalidateVersions()
  return useMutation({
    mutationFn: ({ versionId, title, versionIds }: { versionId: string; title?: string; versionIds?: string[] }) =>
      api.post<VersionSummary>(`/versions/${versionId}/milestone`, { body: { title, version_ids: versionIds } }),
    onSuccess: invalidate,
  })
}

/** 取消阶段性成果：其下归档的版本全部移出归档 */
export function useRemoveMilestone() {
  const invalidate = useInvalidateVersions()
  return useMutation({
    mutationFn: (versionId: string) => api.delete<VersionSummary>(`/versions/${versionId}/milestone`),
    onSuccess: invalidate,
  })
}

/** 把版本移出归档；对里程碑调用则移出它名下的全部版本 */
export function useUnarchiveVersion() {
  const invalidate = useInvalidateVersions()
  return useMutation({
    mutationFn: (versionId: string) => api.post<VersionSummary>(`/versions/${versionId}/unarchive`),
    onSuccess: invalidate,
  })
}

/** 用该版本的 Spec 覆盖（或新建）草稿 */
export function useVersionToDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ versionId, draftName }: { versionId: string; draftName?: string }) =>
      api.post<{ draft: Draft }>(`/versions/${versionId}/to-draft`, { body: draftName ? { draft_name: draftName } : {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: draftKeys.all }),
  })
}
