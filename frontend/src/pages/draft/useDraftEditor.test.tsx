import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { ApiError } from '@/api/client'
import type { AgentSpec, Draft, ValidateResult } from '@/api/types'
import { AUTOSAVE_IDLE_MS, VALIDATE_DEBOUNCE_MS, useDraftEditor } from './useDraftEditor'

const saveDraft = vi.fn()
const validateDraft = vi.fn()
const apiGet = vi.fn()

vi.mock('@/api/queries/drafts', async () => {
  const actual = await vi.importActual<typeof import('@/api/queries/drafts')>('@/api/queries/drafts')
  return { ...actual, saveDraft: (...a: unknown[]) => saveDraft(...a), validateDraft: (...a: unknown[]) => validateDraft(...a) }
})
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => apiGet(...a) } }
})
vi.mock('@/stores/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))

const spec = { schema: 'evalp/v1', kind: 'config', model: { provider: 'p', name: 'm' } } as unknown as AgentSpec
const draft = (rev = 1, s: AgentSpec = spec): Draft => ({
  id: 'drf_1',
  agent_id: 'agt_1',
  name: 'main',
  spec: s,
  base_version_id: null,
  revision: rev,
  created_at: '2026-10-26T00:00:00.000Z',
  updated_at: '2026-10-26T00:00:00.000Z',
})
const ok: ValidateResult = { ok: true, errors: [], warnings: [], resolved_preview: spec }

function setup(d = draft()) {
  const qc = new QueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  return renderHook(() => useDraftEditor(d), { wrapper })
}

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const edit = (r: { current: ReturnType<typeof useDraftEditor> }, t: string) => act(() => r.current.setText(t))

describe('useDraftEditor：干跑校验与保存分离', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    saveDraft.mockReset()
    validateDraft.mockReset().mockResolvedValue(ok)
    apiGet.mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it('挂载时立即做一次干跑校验，带上当前 spec，不保存', async () => {
    const { result } = setup()
    await flush()
    expect(validateDraft).toHaveBeenCalledTimes(1)
    expect(validateDraft).toHaveBeenCalledWith('drf_1', expect.objectContaining({ kind: 'config' }))
    expect(saveDraft).not.toHaveBeenCalled()
    expect(result.current.state).toBe('saved')
    expect(result.current.validation?.ok).toBe(true)
    expect(result.current.validationFresh).toBe(true)
  })

  it('输入后防抖校验，且不会立即保存；多次输入只校验最后一次', async () => {
    const { result } = setup()
    await flush()
    validateDraft.mockClear()
    edit(result, result.current.text.replace('name: m', 'name: m1'))
    await advance(VALIDATE_DEBOUNCE_MS - 100)
    edit(result, result.current.text.replace('name: m1', 'name: m2'))
    await advance(VALIDATE_DEBOUNCE_MS - 100)
    expect(validateDraft).not.toHaveBeenCalled()
    await advance(150)
    expect(validateDraft).toHaveBeenCalledTimes(1)
    expect((validateDraft.mock.calls[0][1] as { model: { name: string } }).model.name).toBe('m2')
    expect(saveDraft).not.toHaveBeenCalled() // 校验期间没有保存
    expect(result.current.state).toBe('dirty')
    expect(result.current.dirty).toBe(true)
  })

  it('输入后校验返回前，校验结果不是“新鲜”的', async () => {
    const { result } = setup()
    await flush()
    edit(result, result.current.text.replace('name: m', 'name: x'))
    expect(result.current.validationFresh).toBe(false)
    await advance(VALIDATE_DEBOUNCE_MS + 10)
    expect(result.current.validationFresh).toBe(true)
  })

  it('闲置 5 秒后自动保存；继续输入会重新计时', async () => {
    saveDraft.mockImplementation(async (_id: string, s: AgentSpec, rev: number) => ({ draft: draft(rev + 1, s) }))
    const { result } = setup()
    await flush()
    edit(result, result.current.text.replace('name: m', 'name: a'))
    await advance(AUTOSAVE_IDLE_MS - 1000)
    edit(result, result.current.text.replace('name: a', 'name: b'))
    await advance(AUTOSAVE_IDLE_MS - 1000)
    expect(saveDraft).not.toHaveBeenCalled()
    await advance(1100)
    expect(saveDraft).toHaveBeenCalledTimes(1)
    expect(saveDraft).toHaveBeenCalledWith('drf_1', expect.objectContaining({ model: { provider: 'p', name: 'b' } }), 1)
    expect(result.current.revision).toBe(2)
    expect(result.current.state).toBe('saved')
  })

  it('手动保存（saveNow）立即保存；随后闲置计时不会重复保存', async () => {
    saveDraft.mockImplementation(async (_id: string, s: AgentSpec, rev: number) => ({ draft: draft(rev + 1, s) }))
    const { result } = setup()
    await flush()
    edit(result, result.current.text.replace('name: m', 'name: manual'))
    await act(async () => {
      expect(await result.current.saveNow()).toBe(true)
    })
    expect(saveDraft).toHaveBeenCalledTimes(1)
    expect(result.current.state).toBe('saved')
    await advance(AUTOSAVE_IDLE_MS + 500)
    expect(saveDraft).toHaveBeenCalledTimes(1)
  })

  it('没有改动时 saveNow 不发请求', async () => {
    const { result } = setup()
    await flush()
    await act(async () => {
      expect(await result.current.saveNow()).toBe(true)
    })
    expect(saveDraft).not.toHaveBeenCalled()
  })

  it('YAML 语法错误：不校验、不保存，状态为 invalid', async () => {
    const { result } = setup()
    await flush()
    validateDraft.mockClear()
    edit(result, 'model: [unclosed')
    await advance(AUTOSAVE_IDLE_MS + 1000)
    expect(validateDraft).not.toHaveBeenCalled()
    expect(saveDraft).not.toHaveBeenCalled()
    expect(result.current.state).toBe('invalid')
  })

  it('校验的 400 结构错误会作为校验结果显示', async () => {
    validateDraft.mockRejectedValueOnce(
      new ApiError(400, 'VALIDATION_ERROR', '结构不合法', { errors: [{ path: 'model', message: '缺少 name' }] }),
    )
    const { result } = setup()
    await flush()
    expect(result.current.validation).toMatchObject({ ok: false, errors: [{ path: 'model', message: '缺少 name' }] })
  })

  it('较早发出的校验结果晚到时不会覆盖较新的', async () => {
    let resolveFirst: (v: ValidateResult) => void = () => {}
    validateDraft.mockReset()
    validateDraft.mockImplementationOnce(() => new Promise<ValidateResult>((r) => (resolveFirst = r)))
    validateDraft.mockResolvedValue({ ...ok, warnings: [{ path: 'x', message: '新的' }] })
    const { result } = setup()
    await flush() // 第一次（挂起）
    edit(result, result.current.text.replace('name: m', 'name: n'))
    await advance(VALIDATE_DEBOUNCE_MS + 10) // 第二次，立即返回
    expect(result.current.validation?.warnings[0]?.message).toBe('新的')
    await act(async () => resolveFirst({ ...ok, warnings: [{ path: 'x', message: '过期的' }] }))
    expect(result.current.validation?.warnings[0]?.message).toBe('新的')
  })
})

