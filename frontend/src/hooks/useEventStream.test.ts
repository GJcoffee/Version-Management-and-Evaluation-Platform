import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useEventStream } from './useEventStream'

/** 最小可用的 EventSource mock */
class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  withCredentials: boolean
  closed = false
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  private listeners = new Map<string, Array<(m: MessageEvent) => void>>()

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url
    this.withCredentials = init?.withCredentials ?? false
    MockEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: (m: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn])
  }
  close() {
    this.closed = true
  }
  /** 测试辅助：模拟服务端推送一帧 */
  emit(type: string, data: unknown, lastEventId = '') {
    const m = new MessageEvent(type, { data: typeof data === 'string' ? data : JSON.stringify(data), lastEventId })
    this.listeners.get(type)?.forEach((fn) => fn(m))
  }
  fail() {
    this.onerror?.()
  }
  static get last() {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }
}

interface Ev {
  seq?: number | null
  type: string
  text?: string
}
const reducer = (s: Ev[], e: Ev) => [...s, e]
const TYPES = ['turn.start', 'llm.delta', 'turn.end']
const build = (after: number) => `/api/v1/sessions/ses_1/stream?after_seq=${after}`

function setup(extra: Partial<Parameters<typeof useEventStream<Ev[], Ev>>[0]> = {}) {
  return renderHook(() =>
    useEventStream<Ev[], Ev>({ buildUrl: build, reducer, initialState: [], eventTypes: TYPES, ...extra }),
  )
}

