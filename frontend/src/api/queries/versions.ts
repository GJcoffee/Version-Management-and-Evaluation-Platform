import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { Draft, Page, Version, VersionDiff, VersionSummary, VersionTag } from '../types'
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

export function useVersionDiff(left: string | undefined | null, right: string | undefined | null) {
  return useQuery({
    queryKey: versionKeys.diff(left ?? '', right ?? ''),
    queryFn: () => api.get<VersionDiff>('/versions/diff', { query: { left: left!, right: right! } }),
    enabled: !!left && !!right,
    placeholderData: keepPreviousData,
  })
}

function useInvalidateVersions() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: versionKeys.all })
    qc.invalidateQueries({ queryKey: agentKeys.all })
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

/** 用该版本的 Spec 覆盖（或新建）草稿 */
export function useVersionToDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ versionId, draftName }: { versionId: string; draftName?: string }) =>
      api.post<{ draft: Draft }>(`/versions/${versionId}/to-draft`, { body: draftName ? { draft_name: draftName } : {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: draftKeys.all }),
  })
}
