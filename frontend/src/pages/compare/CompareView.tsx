import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { MessagesSquare } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { CompareGroup, CompareSend, CompareUpdate } from '@/api/compareTypes'
import { compareKeys, useAlignment, useSendCompareTurn, useUpdateCompare } from '@/api/queries/compare'
import { cancelTurn } from '@/api/queries/sessions'
import { CompareHeader, type SideSummary } from '@/components/compare/CompareHeader'
import { CompareRowView, type Side, type TraceTarget } from '@/components/compare/CompareRowView'
import { TraceDrawer } from '@/components/compare/TraceDrawer'
import { alignmentHighlights, buildRows, type CompareRow, type SeededFork, type SeededPair } from '@/components/compare/compareUtils'
import { liveAlignTurn } from '@/components/compare/incrementalAlign'
import { MessageInput, totalMetrics, useSessionStream, type TurnState } from '@/components/session'
import { EmptyState } from '@/components/ui/EmptyState'
import { cn } from '@/lib/cn'
import { safeGet, safeSet } from '@/lib/safeStorage'
import { toast } from '@/stores/toast'

const ALIGN_KEY = 'evalp.compare.align'
/**
 * 重放期间服务端不会在轮次间隙发 idle（连接一直开着直到重放结束），所以不需要轮询重连；
 * 只有看到“重放仍在进行但连接已因 idle 关闭”（例如连接被中间层掐断后又恢复）时，才隔这么久重连一次作为兜底。
 */
const REPLAY_REOPEN_FALLBACK_MS = 5_000

const endedCount = (turns: readonly TurnState[]) => turns.filter((t) => t.ended).length

