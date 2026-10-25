import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import {
  EVAL_ACTIVE_STATUSES,
  type EvalCompareOut,
  type EvalCreateRequest,
  type EvalCreated,
  type EvalItemDetailOut,
  type EvalItemOut,
  type EvalRunDetailOut,
  type EvalRunOut,
} from '../evalTypes'
import type { Page } from '../types'

export const evalKeys = {
  all: ['evals'] as const,
  list: (p: EvalListParams) => [...evalKeys.all, 'list', p] as const,
  /** 某个 run 的全部查询（详情、用例），按前缀失效 */
  run: (id: string) => [...evalKeys.all, 'run', id] as const,
  detail: (id: string) => [...evalKeys.run(id), 'detail'] as const,
  items: (id: string, f: EvalItemFilter) => [...evalKeys.run(id), 'items', f] as const,
  item: (id: string, itemId: string) => [...evalKeys.run(id), 'item', itemId] as const,
  compare: (l: string, r: string, cls: string) => [...evalKeys.all, 'compare', l, r, cls] as const,
}

export interface EvalListParams {
  agent_id?: string
  batch_id?: string
  /** 逗号分隔的多个状态 */
  status?: string
  limit?: number
}

/** 评测列表（新的在前），支持"加载更多"；`autoRefresh` 为轮询间隔（毫秒），仅在列表里有进行中的评测时生效 */
export function useEvals(params: EvalListParams = {}, opts: { autoRefresh?: number; enabled?: boolean } = {}) {
  return useInfiniteQuery({
    queryKey: evalKeys.list(params),
    queryFn: ({ pageParam }) =>
      api.get<Page<EvalRunOut>>('/evals', {
        query: {
          agent_id: params.agent_id,
          batch_id: params.batch_id,
          status: params.status,
          limit: params.limit ?? 50,
          cursor: pageParam,
        },
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      if (!opts.autoRefresh) return false
      const pages = query.state.data?.pages ?? []
      return pages.some((p) => p.items.some((r) => EVAL_ACTIVE_STATUSES.includes(r.status))) ? opts.autoRefresh : false
    },
    enabled: opts.enabled,
  })
}

/** 评测详情；进行中时每 `pollMs` 毫秒轮询一次（SSE 之外的兜底） */
export function useEval(id: string | undefined, opts: { pollMs?: number } = {}) {
  return useQuery({
    queryKey: evalKeys.detail(id ?? ''),
    queryFn: () => api.get<EvalRunDetailOut>(`/evals/${id}`),
    enabled: !!id,
    refetchInterval: (query) => {
      const st = query.state.data?.status
      return opts.pollMs && st && EVAL_ACTIVE_STATUSES.includes(st) ? opts.pollMs : false
    },
  })
}

export interface EvalItemFilter {
  /** passed：通过；failed：评分未通过；error：执行失败或评估器出错 */
  cls?: 'passed' | 'failed' | 'error' | ''
  q?: string
}

/** 用例（Item）列表，游标分页，供虚拟滚动逐页加载 */
export function useEvalItems(runId: string | undefined, filter: EvalItemFilter = {}, limit = 100) {
  return useInfiniteQuery({
    queryKey: [...evalKeys.items(runId ?? '', filter), limit],
    queryFn: ({ pageParam }) =>
      api.get<Page<EvalItemOut>>(`/evals/${runId}/items`, {
        query: { class: filter.cls, q: filter.q, limit, cursor: pageParam },
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!runId,
    placeholderData: keepPreviousData,
  })
}

/** 单个 Item（带完整输出与用例详情） */
export function useEvalItem(runId: string | undefined, itemId: string | undefined) {
  return useQuery({
    queryKey: evalKeys.item(runId ?? '', itemId ?? ''),
    queryFn: () => api.get<EvalItemDetailOut>(`/evals/${runId}/items/${itemId}`),
    enabled: !!runId && !!itemId,
  })
}

export function useCreateEval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: EvalCreateRequest) => api.post<EvalCreated>('/evals', { body: { via: 'ui', ...body } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: evalKeys.all }),
  })
}

export function useCancelEval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post<EvalRunDetailOut>(`/evals/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: evalKeys.all }),
  })
}

/** 两次评测的逐用例对比；`cls` 为分类（improved / regressed / …），按游标分页 */
export function useEvalCompare(left: string | undefined, right: string | undefined, cls: string, limit = 50) {
  return useInfiniteQuery({
    queryKey: [...evalKeys.compare(left ?? '', right ?? '', cls), limit],
    queryFn: ({ pageParam }) =>
      api.get<EvalCompareOut>('/evals/compare', {
        query: { left, right, class: cls || undefined, limit, cursor: pageParam },
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.cases.next_cursor ?? undefined,
    enabled: !!left && !!right && left !== right,
    placeholderData: keepPreviousData,
  })
}
