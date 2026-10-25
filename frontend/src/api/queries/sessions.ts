import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type {
  Page,
  Session,
  SessionCreate,
  SessionDetail,
  SessionListParams,
  Turn,
  TurnAccepted,
  TurnCreate,
} from '../types'

export const sessionKeys = {
  all: ['sessions'] as const,
  list: (p: SessionListParams) => [...sessionKeys.all, 'list', p] as const,
  /** 某个会话的全部详情查询（按前缀失效） */
  session: (id: string) => [...sessionKeys.all, 'detail', id] as const,
  detail: (id: string, include: string) => [...sessionKeys.session(id), include] as const,
  blob: (hash: string) => ['blobs', hash] as const,
}

function listQuery(p: SessionListParams, cursor?: string) {
  return {
    agent_id: p.agent_id,
    version_id: p.version_id,
    mode: p.mode,
    q: p.q,
    limit: p.limit ?? 50,
    cursor,
  }
}

/** 会话列表的第一页（首页卡片等不需要分页的场景） */
export function useSessions(params: SessionListParams = {}, opts: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: sessionKeys.list(params),
    queryFn: () => api.get<Page<Session>>('/sessions', { query: listQuery(params) }),
    placeholderData: keepPreviousData,
    refetchInterval: opts.refetchInterval,
  })
}

/** 会话列表（新的在前），支持"加载更多" */
export function useInfiniteSessions(params: SessionListParams = {}) {
  return useInfiniteQuery({
    queryKey: [...sessionKeys.list(params), 'infinite'],
    queryFn: ({ pageParam }) => api.get<Page<Session>>('/sessions', { query: listQuery(params, pageParam) }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  })
}

/**
 * 会话详情。`include` 缺省 `turns`；可传 'turns,events'；传 '' 则只要会话信息（不带轮次，更轻）。
 */
export function useSession(id: string | undefined, include: string = 'turns') {
  return useQuery({
    queryKey: sessionKeys.detail(id ?? '', include),
    // api 客户端会忽略空查询参数，而 `?include=`（空值）才表示“都不要”，所以手动拼接
    queryFn: () =>
      include === ''
        ? api.get<SessionDetail>(`/sessions/${id}?include=`)
        : api.get<SessionDetail>(`/sessions/${id}`, { query: { include } }),
    enabled: !!id,
  })
}

export function useCreateSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SessionCreate) => api.post<{ session: Session }>('/sessions', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
  })
}

/** 发送一轮（默认 202，后台执行；之后用 SSE 订阅过程）。会话忙时抛 ApiError(code=SESSION_BUSY) */
export function sendTurn(sessionId: string, body: TurnCreate): Promise<TurnAccepted> {
  return api.post<TurnAccepted>(`/sessions/${sessionId}/turns`, { body })
}

export function useSendTurn(sessionId: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: TurnCreate) => sendTurn(sessionId!, body),
    onSettled: () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
  })
}

/** 取消执行中的轮次：等它收尾后返回 TurnOut（status=cancelled）；已结束的轮次原样返回 */
export function cancelTurn(turnId: string): Promise<Turn> {
  return api.post<Turn>(`/turns/${turnId}/cancel`)
}

export function useCancelTurn() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (turnId: string) => cancelTurn(turnId),
    onSettled: () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
  })
}

/** 读取转存为 blob 的大 payload（`application/json`）；blob 内容不可变，永久缓存 */
export function fetchBlob<T = unknown>(hash: string, sessionId?: string): Promise<T> {
  return api.get<T>(`/blobs/${hash}`, { query: { session_id: sessionId } })
}

export function useBlob<T = unknown>(hash: string | null | undefined, sessionId?: string, enabled = true) {
  return useQuery({
    queryKey: sessionKeys.blob(hash ?? ''),
    queryFn: () => fetchBlob<T>(hash!, sessionId),
    enabled: !!hash && enabled,
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    retry: false,
  })
}
