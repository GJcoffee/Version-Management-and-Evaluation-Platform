import { forwardRef, useCallback, useImperativeHandle, useLayoutEffect, useReducer, useRef, useState, type ReactNode } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { AlertTriangle, ArrowDownToLine, Ban, Bot, ChevronRight, ChevronsDownUp, ChevronsUpDown, ListTree, MessageSquareText, Timer, User } from 'lucide-react'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { LoadingBlock } from '@/components/ui/Spinner'
import { cn } from '@/lib/cn'
import { BlobLoadBar, useLazyBlobs } from './BlobView'
import { Markdown } from './Markdown'
import type { Message, TurnStatus } from '@/api/types'
import { isHighlighted, messageText, turnInputSummary, type AssistantMessageView, type TurnState } from './sessionState'
import { ToolCallCard } from './ToolCallCard'
import { TurnMetricsBar } from './TurnMetricsBar'
import { MiddlewareTurnDetails } from './MiddlewareTurnDetails'

/** 轮次数超过该值时启用虚拟滚动（几百轮的长会话）；短会话保持完整渲染，行为与以前完全一致 */
export const VIRTUALIZE_THRESHOLD = 60
/** 虚拟滚动下未测量轮次的估算高度（px），测量后以实际高度为准 */
const ESTIMATED_TURN_HEIGHT = 260
/** 轮与轮之间的间距（与完整渲染时的 gap-6 一致，px） */
const TURN_GAP = 24
/** 新内容到达后多久之内，列表总高度的变化（测量修正）仍会触发贴底，毫秒 */
const SETTLE_MS = 600

export interface ChatPaneProps {
  turns: readonly TurnState[]
  /** 用于读取转存为 blob 的大内容 */
  sessionId?: string
  /** 正在连接 / 回放历史 */
  loading?: boolean
  /** 当前选中的轮次（高亮左边框），如 Trace 正在显示的那一轮 */
  selectedTurnId?: string | null
  onSelectTurn?: (turnId: string) => void
  /** 点击轮次底部「查看 Trace」按钮：默认等同 onSelectTurn；窄屏下用来打开 Trace 抽屉 */
  onOpenTrace?: (turnId: string) => void
  /**
   * 高亮的步骤集合（双栏对比时标出差异）。元素是 step_id（如 `s2`，匹配所有轮次），
   * 或限定轮次的 `trn_xxx:s2`（见 highlightKey）。
   */
  highlightSteps?: ReadonlySet<string>
  /** 点击工具调用卡片：用于联动 Trace */
  onSelectStep?: (turnId: string, stepId: string) => void
  emptyState?: ReactNode
  /** 新内容到达时自动滚到底部（用户向上滚动后暂停），默认 true */
  autoScroll?: boolean
  /** 未手动切换过的轮次默认折叠，适用于打开会话查看历史 */
  defaultCollapsed?: boolean
  className?: string
}

export interface ChatPaneHandle {
  /** 滚动到某一轮（顶部对齐）；该轮已折叠时先展开。不改变 Trace 选中的轮次。 */
  scrollToTurn: (turnId: string) => void
}

/**
 * 消息流：每轮一个可折叠的标题行（第 N 轮 + 输入摘要 + 状态 + 统计），其下是用户消息、
 * assistant 消息（Markdown + 流式光标）、工具调用卡片、每轮指标条。折叠状态只保存在内存里。
 */
