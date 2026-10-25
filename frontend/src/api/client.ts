import type { ApiErrorBody } from './types'

/** REST 前缀（详设 11.1） */
export const API_PREFIX = '/api/v1'

/** 业务错误：非 2xx 且响应体为 `{"error": {code, message, details}}` 时抛出 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown> | null

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export type QueryValue = string | number | boolean | null | undefined | Array<string | number>
export type QueryParams = Record<string, QueryValue>

export interface RequestOptions {
  /** 查询参数；null/undefined/空字符串会被忽略，数组展开为重复键 */
  query?: QueryParams
  /** JSON 请求体（FormData 则原样发送） */
  body?: unknown
  signal?: AbortSignal
  headers?: Record<string, string>
}

/** 拼接 URL 与查询参数 */
export function buildUrl(path: string, query?: QueryParams, prefix: string = API_PREFIX): string {
  const base = path.startsWith('/') ? `${prefix}${path}` : `${prefix}/${path}`
  if (!query) return base
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === null || v === undefined || v === '') continue
    if (Array.isArray(v)) v.forEach((item) => sp.append(k, String(item)))
    else sp.append(k, String(v))
  }
  const qs = sp.toString()
  return qs ? `${base}?${qs}` : base
}

async function parseResponse(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined
  const text = await res.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function toApiError(status: number, payload: unknown): ApiError {
  const err = (payload as Partial<ApiErrorBody> | undefined)?.error
  if (err && typeof err === 'object' && typeof err.code === 'string') {
    return new ApiError(status, err.code, err.message ?? err.code, err.details ?? null)
  }
  const text = typeof payload === 'string' && payload ? payload.slice(0, 200) : `HTTP ${status}`
  return new ApiError(status, 'HTTP_ERROR', text)
}

/** 底层请求：带 Cookie、解析错误。`url` 为完整路径（含前缀） */
export async function rawRequest<T>(method: string, url: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers }
  let body: BodyInit | undefined
  if (opts.body !== undefined) {
    if (opts.body instanceof FormData) {
      body = opts.body
    } else {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(opts.body)
    }
  }
  let res: Response
  try {
    res = await fetch(url, { method, headers, body, credentials: 'include', signal: opts.signal })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接到服务，请确认后端已启动')
  }
  const payload = await parseResponse(res)
  if (!res.ok) throw toApiError(res.status, payload)
  return payload as T
}

function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  return rawRequest<T>(method, buildUrl(path, opts.query), opts)
}

/**
 * API 客户端。path 不含 `/api/v1` 前缀：
 *   api.get<Page<Agent>>('/agents', { query: { limit: 50 } })
 *   api.post<ProviderOut>('/providers', { body: {...} })
 */
export const api = {
  get: <T>(path: string, opts?: Omit<RequestOptions, 'body'>) => request<T>('GET', path, opts),
  post: <T>(path: string, opts?: RequestOptions) => request<T>('POST', path, opts),
  put: <T>(path: string, opts?: RequestOptions) => request<T>('PUT', path, opts),
  patch: <T>(path: string, opts?: RequestOptions) => request<T>('PATCH', path, opts),
  delete: <T = void>(path: string, opts?: Omit<RequestOptions, 'body'>) => request<T>('DELETE', path, opts),
}

/** 供 SSE 使用：给 EventSource 拼接带前缀的 URL */
export function apiUrl(path: string, query?: QueryParams): string {
  return buildUrl(path, query)
}

/** 应用启动时调用：服务端据此下发 HttpOnly Cookie（不在 /api/v1 前缀下） */
export async function bootstrap(signal?: AbortSignal): Promise<void> {
  await rawRequest<unknown>('GET', '/auth/bootstrap', { signal })
}
