import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { Agent, AgentCreate, AgentKind, AgentUpdate, Draft, Page } from '../types'

export const agentKeys = {
  all: ['agents'] as const,
  list: (p: AgentListParams) => [...agentKeys.all, 'list', p] as const,
  detail: (ref: string) => [...agentKeys.all, 'detail', ref] as const,
}

export interface AgentListParams {
  include_archived?: boolean
  kind?: AgentKind | ''
  q?: string
  limit?: number
}

export function useAgents(params: AgentListParams = {}) {
  return useQuery({
    queryKey: agentKeys.list(params),
    queryFn: () =>
      api.get<Page<Agent>>('/agents', {
        query: { include_archived: params.include_archived, kind: params.kind, q: params.q, limit: params.limit ?? 100 },
      }),
    placeholderData: keepPreviousData,
  })
}

/** `ref` 可以是 agent id 或名称 */
export function useAgent(ref: string | undefined) {
  return useQuery({
    queryKey: agentKeys.detail(ref ?? ''),
    queryFn: () => api.get<Agent>(`/agents/${encodeURIComponent(ref!)}`),
    enabled: !!ref,
  })
}

export function useCreateAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: AgentCreate) => api.post<{ agent: Agent; draft: Draft }>('/agents', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: agentKeys.all }),
  })
}

export function useUpdateAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ ref, body }: { ref: string; body: AgentUpdate }) =>
      api.patch<Agent>(`/agents/${encodeURIComponent(ref)}`, { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: agentKeys.all }),
  })
}

/** 有版本的 agent 会返回 409 AGENT_IN_USE，请改为归档 */
export function useDeleteAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ref: string) => api.delete(`/agents/${encodeURIComponent(ref)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: agentKeys.all }),
  })
}
