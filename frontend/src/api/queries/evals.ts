import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import {
  EVAL_ACTIVE_STATUSES,
  type EvalBudget,
  type EvalCompareOut,
  type EvalCreateRequest,
  type EvalCreated,
  type EvalGateParams,
  type EvalGateResult,
  type EvalHumanScoreEntry,
  type EvalRescoreResult,
  type EvaluatorInput,
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
  /** passed：通过；failed：评分未通过；error：执行失败或评估器出错；flaky：重复结果不一致；pending_human：等待人工打分 */
  cls?: 'passed' | 'failed' | 'error' | 'flaky' | 'pending_human' | ''
  q?: string
  /** 只看含该标签的用例 */
  tag?: string
}

/** 用例（Item）列表，游标分页，供虚拟滚动逐页加载 */
export function useEvalItems(runId: string | undefined, filter: EvalItemFilter = {}, limit = 100) {
  return useInfiniteQuery({
    queryKey: [...evalKeys.items(runId ?? '', filter), limit],
    queryFn: ({ pageParam }) =>
      api.get<Page<EvalItemOut>>(`/evals/${runId}/items`, {
        query: { class: filter.cls, q: filter.q, tag: filter.tag, limit, cursor: pageParam },
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

/** 成本预估：dry_run=true，只返回 estimate，不创建评测 */
export function useEstimateEval() {
  return useMutation({
    mutationFn: (body: EvalCreateRequest) =>
      api.post<EvalCreated>('/evals', { query: { dry_run: true }, body: { via: 'ui', ...body } }),
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

// ───────────────────────────── 运行控制 ─────────────────────────────

/** 暂停 / 继续 / 重跑失败项 / 重新评分 都会改变 run 的状态与进度：成功后刷新评测相关的全部查询 */
function useRunAction<V, R>(fn: (id: string, v: V) => Promise<R>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...v }: { id: string } & V) => fn(id, v as V),
    onSuccess: () => qc.invalidateQueries({ queryKey: evalKeys.all }),
  })
}

export function usePauseEval() {
  return useRunAction<object, EvalRunDetailOut>((id) => api.post<EvalRunDetailOut>(`/evals/${id}/pause`))
}

/** 继续；因预算暂停时可同时调高预算 */
export function useResumeEval() {
  return useRunAction<{ budget?: EvalBudget }, EvalRunDetailOut>((id, v) =>
    api.post<EvalRunDetailOut>(`/evals/${id}/resume`, { body: v.budget ? { budget: v.budget } : {} }),
  )
}

/** 把执行失败的 Item 重新放回队列 */
export function useRetryFailed() {
  return useRunAction<object, { retried: number }>((id) => api.post<{ retried: number }>(`/evals/${id}/retry-failed`))
}

/** 仅重新评分：`evaluators` 省略时只重算出错 / 过期的评分 */
export function useRescoreEval() {
  return useRunAction<{ evaluators?: EvaluatorInput[] }, EvalRescoreResult>((id, v) =>
    api.post<EvalRescoreResult>(`/evals/${id}/rescore`, { body: v.evaluators ? { evaluators: v.evaluators } : {} }),
  )
}

/** 提交人工评分（批量，要么全部生效要么全部不生效） */
export function useSubmitHumanScores() {
  return useRunAction<{ scores: EvalHumanScoreEntry[] }, { updated: number }>((id, v) =>
    api.post<{ updated: number }>(`/evals/${id}/human-scores`, { body: { scores: v.scores } }),
  )
}

/** CI 门禁检查：始终 200，结论在 `passed` 里 */
export function useEvalGate() {
  return useMutation({
    mutationFn: ({ id, ...params }: { id: string } & EvalGateParams) =>
      api.get<EvalGateResult>(`/evals/${id}/gate`, { query: { ...params } }),
  })
}
