import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type {
  Alignment,
  CompareCreate,
  CompareGroup,
  CompareTurnAccepted,
  CompareTurnCreate,
  ReplayRequest,
  ReplayResponse,
} from '../compareTypes'
import type { Page } from '../types'
import { sessionKeys } from './sessions'

export const compareKeys = {
  all: ['compare'] as const,
  list: (limit: number) => [...compareKeys.all, 'list', limit] as const,
  detail: (id: string) => [...compareKeys.all, 'detail', id] as const,
  /** version 变化时重新拉取（由页面在两侧 turn.start / turn.end 后改变它） */
  alignment: (id: string, version: string) => [...compareKeys.all, 'alignment', id, version] as const,
}

/** 最近的对比组（新的在前） */
export function useCompares(limit = 6) {
  return useQuery({
    queryKey: compareKeys.list(limit),
    queryFn: () => api.get<Page<CompareGroup>>('/compare', { query: { limit } }),
  })
}

export function useCompare(id: string | undefined) {
  return useQuery({
    queryKey: compareKeys.detail(id ?? ''),
    queryFn: () => api.get<CompareGroup>(`/compare/${id}`),
    enabled: !!id,
  })
}

/** 对齐结果；`version` 变化即重新拉取（保留上一份数据，避免闪烁） */
export function useAlignment(id: string | undefined, version: string, enabled = true) {
  return useQuery({
    queryKey: compareKeys.alignment(id ?? '', version),
    queryFn: () => api.get<Alignment>(`/compare/${id}/alignment`),
    enabled: !!id && enabled,
    placeholderData: keepPreviousData,
    staleTime: 0,
  })
}

export function useCreateCompare() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CompareCreate) => api.post<CompareGroup>('/compare', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: compareKeys.all }),
  })
}

/** live 模式发送一轮：两侧并发执行，409 SESSION_BUSY 表示有一侧正忙 */
export function useSendCompareTurn(compareId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CompareTurnCreate) => api.post<CompareTurnAccepted>(`/compare/${compareId}/turns`, { body }),
    onSettled: () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
  })
}

/** 在其他版本上重放（锚定模式）：返回新会话与对比组 */
export function useReplaySession(sessionId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ReplayRequest) => api.post<ReplayResponse>(`/sessions/${sessionId}/replay`, { body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: compareKeys.all })
      void qc.invalidateQueries({ queryKey: sessionKeys.all })
    },
  })
}
