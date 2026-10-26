import { vi } from 'vitest'

export interface MockCall {
  method: string
  /** 含查询串的相对地址，如 /api/v1/evals/evr_1/items?class=pending_human */
  url: string
  body: unknown
}

type Handler = unknown | ((call: MockCall) => unknown)

/**
 * 用 fetch 桩模拟后端：`routes` 按 `METHOD /path-regex` 匹配（path 不含查询串，含 /api/v1 前缀）。
 * 返回值为 JSON 响应体；handler 返回 `{ __status, body }` 可指定非 2xx 状态。未匹配的请求返回 404。
 * 每次调用 `mockApi` 都会重新安装桩，测试结束由 `vi.unstubAllGlobals()` 恢复。
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: MockCall[] = []
  const table = Object.entries(routes).map(([k, h]) => {
    const [method, pattern] = k.split(' ')
    return { method, re: new RegExp(`^${pattern}$`), h }
  })
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown
    if (typeof init?.body === 'string') body = JSON.parse(init.body)
    const call: MockCall = { method, url, body }
    calls.push(call)
    const path = url.split('?')[0]
    const hit = table.find((r) => r.method === method && r.re.test(path))
    if (!hit) return new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: `no mock: ${method} ${url}` } }), { status: 404 })
    const out = typeof hit.h === 'function' ? (hit.h as (c: MockCall) => unknown)(call) : hit.h
    if (out && typeof out === 'object' && '__status' in out) {
      const o = out as { __status: number; body: unknown }
      return new Response(JSON.stringify(o.body), { status: o.__status })
    }
    return new Response(JSON.stringify(out ?? {}), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock, find: (method: string, re: RegExp) => calls.filter((c) => c.method === method && re.test(c.url)) }
}