describe('useDraftEditor：冲突处理', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    saveDraft.mockReset()
    validateDraft.mockReset().mockResolvedValue(ok)
    apiGet.mockReset()
  })
  afterEach(() => vi.useRealTimers())

  const serverSpec = { ...spec, model: { provider: 'p', name: 'server' } } as unknown as AgentSpec

  it('保存遇到 DRAFT_CONFLICT：进入冲突状态并拉取服务端草稿，之后不再自动保存', async () => {
    saveDraft.mockRejectedValueOnce(new ApiError(409, 'DRAFT_CONFLICT', '草稿已被修改', { current_revision: 5 }))
    apiGet.mockResolvedValue(draft(5, serverSpec))
    const { result } = setup()
    await flush()
    edit(result, result.current.text.replace('name: m', 'name: mine'))
    await advance(AUTOSAVE_IDLE_MS + 100)
    expect(saveDraft).toHaveBeenCalledTimes(1)
    expect(result.current.state).toBe('conflict')
    expect(result.current.conflict?.server.revision).toBe(5)
    edit(result, result.current.text + '\n# more')
    await advance(AUTOSAVE_IDLE_MS + 100)
    expect(saveDraft).toHaveBeenCalledTimes(1)
  })

  it('用本地内容覆盖：以服务端 revision 重新保存', async () => {
    saveDraft.mockRejectedValueOnce(new ApiError(409, 'DRAFT_CONFLICT', '冲突', { current_revision: 5 }))
    saveDraft.mockImplementation(async (_id: string, s: AgentSpec, rev: number) => ({ draft: draft(rev + 1, s) }))
    apiGet.mockResolvedValue(draft(5, serverSpec))
    const { result } = setup()
    await flush()
    edit(result, result.current.text.replace('name: m', 'name: mine'))
    await act(async () => {
      await result.current.saveNow()
    })
    expect(result.current.state).toBe('conflict')
    await act(async () => {
      await result.current.overwrite()
    })
    expect(saveDraft).toHaveBeenLastCalledWith('drf_1', expect.objectContaining({ model: { provider: 'p', name: 'mine' } }), 5)
    expect(result.current.revision).toBe(6)
    expect(result.current.state).toBe('saved')
  })

  it('放弃本地修改：文本换成服务端内容并重新校验', async () => {
    saveDraft.mockRejectedValueOnce(new ApiError(409, 'DRAFT_CONFLICT', '冲突', { current_revision: 5 }))
    apiGet.mockResolvedValue(draft(5, serverSpec))
    const { result } = setup()
    await flush()
    edit(result, result.current.text.replace('name: m', 'name: mine'))
    await act(async () => {
      await result.current.saveNow()
    })
    validateDraft.mockClear()
    await act(async () => {
      await result.current.discardLocal()
    })
    expect(result.current.text).toContain('name: server')
    expect(result.current.revision).toBe(5)
    await advance(VALIDATE_DEBOUNCE_MS + 10)
    expect(validateDraft).toHaveBeenCalledWith('drf_1', expect.objectContaining({ model: { provider: 'p', name: 'server' } }))
    expect(result.current.state).toBe('saved')
  })
})
