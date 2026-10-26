import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { AlertTriangle, ArrowDownToLine, Ban, Bot, ListTree, MessageSquareText, Timer, User } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { LoadingBlock } from '@/components/ui/Spinner'
import { cn } from '@/lib/cn'
import { BlobLoadBar, useLazyBlobs } from './BlobView'
import { Markdown } from './Markdown'
import type { Message } from '@/api/types'
import { isHighlighted, messageText, type AssistantMessageView, type TurnState } from './sessionState'
import { ToolCallCard } from './ToolCallCard'
import { TurnMetricsBar } from './TurnMetricsBar'

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
  className?: string
}

/** 消息流：用户消息、assistant 消息（Markdown + 流式光标）、工具调用卡片、每轮指标条 */
export function ChatPane({
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
  className,
}: ChatPaneProps) {
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
    <div className={cn('relative min-h-0', className)}>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        onLoadCapture={onMediaLoad}
        onLoadedMetadataCapture={onMediaLoad}
        className="h-full overflow-y-auto px-4 py-4"
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
            {turns.map((t) => (
              <TurnView
                key={t.id}
                turn={t}
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
}

export interface TurnViewProps {
  turn: TurnState
  sessionId?: string
  selected?: boolean
  onSelectTurn?: (turnId: string) => void
  onOpenTrace?: (turnId: string) => void
  highlightSteps?: ReadonlySet<string>
  onSelectStep?: (turnId: string, stepId: string) => void
  className?: string
}

/** 一轮对话：用户消息 -> assistant 消息（含工具调用卡片） -> 状态提示 -> 指标条。可单独使用（双栏对比按行对齐时）。 */
export function TurnView({ turn, sessionId, selected, onSelectTurn, onOpenTrace, highlightSteps, onSelectStep, className }: TurnViewProps) {
  return (
    <section
      data-testid="turn"
      data-turn-id={turn.id}
      data-status={turn.status}
      aria-current={selected || undefined}
      onClick={() => onSelectTurn?.(turn.id)}
      className={cn(
        'space-y-3 rounded-lg border-l-2 py-1 pl-3 transition-colors',
        selected ? 'border-primary' : 'border-transparent',
        onSelectTurn && 'cursor-pointer',
        className,
      )}
    >
      <UserMessage turn={turn} sessionId={sessionId} />

      {turn.messages.map((m) => (
        <AssistantMessage
          key={m.key}
          message={m}
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
  turn,
  sessionId,
  highlightSteps,
  onSelectStep,
}: {
  message: AssistantMessageView
  turn: TurnState
  sessionId?: string
  highlightSteps?: ReadonlySet<string>
  onSelectStep?: (turnId: string, stepId: string) => void
}) {
  const blob = useLazyBlobs(m.contentBlob ? { $blob: m.contentBlob } : null, sessionId)
  const loaded = blob.resolved as { content?: string | null; message?: { content?: string } } | null
  const content = m.contentBlob ? (loaded?.content ?? loaded?.message?.content ?? m.content) : m.content
  const hasText = content.trim().length > 0 || (m.streaming && m.toolCalls.length === 0) || !!m.contentBlob
  const highlighted = isHighlighted(highlightSteps, turn.id, m.stepId)
  // 没有文字也没有工具调用（例如模型调用失败的那一步）：不画空气泡
  if (!hasText && m.toolCalls.length === 0) return null

  return (
    <div className="flex gap-2" data-testid="assistant-message" data-final={m.final || undefined} data-step={m.stepId ?? undefined}>
      <Avatar icon={<Bot className="h-3.5 w-3.5" />} />
      <div className="min-w-0 flex-1 space-y-2">
        {hasText && (
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
        )}
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
