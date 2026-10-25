import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { MessagesSquare } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { CompareGroup } from '@/api/compareTypes'
import { compareKeys, useAlignment, useSendCompareTurn } from '@/api/queries/compare'
import { cancelTurn } from '@/api/queries/sessions'
import { CompareHeader, type SideSummary } from '@/components/compare/CompareHeader'
import { CompareRowView, type Side, type TraceTarget } from '@/components/compare/CompareRowView'
import { TraceDrawer } from '@/components/compare/TraceDrawer'
import { alignmentHighlights, buildRows, type SeededPair } from '@/components/compare/compareUtils'
import { MessageInput, totalMetrics, useSessionStream, type TurnState } from '@/components/session'
import { EmptyState } from '@/components/ui/EmptyState'
import { safeGet, safeSet } from '@/lib/safeStorage'
import { toast } from '@/stores/toast'

const ALIGN_KEY = 'evalp.compare.align'
/** 重放这么久没有任何进展就认为已停止（不再轮询重连） */
const REPLAY_STALL_MS = 120_000

const endedCount = (turns: readonly TurnState[]) => turns.filter((t) => t.ended).length

/** 滚动签名：最后一轮的事件数与最后一条消息的文本长度（流式输出时也会变） */
function streamSig(turns: readonly TurnState[]): string {
  const last = turns[turns.length - 1]
  const m = last?.messages[last.messages.length - 1]
  return `${turns.length}:${last?.events.length ?? 0}:${m?.content.length ?? 0}:${last?.status ?? ''}`
}

/**
 * 双栏对比（live / history / replay 共用）：
 * 两侧各订阅一个会话的 SSE；行由服务端对齐结果（按 pair_index / idx）决定，单元格取 SSE 还原出的轮次，
 * 所以回放历史、实时输出、重放进度走的是同一条路径。
 */
