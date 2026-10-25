import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

/** 事件至少要有可选的 seq；llm.delta 没有 seq */
export interface StreamEvent {
  seq?: number | null
  type?: string
}

export type EventStreamStatus =
  /** 未启用（buildUrl 返回 null / enabled=false） */
  | 'disabled'
  | 'connecting'
  | 'open'
  /** 服务端发送了 idle，连接已关闭；需要时调用 reopen() */
  | 'idle'
  /** 断线，等待重连 */
  | 'reconnecting'
  | 'closed'

export interface UseEventStreamOptions<S, E extends StreamEvent> {
  /**
   * 构造 SSE 地址。afterSeq 为已收到的最大 seq（首次为 0）。
   * 返回 null 表示暂不连接。返回值在 afterSeq=0 时的取值若变化，会重置状态并重新连接。
   */
  buildUrl: (afterSeq: number) => string | null
  reducer: (state: S, event: E) => S
  initialState: S
  /** 要监听的 SSE `event:` 名称 */
  eventTypes: readonly string[]
  enabled?: boolean
  /** 首次重连间隔，默认 1000ms，之后指数退避 */
  retryDelayMs?: number
  /** 退避上限，默认 10000ms */
  maxRetryDelayMs?: number
}

export interface UseEventStreamResult<S> {
  state: S
  status: EventStreamStatus
  /** 重新打开订阅（从已收到的最大 seq 继续），用于 idle 之后发起新一轮时 */
  reopen: () => void
  /** 主动关闭，不再重连 */
  close: () => void
  /** 已收到的最大 seq */
  getLastSeq: () => number
}

type Action<S, E> = { kind: 'event'; event: E } | { kind: 'reset'; state: S }

/**
 * 通用 SSE Hook（详设 14.3）：
 * - 断线后带 after_seq 自动重连（指数退避）；
 * - 按 seq 去重（重放与实时衔接时可能重复）；无 seq 的事件（llm.delta）直接派发；
 * - 收到 `idle` 事件后关闭连接，不再重连；之后可通过 reopen() 重新订阅。
 *
 * 用法：
 *   const { state, status, reopen } = useEventStream({
 *     buildUrl: (after) => apiUrl(`/sessions/${id}/stream`, { after_seq: after }),
 *     reducer: sessionReducer, initialState, eventTypes: SESSION_EVENT_TYPES,
 *   })
 */
export function useEventStream<S, E extends StreamEvent>(
  options: UseEventStreamOptions<S, E>,
): UseEventStreamResult<S> {
  const { enabled = true } = options
  const [state, dispatch] = useReducer(
    (s: S, a: Action<S, E>): S => (a.kind === 'reset' ? a.state : options.reducer(s, a.event)),
    options.initialState,
  )
  const [status, setStatus] = useState<EventStreamStatus>('disabled')

  // 回调与选项放入 ref，避免调用方传入内联函数导致反复重连
  const optsRef = useRef(options)
  optsRef.current = options
  const lastSeqRef = useRef(0)
  const controllerRef = useRef<{ open: () => void; close: () => void } | null>(null)

  // 连接身份：afterSeq=0 时的 URL。变化意味着换了一个流
  const identity = enabled ? options.buildUrl(0) : null

  useEffect(() => {
    lastSeqRef.current = 0
    dispatch({ kind: 'reset', state: optsRef.current.initialState })
    if (identity === null) {
      controllerRef.current = null
      setStatus('disabled')
      return
    }

    let es: EventSource | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    let stopped = false // 卸载或主动关闭
    let attempt = 0

    const clearTimer = () => {
      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }
    }
    const shutdown = () => {
      clearTimer()
      if (es) {
        es.onerror = null
        es.onopen = null
        es.close()
        es = null
      }
    }

    const handleMessage = (m: MessageEvent) => {
      let ev: E
      try {
        ev = JSON.parse(m.data) as E
      } catch {
        return
      }
      if (ev === null || typeof ev !== 'object') return
      // 兼容：data 中没有 type/seq 时，用 SSE 帧的 event 名和 id 补齐
      if (ev.type === undefined) ev = { ...ev, type: m.type }
      if (ev.seq === undefined && m.lastEventId) {
        const n = Number(m.lastEventId)
        if (Number.isFinite(n)) ev = { ...ev, seq: n }
      }
      if (typeof ev.seq === 'number') {
        if (ev.seq <= lastSeqRef.current) return // 去重
        lastSeqRef.current = ev.seq
      }
      attempt = 0
      dispatch({ kind: 'event', event: ev })
    }

    const open = () => {
      shutdown()
      stopped = false
      const url = optsRef.current.buildUrl(lastSeqRef.current)
      if (url === null) {
        setStatus('disabled')
        return
      }
      setStatus('connecting')
      const source = new EventSource(url, { withCredentials: true })
      es = source
      source.onopen = () => {
        attempt = 0
        setStatus('open')
      }
      for (const t of optsRef.current.eventTypes) source.addEventListener(t, handleMessage as EventListener)
      source.addEventListener('idle', () => {
        stopped = true
        shutdown()
        setStatus('idle')
      })
      source.onerror = () => {
        shutdown()
        if (stopped) return
        const base = optsRef.current.retryDelayMs ?? 1000
        const max = optsRef.current.maxRetryDelayMs ?? 10000
        const delay = Math.min(base * 2 ** attempt, max)
        attempt += 1
        setStatus('reconnecting')
        timer = setTimeout(open, delay)
      }
    }

    controllerRef.current = {
      open: () => {
        attempt = 0
        open()
      },
      close: () => {
        stopped = true
        shutdown()
        setStatus('closed')
      },
    }
    open()

    return () => {
      stopped = true
      shutdown()
      controllerRef.current = null
    }
  }, [identity])

  const reopen = useCallback(() => controllerRef.current?.open(), [])
  const close = useCallback(() => controllerRef.current?.close(), [])
  const getLastSeq = useCallback(() => lastSeqRef.current, [])

  return { state, status, reopen, close, getLastSeq }
}