describe('useEventStream', () => {
  beforeEach(() => {
    MockEventSource.instances = []
    vi.useFakeTimers()
    vi.stubGlobal('EventSource', MockEventSource)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('以 after_seq=0 和 withCredentials 打开连接', () => {
    setup()
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.last.url).toBe('/api/v1/sessions/ses_1/stream?after_seq=0')
    expect(MockEventSource.last.withCredentials).toBe(true)
  })

  it('派发事件，按 seq 去重，llm.delta（无 seq）直接派发', () => {
    const { result } = setup()
    const es = MockEventSource.last
    act(() => {
      es.onopen?.()
      es.emit('turn.start', { seq: 1, type: 'turn.start' })
      es.emit('turn.start', { seq: 1, type: 'turn.start' }) // 重复
      es.emit('llm.delta', { type: 'llm.delta', text: 'a' })
      es.emit('llm.delta', { type: 'llm.delta', text: 'b' })
      es.emit('turn.end', { seq: 2, type: 'turn.end' })
      es.emit('turn.start', { seq: 2, type: 'turn.end' }) // 旧 seq
    })
    expect(result.current.status).toBe('open')
    expect(result.current.state.map((e) => e.text ?? e.seq)).toEqual([1, 'a', 'b', 2])
    expect(result.current.getLastSeq()).toBe(2)
  })

  it('data 缺少 type/seq 时用 SSE 帧的 event 名和 id 补齐', () => {
    const { result } = setup()
    act(() => MockEventSource.last.emit('turn.start', {}, '7'))
    expect(result.current.state[0]).toMatchObject({ type: 'turn.start', seq: 7 })
  })

  it('忽略无法解析的数据', () => {
    const { result } = setup()
    act(() => MockEventSource.last.emit('turn.start', 'not json'))
    expect(result.current.state).toEqual([])
  })

  it('断线后 1s 重连，并带上已收到的最大 seq', () => {
    const { result } = setup()
    const first = MockEventSource.last
    act(() => first.emit('turn.start', { seq: 5, type: 'turn.start' }))
    act(() => first.fail())
    expect(first.closed).toBe(true)
    expect(result.current.status).toBe('reconnecting')
    expect(MockEventSource.instances).toHaveLength(1)

    act(() => vi.advanceTimersByTime(999))
    expect(MockEventSource.instances).toHaveLength(1)
    act(() => vi.advanceTimersByTime(1))
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.last.url).toBe('/api/v1/sessions/ses_1/stream?after_seq=5')

    // 回放中重复的旧事件被去重
    act(() => {
      MockEventSource.last.emit('turn.start', { seq: 5, type: 'turn.start' })
      MockEventSource.last.emit('turn.end', { seq: 6, type: 'turn.end' })
    })
    expect(result.current.state.map((e) => e.seq)).toEqual([5, 6])
  })

  it('连续失败时指数退避，成功收到事件后恢复', () => {
    setup()
    act(() => MockEventSource.last.fail())
    act(() => vi.advanceTimersByTime(1000))
    expect(MockEventSource.instances).toHaveLength(2)
    act(() => MockEventSource.last.fail())
    act(() => vi.advanceTimersByTime(1999))
    expect(MockEventSource.instances).toHaveLength(2)
    act(() => vi.advanceTimersByTime(1))
    expect(MockEventSource.instances).toHaveLength(3)
    act(() => MockEventSource.last.onopen?.())
    act(() => MockEventSource.last.fail())
    act(() => vi.advanceTimersByTime(1000))
    expect(MockEventSource.instances).toHaveLength(4)
  })

  it('收到 idle 后关闭且不再重连，reopen() 从 last_seq 继续', () => {
    const { result } = setup()
    const first = MockEventSource.last
    act(() => {
      first.emit('turn.end', { seq: 9, type: 'turn.end' })
      first.emit('idle', {})
    })
    expect(first.closed).toBe(true)
    expect(result.current.status).toBe('idle')
    act(() => vi.advanceTimersByTime(30_000))
    expect(MockEventSource.instances).toHaveLength(1)

    act(() => result.current.reopen())
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.last.url).toBe('/api/v1/sessions/ses_1/stream?after_seq=9')
    expect(result.current.status).toBe('connecting')
    // reopen 不会清空已有状态
    expect(result.current.state).toHaveLength(1)
  })

  it('close() 主动关闭后不重连', () => {
    const { result } = setup()
    act(() => result.current.close())
    expect(MockEventSource.last.closed).toBe(true)
    expect(result.current.status).toBe('closed')
    act(() => vi.advanceTimersByTime(30_000))
    expect(MockEventSource.instances).toHaveLength(1)
  })

  it('卸载时关闭连接并取消待执行的重连', () => {
    const { unmount } = setup()
    act(() => MockEventSource.last.fail())
    unmount()
    act(() => vi.advanceTimersByTime(30_000))
    expect(MockEventSource.instances).toHaveLength(1)

    const second = setup()
    const es = MockEventSource.last
    second.unmount()
    expect(es.closed).toBe(true)
  })

  it('buildUrl 返回 null 或 enabled=false 时不连接', () => {
    const a = renderHook(() =>
      useEventStream<Ev[], Ev>({ buildUrl: () => null, reducer, initialState: [], eventTypes: TYPES }),
    )
    expect(MockEventSource.instances).toHaveLength(0)
    expect(a.result.current.status).toBe('disabled')
    const b = setup({ enabled: false })
    expect(MockEventSource.instances).toHaveLength(0)
    expect(b.result.current.status).toBe('disabled')
  })

  it('流标识（URL）变化时重置状态并重新连接', () => {
    let id = 'ses_1'
    const { result, rerender } = renderHook(() =>
      useEventStream<Ev[], Ev>({
        buildUrl: (a) => `/api/v1/sessions/${id}/stream?after_seq=${a}`,
        reducer,
        initialState: [],
        eventTypes: TYPES,
      }),
    )
    act(() => MockEventSource.last.emit('turn.start', { seq: 3, type: 'turn.start' }))
    expect(result.current.state).toHaveLength(1)
    const old = MockEventSource.last

    id = 'ses_2'
    rerender()
    expect(old.closed).toBe(true)
    expect(MockEventSource.last.url).toBe('/api/v1/sessions/ses_2/stream?after_seq=0')
    expect(result.current.state).toEqual([])
    expect(result.current.getLastSeq()).toBe(0)
  })

  it('调用方传入内联函数（每次渲染新引用）不会导致重复连接', () => {
    const { rerender } = setup()
    rerender()
    rerender()
    expect(MockEventSource.instances).toHaveLength(1)
  })
})