export function CompareView({ group }: { group: CompareGroup }) {
  const qc = useQueryClient()
  const mode = group.mode
  const left = useSessionStream(group.left.session_id)
  const right = useSessionStream(group.right.session_id)
  const sendPair = useSendCompareTurn(group.id)

  // ───── 对齐数据：行结构始终来自它；两侧每次 turn.start / turn.end 之后重新拉取 ─────
  const version = [left.turns.length, endedCount(left.turns), right.turns.length, endedCount(right.turns)].join('.')
  const alignment = useAlignment(group.id, version)
  useEffect(() => {
    // turn.end 事件可能先于轮次落库的最终字段到达：稍后再拉一次，保证 final_text / 指标是终态
    const t = setTimeout(() => void qc.invalidateQueries({ queryKey: [...compareKeys.all, 'alignment', group.id] }), 1200)
    return () => clearTimeout(t)
  }, [version, qc, group.id])

  const [seeded, setSeeded] = useState<SeededPair[]>([])
  const rows = useMemo(() => buildRows(alignment.data, seeded), [alignment.data, seeded])
  const leftById = useMemo(() => new Map(left.turns.map((t) => [t.id, t])), [left.turns])
  const rightById = useMemo(() => new Map(right.turns.map((t) => [t.id, t])), [right.turns])

  const [alignOn, setAlignOnState] = useState(() => safeGet(ALIGN_KEY) === '1')
  const setAlignOn = (v: boolean) => {
    setAlignOnState(v)
    safeSet(ALIGN_KEY, v ? '1' : '0')
  }
  const highlights = useMemo(() => alignmentHighlights(alignment.data), [alignment.data])

  // ───── live：发送 / 停止 ─────
  const [awaiting, setAwaiting] = useState<{ left: string; right: string } | null>(null)
  const awL = awaiting ? leftById.get(awaiting.left) : undefined
  const awR = awaiting ? rightById.get(awaiting.right) : undefined
  const awaitingDone = !!awaiting && !!awL?.ended && !!awR?.ended
  useEffect(() => {
    if (awaitingDone) setAwaiting(null)
  }, [awaitingDone])
  const awaitingMissing = !!awaiting && (!awL || !awR)
  useEffect(() => {
    if (!awaitingMissing) return
    const t = setTimeout(() => setAwaiting(null), 20_000) // 事件始终没到：不要一直卡在运行中
    return () => clearTimeout(t)
  }, [awaitingMissing])

  const running = left.running || right.running || (!!awaiting && !awaitingDone)
  const [stopping, setStopping] = useState(false)

  const onSend = async (text: string, opts: { variables?: Record<string, string> }) => {
    try {
      const r = await sendPair.mutateAsync({ input: text, variables: opts.variables })
      setSeeded((s) => [...s, { pair: r.pair_index, leftTurnId: r.left_turn_id, rightTurnId: r.right_turn_id }])
      setAwaiting({ left: r.left_turn_id, right: r.right_turn_id })
      // 连接在上一轮 idle 后已关闭：两侧都从已收到的最大 seq 继续订阅
      left.reopen()
      right.reopen()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'SESSION_BUSY') toast.warning('有一侧会话正在执行上一轮，请等待结束或先停止')
      else toast.error('发送失败', { description: e instanceof ApiError ? e.message : undefined })
      throw e
    }
  }

  const onStop = async () => {
    const ids = [
      left.runningTurn?.id ?? (awaiting && !awL?.ended ? awaiting.left : null),
      right.runningTurn?.id ?? (awaiting && !awR?.ended ? awaiting.right : null),
    ].filter((x): x is string => !!x)
    setStopping(true)
    try {
      await Promise.all(ids.map((id) => cancelTurn(id)))
    } catch (e) {
      toast.error('停止失败', { description: e instanceof ApiError ? e.message : undefined })
    } finally {
      setStopping(false)
    }
  }

  // ───── replay：进度，以及串行重放期间保持右侧订阅 ─────
  // 还有没跑完的行：右侧轮次还没创建（排队中），或已创建但还没从 SSE 收到 turn.end
  const unfinished = rows.some((r) => !r.rightTurnId || !rightById.get(r.rightTurnId)?.ended)
  const [stalled, setStalled] = useState(false)
  const progressSig = `${right.state.lastSeq}:${right.turns.length}:${rows.length}`
  useEffect(() => {
    setStalled(false)
    const t = setTimeout(() => setStalled(true), REPLAY_STALL_MS)
    return () => clearTimeout(t)
  }, [progressSig])
  const replayActive = mode === 'replay' && !stalled && (!alignment.data || unfinished || right.running)
  const { status: rightStatus, reopen: reopenRight } = right
  useEffect(() => {
    // 后端对 idle 的会话会立刻发 idle 并关闭连接：重放在轮次之间（以及第一轮开始前）会短暂空闲，需要持续重连
    if (!replayActive) return
    const t = setInterval(() => {
      if (rightStatus === 'idle') reopenRight()
    }, 1500)
    return () => clearInterval(t)
  }, [replayActive, rightStatus, reopenRight])

  const replay = useMemo(() => {
    if (mode !== 'replay') return null
    const done = rows.filter((r) => r.rightTurnId && rightById.get(r.rightTurnId)?.ended).length
    return { done, total: rows.length, active: replayActive }
  }, [mode, rows, rightById, replayActive])

  // ───── Trace 抽屉 ─────
  const [trace, setTrace] = useState<TraceTarget | null>(null)
  const [selectedStep, setSelectedStep] = useState<string | null>(null)
  const openTrace = useCallback((side: Side, turnId: string, stepId?: string) => {
    setTrace({ side, turnId })
    setSelectedStep(stepId ?? null)
  }, [])
  const traceTurn = trace ? (trace.side === 'left' ? leftById : rightById).get(trace.turnId) : undefined
  const traceOrdinal = trace
    ? rows.findIndex((r) => (trace.side === 'left' ? r.leftTurnId : r.rightTurnId) === trace.turnId) + 1
    : 0
  const traceSide = trace ? (trace.side === 'left' ? group.left : group.right) : null

  // ───── 自动滚动到底部（live / replay；用户向上滚动后暂停）─────
  const scrollRef = useRef<HTMLDivElement>(null)
  const stuckRef = useRef(true)
  const follow = mode !== 'history'
  const sig = `${rows.length}|${streamSig(left.turns)}|${streamSig(right.turns)}`
  useLayoutEffect(() => {
    if (!follow || !stuckRef.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [sig, follow])
  const onScroll = () => {
    const el = scrollRef.current
    if (el) stuckRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const leftSummary = useMemo<SideSummary>(
    () => ({ turns: left.turns.length, totals: totalMetrics(left.turns), running: left.running }),
    [left.turns, left.running],
  )
  const rightSummary = useMemo<SideSummary>(
    () => ({ turns: right.turns.length, totals: totalMetrics(right.turns), running: right.running }),
    [right.turns, right.running],
  )
  const loading = left.loading || right.loading || alignment.isLoading

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="compare-page" data-mode={mode} data-running={running || undefined}>
      <CompareHeader
        group={group}
        left={leftSummary}
        right={rightSummary}
        replay={replay}
        alignOn={alignOn}
        onToggleAlign={setAlignOn}
        reconnecting={left.status === 'reconnecting' || right.status === 'reconnecting'}
      />

      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto px-4 py-4" data-testid="compare-rows">
          {rows.length === 0 ? (
            <EmptyState
              icon={MessagesSquare}
              title={loading ? '加载中…' : mode === 'live' ? '还没有对话' : '没有可对比的轮次'}
              description={mode === 'live' && !loading ? '在下方输入内容，两侧会同时开始回答。' : undefined}
              className="py-16"
            />
          ) : (
            <div className="mx-auto flex max-w-[1600px] flex-col gap-6">
              {rows.map((row, i) => (
                <CompareRowView
                  key={row.pair}
                  row={row}
                  ordinal={i + 1}
                  mode={mode}
                  left={row.leftTurnId ? leftById.get(row.leftTurnId) : undefined}
                  right={row.rightTurnId ? rightById.get(row.rightTurnId) : undefined}
                  leftSessionId={group.left.session_id}
                  rightSessionId={group.right.session_id}
                  replayActive={replayActive}
                  alignOn={alignOn}
                  leftHighlight={highlights.left}
                  rightHighlight={highlights.right}
                  trace={trace}
                  onOpenTrace={openTrace}
                />
              ))}
            </div>
          )}
        </div>
        {trace && traceSide && (
          <TraceDrawer
            title={`${trace.side === 'left' ? '左' : '右'}侧 · ${traceSide.agent_name ?? ''} v${traceSide.version_seq ?? '?'}${traceOrdinal ? ` · 第 ${traceOrdinal} 轮` : ''}`}
            turn={traceTurn}
            sessionId={traceSide.session_id}
            selectedStepId={selectedStep}
            onSelectStep={setSelectedStep}
            highlightSteps={alignOn ? (trace.side === 'left' ? highlights.left : highlights.right) : undefined}
            onClose={() => setTrace(null)}
          />
        )}
      </div>

      {mode === 'live' && (
        <div className="shrink-0 border-t border-border bg-background px-4 py-3">
          <MessageInput
            className="mx-auto max-w-3xl"
            running={running}
            stopping={stopping}
            onSend={onSend}
            onStop={() => void onStop()}
            placeholder="输入消息，两侧同时发送。Enter 发送，Shift+Enter 换行"
            autoFocus
          />
        </div>
      )}
    </div>
  )
}
