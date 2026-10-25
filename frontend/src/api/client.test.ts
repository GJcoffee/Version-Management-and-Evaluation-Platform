import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, bootstrap, buildUrl } from './client'

function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** 执行请求并返回抛出的错误 */
async function thrown(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    return e as ApiError
  }
  throw new Error('预期抛出错误')
}

describe('buildUrl', () => {
  it('加前缀并忽略空值', () => {
    expect(buildUrl('/agents')).toBe('/api/v1/agents')
    expect(buildUrl('agents')).toBe('/api/v1/agents')
    expect(buildUrl('/agents', { limit: 50, cursor: null, q: '', archived: false })).toBe(
      '/api/v1/agents?limit=50&archived=false',
    )
  })
  it('数组展开为重复键，并做 URL 编码', () => {
    expect(buildUrl('/x', { tag: ['a', 'b'], q: '中 文' })).toBe('/api/v1/x?tag=a&tag=b&q=%E4%B8%AD+%E6%96%87')
  })
})

describe('api client', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('GET 带 credentials 并返回 JSON', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { items: [], next_cursor: null }))
    const res = await api.get<{ items: unknown[] }>('/agents', { query: { limit: 10 } })
    expect(res.items).toEqual([])
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/agents?limit=10')
    expect(init.method).toBe('GET')
    expect(init.credentials).toBe('include')
    expect(init.body).toBeUndefined()
  })

  it('POST 序列化 JSON body 并设置 Content-Type', async () => {
    fetchMock.mockResolvedValue(jsonResponse(201, { id: 'prv_1' }))
    await api.post('/providers', { body: { name: 'x' } })
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{"name":"x"}')
    expect(init.headers['Content-Type']).toBe('application/json')
  })

  it('204 返回 undefined', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(api.delete('/providers/prv_1')).resolves.toBeUndefined()
  })

  it('非 2xx 解析为 ApiError', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, { error: { code: 'DRAFT_CONFLICT', message: '草稿已被修改', details: { current_revision: 8 } } }),
    )
    const err = await thrown(api.put('/drafts/drf_1', { body: {} }))
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(409)
    expect(err.code).toBe('DRAFT_CONFLICT')
    expect(err.message).toBe('草稿已被修改')
    expect(err.details).toEqual({ current_revision: 8 })
  })

  it('非 JSON 错误体降级为 HTTP_ERROR', async () => {
    fetchMock.mockResolvedValue(new Response('Bad Gateway', { status: 502 }))
    const err = await thrown(api.get('/health'))
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('HTTP_ERROR')
    expect(err.status).toBe(502)
    expect(err.message).toBe('Bad Gateway')
  })

  it('网络错误转为 NETWORK_ERROR', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const err = await thrown(api.get('/health'))
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('NETWORK_ERROR')
    expect(err.status).toBe(0)
  })

  it('bootstrap 请求 /auth/bootstrap（不带 /api/v1 前缀）', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }))
    await bootstrap()
    expect(fetchMock.mock.calls[0][0]).toBe('/auth/bootstrap')
    expect(fetchMock.mock.calls[0][1].credentials).toBe('include')
  })
})