export const ChatPane = forwardRef<ChatPaneHandle, ChatPaneProps>(function ChatPane({
  turns,
  sessionId,
  loading,
  selectedTurnId,
  onSelectTurn,
  onOpenTrace,
  highlightSteps,
  onSelectStep,
  emptyState,
  autoScroll = true,
  defaultCollapsed = false,
  className,
}, ref) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const stuckRef = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const virtual = turns.length > VIRTUALIZE_THRESHOLD
  const lastChangeAt = useRef(0)

  const virt = useVirtualizer({
    count: virtual ? turns.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATED_TURN_HEIGHT + TURN_GAP,
    getItemKey: (i) => turns[i]?.id ?? i,
    overscan: 4,
  })
  const totalSize = virtual ? virt.getTotalSize() : 0

  // 只记录手动切换的轮次，异步回放到达的历史也沿用默认折叠状态。
  const [foldStates, setFoldStates] = useState<ReadonlyMap<string, boolean>>(() => new Map())
  const isCollapsed = (turnId: string) => foldStates.get(turnId) ?? defaultCollapsed
  const resetMeasurements = useRef(false)
  const foldScrollAnchor = useRef<{ index: number; offset: number } | null>(null)
  const pauseAutoScroll = useCallback(() => {
    stuckRef.current = false
    lastChangeAt.current = 0
  }, [])
  const toggleTurn = useCallback((turnId: string) => {
    pauseAutoScroll()
    setFoldStates((prev) => {
      const next = new Map(prev)
      next.set(turnId, !(prev.get(turnId) ?? defaultCollapsed))
      return next
    })
  }, [defaultCollapsed, pauseAutoScroll])
  const setAllFolded = (folded: boolean) => {
    pauseAutoScroll()
    const pane = scrollRef.current
    const paneTop = pane?.getBoundingClientRect().top ?? 0
    const anchor = virtual && pane
      ? Array.from(pane.querySelectorAll<HTMLElement>('[data-index]')).find((el) => el.getBoundingClientRect().bottom > paneTop)
      : undefined
    foldScrollAnchor.current = anchor && pane ? {
      index: Number(anchor.dataset.index),
      offset: paneTop + (parseFloat(getComputedStyle(pane).paddingTop) || 0) - anchor.getBoundingClientRect().top,
    } : null
    resetMeasurements.current = true
    setFoldStates(new Map(turns.map((t) => [t.id, folded])))
  }
  const collapseAll = () => setAllFolded(true)
  const expandAll = () => setAllFolded(false)

  // 单轮开合只重新测量已挂载的内容，保留屏幕上方的高度缓存，避免标题移位；
  // 批量开合同时影响未挂载的轮次，需要清掉全部测量缓存。
  const measuredFor = useRef(foldStates)
  useLayoutEffect(() => {
    if (!virtual || measuredFor.current === foldStates) return
    measuredFor.current = foldStates
    if (resetMeasurements.current) {
      resetMeasurements.current = false
      virt.measure()
    }
    scrollRef.current!.querySelectorAll<HTMLElement>('[data-index]').forEach((el) => virt.measureElement(el))
  }, [foldStates, virtual, virt])

  useLayoutEffect(() => {
    const anchor = foldScrollAnchor.current
    // 等新总高度提交到 DOM 后再定位，避免浏览器按旧高度把滚动位置截断。
    if (!anchor || totalSize !== virt.getTotalSize()) return
    foldScrollAnchor.current = null
    const item = virt.measurementsCache[anchor.index]
    const target = item ? item.start + anchor.offset : null
    if (target !== null && Math.abs(scrollRef.current!.scrollTop - target) > 0.5) virt.scrollToOffset(target)
  })

  // 滚动到指定轮次：先展开（状态更新），在提交后的布局阶段再滚动，这样量到的是展开后的高度
  const pendingScroll = useRef<string | null>(null)
  const [, requestRender] = useReducer((n: number) => n + 1, 0)
  useImperativeHandle(
    ref,
    () => ({
      scrollToTurn: (turnId) => {
        pendingScroll.current = turnId
        setFoldStates((prev) => {
          if (prev.get(turnId) === false) return prev
          const next = new Map(prev)
          next.set(turnId, false)
          return next
        })
        requestRender()
      },
    }),
    [],
  )
  useLayoutEffect(() => {
    const turnId = pendingScroll.current
    if (turnId === null) return
    pendingScroll.current = null
    stuckRef.current = false
    if (virtual) {
      virt.scrollToIndex(
        turns.findIndex((t) => t.id === turnId),
        { align: 'start' },
      )
      return
    }
    const pane = scrollRef.current!
    const target = Array.from(pane.querySelectorAll<HTMLElement>('[data-turn-id]')).find((e) => e.dataset.turnId === turnId)!
    pane.scrollTop = target.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop
  })

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    stuckRef.current = near
    setShowJump(!near)
  }, [])

  // 内容变化的签名：轮次数、最后一轮的消息数 / 文本长度 / 工具调用数 / 状态
  const last = turns[turns.length - 1]
  const lastMsg = last?.messages[last.messages.length - 1]
  const signature = [
    turns.length,
    last?.messages.length ?? 0,
    lastMsg?.content.length ?? 0,
    lastMsg?.toolCalls.length ?? 0,
    last?.status ?? '',
    last?.events.length ?? 0,
  ].join(':')

  useLayoutEffect(() => {
    lastChangeAt.current = Date.now()
    if (!autoScroll || !stuckRef.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [signature, autoScroll])

  // 虚拟滚动：新内容到达后，轮次被测量会修正总高度，贴底状态下需要继续跟随；
  // 超过 SETTLE_MS 后的高度变化（例如用户手动展开工具卡片）不再强制贴底
  useLayoutEffect(() => {
    if (!virtual || !autoScroll || !stuckRef.current) return
    if (Date.now() - lastChangeAt.current > SETTLE_MS) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [totalSize, virtual, autoScroll])

  // 图片 / 视频异步加载完成会撑高内容：贴底状态下继续跟随，避免新内容被挤出视野
  const onMediaLoad = useCallback(() => {
    const el = scrollRef.current
    if (el && autoScroll && stuckRef.current) el.scrollTop = el.scrollHeight
  }, [autoScroll])

  const jump = () => {
    const el = scrollRef.current
    if (!el) return
    stuckRef.current = true
    lastChangeAt.current = Date.now()
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    setShowJump(false)
  }

  return (
    <div className={cn('relative flex min-h-0 flex-col', className)}>
      {turns.length > 0 && (
        <div className="flex shrink-0 items-center justify-end gap-1 border-b border-border px-4 py-1" data-testid="fold-toolbar">
          <Button size="sm" variant="ghost" onClick={collapseAll}>
            <ChevronsDownUp className="h-3.5 w-3.5" />
            全部折叠
          </Button>
          <Button size="sm" variant="ghost" onClick={expandAll}>
            <ChevronsUpDown className="h-3.5 w-3.5" />
            全部展开
          </Button>
        </div>
      )}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        onLoadCapture={onMediaLoad}
        onLoadedMetadataCapture={onMediaLoad}
        onClickCapture={(event) => {
          // 工具卡片等内部内容展开后，异步媒体加载和虚拟高度修正也不应把视野推到底部。
          if (event.target instanceof Element && event.target.closest('button[aria-expanded]')) pauseAutoScroll()
        }}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
        style={{ overflowAnchor: 'none' }}
        data-testid="chat-pane"
      >
        {turns.length === 0 ? (
          loading ? (
            <LoadingBlock label="加载会话…" className="py-16" />
          ) : (
            (emptyState ?? (
              <div className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
                <MessageSquareText className="h-8 w-8" strokeWidth={1.5} />
                <p className="text-sm">还没有消息，在下方输入内容开始对话</p>
              </div>
            ))
          )
        ) : virtual ? (
          <div
            className="relative mx-auto w-full max-w-3xl"
            style={{ height: virt.getTotalSize() }}
            data-testid="chat-virtual"
          >
            {virt.getVirtualItems().map((v) => {
              const t = turns[v.index]
              return (
                <div
                  key={v.key}
                  ref={virt.measureElement}
                  data-index={v.index}
                  className="absolute left-0 top-0 w-full"
                  style={{ transform: `translateY(${v.start}px)`, paddingBottom: TURN_GAP }}
                >
                  <TurnView
                    turn={t}
                    fold={{ ordinal: v.index + 1, collapsed: isCollapsed(t.id), onToggle: () => toggleTurn(t.id) }}
                    sessionId={sessionId}
                    selected={selectedTurnId === t.id}
                    onSelectTurn={onSelectTurn}
                    onOpenTrace={onOpenTrace}
                    highlightSteps={highlightSteps}
                    onSelectStep={onSelectStep}
                  />
                </div>
              )
            })}
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-6">
            {turns.map((t, i) => (
              <TurnView
                key={t.id}
                turn={t}
                fold={{ ordinal: i + 1, collapsed: isCollapsed(t.id), onToggle: () => toggleTurn(t.id) }}
                sessionId={sessionId}
                selected={selectedTurnId === t.id}
                onSelectTurn={onSelectTurn}
                onOpenTrace={onOpenTrace}
                highlightSteps={highlightSteps}
                onSelectStep={onSelectStep}
              />
            ))}
          </div>
        )}
      </div>
      {showJump && (
        <Button
          size="sm"
          variant="outline"
          className="absolute bottom-3 right-5 bg-card shadow"
          onClick={jump}
          aria-label="回到底部"
        >
          <ArrowDownToLine className="h-3.5 w-3.5" />
          回到底部
        </Button>
      )}
    </div>
  )
})

export interface TurnViewProps {
  turn: TurnState
  /** 传入后在顶部显示可折叠的标题行；会话页和双栏对比共用。 */
  fold?: TurnFold
  sessionId?: string
  selected?: boolean
  onSelectTurn?: (turnId: string) => void
  onOpenTrace?: (turnId: string) => void
  highlightSteps?: ReadonlySet<string>
  onSelectStep?: (turnId: string, stepId: string) => void
  className?: string
}

export interface TurnFold {
  /** 第几轮（从 1 开始） */
  ordinal: number
  collapsed: boolean
  onToggle: () => void
}

const TURN_STATUS: Record<TurnStatus, { label: string; variant: BadgeVariant }> = {
  running: { label: '运行中', variant: 'info' },
  ok: { label: '已完成', variant: 'success' },
  error: { label: '出错', variant: 'danger' },
  cancelled: { label: '已取消', variant: 'outline' },
  timeout: { label: '超时', variant: 'warning' },
}

/** 轮次标题行：点击折叠 / 展开。统计复用指标条（tokens、用时、TTFT、成本、LLM / 工具调用次数）。 */
function TurnHeader({ turn, fold, selected }: { turn: TurnState; fold: TurnFold; selected?: boolean }) {
  const status = TURN_STATUS[turn.status]
  return (
    <div
      data-testid="turn-header"
      onClick={(e) => {
        e.stopPropagation() // 折叠不改变 Trace 选中的轮次
        fold.onToggle()
      }}
      className={cn(
        'flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-1 hover:bg-accent/60',
        selected && 'bg-primary/10',
      )}
    >
      <button type="button" aria-expanded={!fold.collapsed} className="flex min-w-0 flex-1 basis-56 items-center gap-2 text-left text-sm">
        <ChevronRight className={cn('h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform', !fold.collapsed && 'rotate-90')} />
        <span className="shrink-0 font-medium">第 {fold.ordinal} 轮</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground" data-testid="turn-summary">
          {turnInputSummary(turn).slice(0, 200)}
        </span>
        <Badge variant={status.variant} data-testid="turn-status">
          {status.label}
        </Badge>
      </button>
      <TurnMetricsBar turn={turn} testId="turn-header-metrics" />
    </div>
  )
}

/** 一轮对话：标题行 -> 用户消息 -> assistant 消息（含工具调用卡片） -> 状态提示 -> 指标条。可单独使用（双栏对比按行对齐时）。 */
export function TurnView({ turn, fold, sessionId, selected, onSelectTurn, onOpenTrace, highlightSteps, onSelectStep, className }: TurnViewProps) {
  return (
    <section
      data-testid="turn"
      data-turn-id={turn.id}
      data-status={turn.status}
      data-collapsed={fold?.collapsed || undefined}
      aria-current={selected || undefined}
      onClick={() => onSelectTurn?.(turn.id)}
      className={cn(
        'space-y-3 rounded-lg border-l-2 py-1 pl-3 transition-colors',
        selected ? 'border-primary' : 'border-transparent',
        onSelectTurn && 'cursor-pointer',
        className,
      )}
    >
      {fold && <TurnHeader turn={turn} fold={fold} selected={selected} />}

      {!fold?.collapsed && (
        <>
          <UserMessage turn={turn} sessionId={sessionId} />
          {turn.middlewareSnapshot && (
            <MiddlewareTurnDetails turnId={turn.id} sessionId={sessionId} initial={turn.middlewareSnapshot} />
          )}

          {turn.messages.map((m, i) => (
            <AssistantMessage
              key={m.key}
              message={m}
              nextMessage={turn.messages[i + 1]}
              turn={turn}
              sessionId={sessionId}
              highlightSteps={highlightSteps}
              onSelectStep={onSelectStep}
            />
          ))}

          {turn.status === 'running' && turn.messages.length === 0 && (
            <div className="flex gap-2">
              <Avatar icon={<Bot className="h-3.5 w-3.5" />} />
              <div className="rounded-2xl rounded-tl-sm bg-muted px-3 py-2 text-sm text-muted-foreground" data-testid="thinking">
                <span className="stream-cursor" aria-label="等待模型响应" />
              </div>
            </div>
          )}

          <TurnStatusBanner turn={turn} />

          <TurnMetricsBar turn={turn} className="pl-9">
            {onSelectTurn && (
              <button
                type="button"
                className={cn(
                  'inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent hover:text-foreground',
                  selected && 'text-primary',
                )}
                onClick={(e) => {
                  e.stopPropagation()
                  if (onOpenTrace) onOpenTrace(turn.id)
                  else onSelectTurn(turn.id)
                }}
                data-testid="select-turn"
              >
                <ListTree className="h-3 w-3" />
                {selected ? '正在查看 Trace' : '查看 Trace'}
              </button>
            )}
          </TurnMetricsBar>
        </>
      )}
    </section>
  )
}

function UserMessage({ turn, sessionId }: { turn: TurnState; sessionId?: string }) {
  const blob = useLazyBlobs(turn.inputBlob ? { $blob: turn.inputBlob } : null, sessionId)
  const loaded = blob.resolved as { input?: Message } | null
  const input = turn.input ?? loaded?.input ?? null
  if (!input && !turn.inputBlob) return null
  const text = messageText(input)
  return (
    <div className="flex justify-end gap-2" data-testid="user-message">
      <div className="max-w-[85%] min-w-0">
        {!input && (
          <BlobLoadBar pending={blob.pending} loading={blob.loading} error={blob.error} onLoad={blob.load} label="输入内容较大，已转存" />
        )}
        {input && (
          <div className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm bg-primary px-3 py-2 text-sm text-primary-foreground">
            {text || <span className="opacity-70">（空消息）</span>}
          </div>
        )}
      </div>
      <Avatar icon={<User className="h-3.5 w-3.5" />} />
    </div>
  )
}

function Avatar({ icon }: { icon: ReactNode }) {
  return (
    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
      {icon}
    </span>
  )
}

function AssistantMessage({
  message: m,
  nextMessage,
  turn,
  sessionId,
  highlightSteps,
  onSelectStep,
}: {
  message: AssistantMessageView
  nextMessage?: AssistantMessageView
  turn: TurnState
  sessionId?: string
  highlightSteps?: ReadonlySet<string>
  onSelectStep?: (turnId: string, stepId: string) => void
}) {
  const blob = useLazyBlobs(m.contentBlob ? { $blob: m.contentBlob } : null, sessionId)
  const loaded = blob.resolved as { content?: string | null; message?: { content?: string } } | null
  const loadedContent = loaded?.content ?? loaded?.message?.content
  const content = m.contentBlob ? (loadedContent ?? m.content) : m.content
  // 代码型 agent 可能把最后一次模型响应再次记录为最终消息。只隐藏紧邻最终消息的相同正文，
  // 工具卡片仍保留在最终回复之前；独立模型步骤、流式输出及未加载的 blob 不合并，Trace 不受影响。
  const repeatedFinal = !m.final && !m.streaming && (!m.contentBlob || (blob.pending === 0 && typeof loadedContent === 'string'))
    && nextMessage?.final && !nextMessage.streaming && !nextMessage.contentBlob
    && content.trim().length > 0 && content === nextMessage.content
  const hasText = !repeatedFinal && (content.trim().length > 0 || (m.streaming && m.toolCalls.length === 0) || !!m.contentBlob)
  const highlighted = isHighlighted(highlightSteps, turn.id, m.stepId)
  // 没有文字也没有工具调用（例如模型调用失败的那一步）：不画空气泡
  if (!hasText && m.toolCalls.length === 0) return null

  // 出错 / 取消等轮次可能没有最终消息事件，已结束轮次的末条结果仍应展示在工具轨迹之后。
  const afterTools = turn.ended && !nextMessage && m.toolCalls.length > 0
  const textBubble = hasText && (
    <div
      className={cn(
        'rounded-2xl rounded-tl-sm bg-muted px-3 py-2',
        highlighted && 'ring-2 ring-warning/70 ring-offset-1 ring-offset-background',
        turn.status === 'error' && m.final && 'bg-danger/10',
      )}
      title={m.llm?.model ? `模型：${m.llm.model}` : undefined}
    >
      {m.contentBlob && (
        <BlobLoadBar pending={blob.pending} loading={blob.loading} error={blob.error} onLoad={blob.load} label="回复内容较大，已转存" />
      )}
      <Markdown streaming={m.streaming}>{content}</Markdown>
      {m.partial && (
        <p className="mt-1.5 text-xs text-muted-foreground" data-testid="partial-mark">
          {turn.status === 'cancelled' ? '已取消，以上为已生成的部分内容' : '执行中断，以上为已生成的部分内容'}
        </p>
      )}
    </div>
  )

  return (
    <div className="flex gap-2" data-testid="assistant-message" data-final={m.final || undefined} data-step={m.stepId ?? undefined}>
      <Avatar icon={<Bot className="h-3.5 w-3.5" />} />
      <div className="min-w-0 flex-1 space-y-2">
        {!afterTools && textBubble}
        {m.toolCalls.length > 0 && (
          <div className="space-y-1.5">
            {m.toolCalls.map((c) => (
              <ToolCallCard
                key={c.id}
                call={c}
                sessionId={sessionId}
                highlighted={isHighlighted(highlightSteps, turn.id, c.stepId)}
                onSelect={(call) => call.stepId && onSelectStep?.(turn.id, call.stepId)}
              />
            ))}
          </div>
        )}
        {afterTools && textBubble}
      </div>
    </div>
  )
}

/** 错误 / 取消 / 超时的明确提示 */
export function TurnStatusBanner({ turn }: { turn: Pick<TurnState, 'status' | 'error'> }) {
  if (turn.status === 'error') {
    return (
      <div role="alert" data-testid="turn-error" className="ml-8 flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 font-medium">
            执行出错
            {turn.error && <Badge variant="danger" className="font-mono">{turn.error.code}</Badge>}
            {turn.error?.retriable && <Badge variant="outline">可重试</Badge>}
          </div>
          <p className="mt-0.5 whitespace-pre-wrap break-words">{turn.error?.message ?? '没有更多错误信息'}</p>
        </div>
      </div>
    )
  }
  if (turn.status === 'timeout') {
    return (
      <div role="alert" data-testid="turn-timeout" className="ml-8 flex items-start gap-2 rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        <Timer className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 font-medium">
            执行超时
            {turn.error && <Badge variant="warning" className="font-mono">{turn.error.code}</Badge>}
          </div>
          {turn.error?.message && <p className="mt-0.5 whitespace-pre-wrap break-words">{turn.error.message}</p>}
        </div>
      </div>
    )
  }
  if (turn.status === 'cancelled') {
    return (
      <div data-testid="turn-cancelled" className="ml-8 flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
        <Ban className="h-3.5 w-3.5 shrink-0" />
        已取消：本轮被手动停止，已生成的内容保留在上方
      </div>
    )
  }
  return null
}
