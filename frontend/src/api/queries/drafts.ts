import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { AgentSpec, Draft, PublishRequest, PublishResult, ValidateResult } from '../types'
import { agentKeys } from './agents'
import { versionKeys } from './versions'

export const draftKeys = {
  all: ['drafts'] as const,
  list: (agentRef: string) => [...draftKeys.all, 'list', agentRef] as const,
  detail: (id: string) => [...draftKeys.all, 'detail', id] as const,
}

export function useDrafts(agentRef: string | undefined) {
  return useQuery({
    queryKey: draftKeys.list(agentRef ?? ''),
    queryFn: () => api.get<Draft[]>(`/agents/${encodeURIComponent(agentRef!)}/drafts`),
    enabled: !!agentRef,
  })
}

export function useDraft(id: string | undefined) {
  return useQuery({
    queryKey: draftKeys.detail(id ?? ''),
    queryFn: () => api.get<Draft>(`/drafts/${id}`),
    enabled: !!id,
    // 编辑器自己管理草稿状态，避免窗口聚焦时的后台刷新覆盖本地修改
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

export function useCreateDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      agentRef,
      name,
      spec,
      from_version_id,
    }: {
      agentRef: string
      name: string
      spec?: Partial<AgentSpec>
      from_version_id?: string | null
    }) =>
      api.post<{ draft: Draft }>(`/agents/${encodeURIComponent(agentRef)}/drafts`, {
        body: { name, spec, from_version_id },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: draftKeys.all }),
  })
}

/** 保存草稿（乐观锁）。冲突时抛 ApiError(code=DRAFT_CONFLICT, details.current_revision) */
export function saveDraft(id: string, spec: AgentSpec, expectedRevision: number): Promise<{ draft: Draft }> {
  return api.put<{ draft: Draft }>(`/drafts/${id}`, { body: { spec, expected_revision: expectedRevision } })
}

/**
 * 校验草稿。传 `spec` 时对它做干跑校验（不保存、不改 revision）；不传则校验服务端已保存的内容。
 */
export function validateDraft(id: string, spec?: AgentSpec): Promise<ValidateResult> {
  return api.post<ValidateResult>(`/drafts/${id}/validate`, spec ? { body: { spec } } : undefined)
}

export function publishDraft(id: string, body: PublishRequest): Promise<PublishResult> {
  return api.post<PublishResult>(`/drafts/${id}/publish`, { body })
}

/** 发布后需要让版本、agent（latest_version）与草稿（base_version_id）相关缓存失效 */
export function useInvalidateAfterPublish() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: versionKeys.all })
    qc.invalidateQueries({ queryKey: agentKeys.all })
    qc.invalidateQueries({ queryKey: draftKeys.all })
  }
}