const SEND_SIDES: Array<{ value: CompareSend; label: string }> = [
  { value: 'both', label: '两侧' },
  { value: 'left', label: '仅左侧' },
  { value: 'right', label: '仅右侧' },
]

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
  const updateCompare = useUpdateCompare(group.id)

  const onUpdate = async (patch: CompareUpdate) => {
    try {
      await updateCompare.mutateAsync(patch)
    } catch (e) {
      toast.error('保存失败', { description: e instanceof ApiError ? e.message : undefined })
      throw e
    }
  }

  // ───── 对齐数据：行结构始终来自它；两侧每次 turn.start / turn.end 之后重新拉取 ─────
  const version = [left.turns.length, endedCount(left.turns), right.turns.length, endedCount(right.turns)].join('.')
  const alignment = useAlignment(group.id, version)
  useEffect(() => {
    // turn.end 事件可能先于轮次落库的最终字段到达：稍后再拉一次，保证 final_text / 指标是终态
    const t = setTimeout(() => void qc.invalidateQueries({ queryKey: [...compareKeys.all, 'alignment', group.id] }), 1200)
    return () => clearTimeout(t)
  }, [version, qc, group.id])

  const [seeded, setSeeded] = useState<SeededPair[]>([])
  const [seededForks, setSeededForks] = useState<SeededFork[]>([])
  const baseRows = useMemo(() => buildRows(alignment.data, seeded, seededForks), [alignment.data, seeded, seededForks])
  const leftById = useMemo(() => new Map(left.turns.map((t) => [t.id, t])), [left.turns])
  const rightById = useMemo(() => new Map(right.turns.map((t) => [t.id, t])), [right.turns])

  // 运行中的行用浏览器里根据已收到的事件算出的增量对齐（随输出实时更新）；两侧都结束后以服务端结果为准。
  // 任一侧的轮次还没从 SSE 还原出来（加载中）时也沿用服务端结果。
  const rows = useMemo<CompareRow[]>(
    () =>
      baseRows.map((r) => {
        if (r.fork) return r
        const l = r.leftTurnId ? leftById.get(r.leftTurnId) : undefined
        const rt = r.rightTurnId ? rightById.get(r.rightTurnId) : undefined
        if ((r.leftTurnId && !l) || (r.rightTurnId && !rt)) return r
        if (!(l && !l.ended) && !(rt && !rt.ended)) return r
        return { ...r, align: liveAlignTurn(r.pair, r.leftTurnId, r.rightTurnId, l, rt) }
      }),
    [baseRows, leftById, rightById],
  )
  // 行号只数成对的行；单侧消息（分叉）没有行号
  const ordinals = useMemo(() => {
    let n = 0
    return rows.map((r) => (r.fork ? 0 : ++n))
  }, [rows])

  const [alignOn, setAlignOnState] = useState(() => safeGet(ALIGN_KEY) === '1')
  const setAlignOn = (v: boolean) => {
    setAlignOnState(v)
    safeSet(ALIGN_KEY, v ? '1' : '0')
  }
  const highlights = useMemo(
    () => alignmentHighlights({ turns: rows.flatMap((r) => (r.align ? [r.align] : [])) }),
    [rows],
  )

  // ───── live：发送 / 停止 ─────
  const [sendSide, setSendSide] = useState<CompareSend>('both')
  const [awaiting, setAwaiting] = useState<{ left: string | null; right: string | null } | null>(null)
  const awL = awaiting?.left ? leftById.get(awaiting.left) : undefined
  const awR = awaiting?.right ? rightById.get(awaiting.right) : undefined
  const awaitingDone = !!awaiting && (!awaiting.left || !!awL?.ended) && (!awaiting.right || !!awR?.ended)
  useEffect(() => {
    if (awaitingDone) setAwaiting(null)
  }, [awaitingDone])
  const awaitingMissing = !!awaiting && ((!!awaiting.left && !awL) || (!!awaiting.right && !awR))
  useEffect(() => {
    if (!awaitingMissing) return
    const t = setTimeout(() => setAwaiting(null), 20_000) // 事件始终没到：不要一直卡在运行中
    return () => clearTimeout(t)
  }, [awaitingMissing])

  const running = left.running || right.running || (!!awaiting && !awaitingDone)
  const [stopping, setStopping] = useState(false)

  const onSend = async (text: string, opts: { variables?: Record<string, string> }) => {
    try {
      const r = await sendPair.mutateAsync({ input: text, variables: opts.variables, side: sendSide })
      if (r.pair_index !== null && r.left_turn_id && r.right_turn_id) {
        const pair = r.pair_index
        setSeeded((s) => [...s, { pair, leftTurnId: r.left_turn_id!, rightTurnId: r.right_turn_id! }])
      } else {
        // 单侧消息：不占 pair_index，排在当前最后一个成对行之后
        const side = r.left_turn_id ? 'left' : 'right'
        const turnId = (r.left_turn_id ?? r.right_turn_id)!
        const afterPair = rows.reduce<number | null>((m, row) => (row.fork ? m : Math.max(m ?? row.pair, row.pair)), null)
        setSeededForks((s) => [...s, { side, turnId, afterPair }])
      }
      setAwaiting({ left: r.left_turn_id, right: r.right_turn_id })
      // 连接在上一轮 idle 后已关闭：发送了的一侧从已收到的最大 seq 继续订阅
      if (r.left_turn_id) left.reopen()
      if (r.right_turn_id) right.reopen()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'SESSION_BUSY') toast.warning('有一侧会话正在执行上一轮，请等待结束或先停止')
      else toast.error('发送失败', { description: e instanceof ApiError ? e.message : undefined })
      throw e
    }
  }

  const onStop = async () => {
    const ids = [
      left.runningTurn?.id ?? (awaiting?.left && !awL?.ended ? awaiting.left : null),
      right.runningTurn?.id ?? (awaiting?.right && !awR?.ended ? awaiting.right : null),
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

  // ───── replay：进度来自服务端的重放状态（对比组 replay_status），不再用本地启发式推断 ─────
  const replayStatus = mode === 'replay' ? group.replay_status : null
  const replayActive = replayStatus === 'running'
  const { status: rightStatus, reopen: reopenRight } = right
  useEffect(() => {
    // 重放结束时服务端发 idle：马上刷新一次对比组，让状态不必等下一次轮询
    if (mode === 'replay' && rightStatus === 'idle') void qc.invalidateQueries({ queryKey: compareKeys.detail(group.id) })
  }, [mode, rightStatus, qc, group.id])
  useEffect(() => {
    // 兜底：正常情况下重放期间连接一直开着；仅当重放仍在进行而连接已关闭时才重连
    if (!replayActive || rightStatus !== 'idle') return
    const t = setTimeout(reopenRight, REPLAY_REOPEN_FALLBACK_MS)
    return () => clearTimeout(t)
  }, [replayActive, rightStatus, reopenRight])

  const replay = useMemo(() => {
    if (!replayStatus) return null
    const done = rows.filter((r) => r.rightTurnId && rightById.get(r.rightTurnId)?.ended).length
    return {
      done,
      total: group.replay_turns?.length ?? rows.length,
      status: replayStatus,
      mode: group.replay_mode,
      error: group.replay_error,
    }
  }, [replayStatus, rows, rightById, group.replay_turns, group.replay_mode, group.replay_error])

  // ───── Trace 抽屉 ─────
  const [trace, setTrace] = useState<TraceTarget | null>(null)
  const [selectedStep, setSelectedStep] = useState<string | null>(null)
  const openTrace = useCallback((side: Side, turnId: string, stepId?: string) => {
    setTrace({ side, turnId })
    setSelectedStep(stepId ?? null)
  }, [])
  const traceTurn = trace ? (trace.side === 'left' ? leftById : rightById).get(trace.turnId) : undefined
  const traceRow = trace ? rows.findIndex((r) => (trace.side === 'left' ? r.leftTurnId : r.rightTurnId) === trace.turnId) : -1
  const traceOrdinal = traceRow >= 0 ? ordinals[traceRow] : 0
  const traceIsFork = traceRow >= 0 && !!rows[traceRow].fork
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
        onUpdate={onUpdate}
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
                  key={row.key}
                  row={row}
                  ordinal={ordinals[i]}
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
            title={`${trace.side === 'left' ? '左' : '右'}侧 · ${traceSide.agent_name ?? ''} v${traceSide.version_seq ?? '?'}${traceIsFork ? ' · 单侧消息' : traceOrdinal ? ` · 第 ${traceOrdinal} 轮` : ''}`}
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
          <div className="mx-auto mb-1.5 flex max-w-3xl items-center gap-2 text-xs text-muted-foreground" data-testid="send-side">
            <span>发送到</span>
            <div role="radiogroup" aria-label="发送到" className="inline-flex overflow-hidden rounded-md border border-border">
              {SEND_SIDES.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  role="radio"
                  aria-checked={sendSide === o.value}
                  disabled={running}
                  onClick={() => setSendSide(o.value)}
                  data-testid={`send-side-${o.value}`}
                  className={cn(
                    'px-2.5 py-1 transition-colors disabled:cursor-not-allowed disabled:opacity-60',
                    sendSide === o.value ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
                  )}
                >
                  {o.label}
                </button>
              ))}
            </div>
            {sendSide !== 'both' && <span>单侧消息不参与轮次对齐，之后这一侧的上下文会比另一侧多这条消息。</span>}
          </div>
          <MessageInput
            className="mx-auto max-w-3xl"
            running={running}
            stopping={stopping}
            onSend={onSend}
            onStop={() => void onStop()}
            placeholder={sendSide === 'both' ? '输入消息，两侧同时发送。Enter 发送，Shift+Enter 换行' : `输入消息，只发给${sendSide === 'left' ? '左' : '右'}侧。Enter 发送，Shift+Enter 换行`}
            autoFocus
          />
        </div>
      )}
    </div>
  )
}
