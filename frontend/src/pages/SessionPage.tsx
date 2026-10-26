import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { PanelRightClose, PanelRightOpen, Variable, WifiOff } from 'lucide-react'
import { ApiError } from '@/api/client'
import { sessionKeys, useCancelTurn, useRenameSession, useSendTurn, useSession } from '@/api/queries/sessions'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { LoadingBlock } from '@/components/ui/Spinner'
import { SessionCompareActions } from '@/components/compare/SessionCompareActions'
import { ChatPane, EditableTitle, MessageInput, TraceViewer, totalMetrics, useSessionStream } from '@/components/session'
import { cn } from '@/lib/cn'
import { formatCost, formatTokens } from '@/lib/format'
import { safeGet, safeSet } from '@/lib/safeStorage'
import { useIsNarrow } from '@/lib/useMediaQuery'
import { toast } from '@/stores/toast'
import { MODE_LABEL, sessionTitle, versionLabel } from './session/sessionUtils'
import { SessionStatusBadge } from './session/SessionList'

const TRACE_KEY = 'evalp.session.trace-open'
const TRACE_WIDE_KEY = 'evalp.session.trace-wide'

/**
 * 会话页：左侧是对话（ChatPane + MessageInput），右侧是可收起的 Trace。
 * 对话内容完全来自 SSE（先回放历史，再接实时），所以从历史打开与实时查看走同一条路径。
 */
export default function SessionPage() {
  const { id } = useParams()
  const detail = useSession(id, '')
  if (detail.isLoading) return <LoadingBlock label="加载会话…" className="py-24" />
  if (detail.error || !detail.data) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <div className="p-6 text-sm text-danger">
        {notFound ? '会话不存在' : `无法加载会话：${detail.error instanceof ApiError ? detail.error.message : '未知错误'}`}
        <Link to="/agents" className="ml-3 text-primary underline">
          返回 Agents
        </Link>
      </div>
    )
  }
  return <SessionView key={id} sessionId={id!} />
}

function SessionView({ sessionId }: { sessionId: string }) {
  const qc = useQueryClient()
  const detail = useSession(sessionId, '')
  const session = detail.data!.session
  const stream = useSessionStream(sessionId)
  const sendTurn = useSendTurn(sessionId)
  const cancel = useCancelTurn()
  const rename = useRenameSession(sessionId)
  const onRename = async (title: string) => {
    try {
      await rename.mutateAsync(title)
    } catch (e) {
      toast.error('修改标题失败', { description: e instanceof ApiError ? e.message : undefined })
      throw e
    }
  }

  // 刚发出的轮次：POST 返回到 SSE 送来 turn.start 之间的空档里，也要显示为运行中
  const [awaitingTurnId, setAwaitingTurnId] = useState<string | null>(null)
  const awaitingTurn = awaitingTurnId ? stream.turns.find((t) => t.id === awaitingTurnId) : undefined
  useEffect(() => {
    if (!awaitingTurnId) return
    if (awaitingTurn?.ended) setAwaitingTurnId(null)
  }, [awaitingTurnId, awaitingTurn?.ended])
  useEffect(() => {
    if (!awaitingTurnId || awaitingTurn) return
    const t = setTimeout(() => setAwaitingTurnId(null), 15_000) // 事件始终没到：不要一直卡在运行中
    return () => clearTimeout(t)
  }, [awaitingTurnId, awaitingTurn])

  const running = stream.running || (awaitingTurnId !== null && !awaitingTurn?.ended)
  const runningTurnId = stream.runningTurn?.id ?? awaitingTurnId

  // 轮次结束后刷新会话信息（标题、累计指标、状态）
  const endedCount = stream.turns.filter((t) => t.ended).length
  useEffect(() => {
    void qc.invalidateQueries({ queryKey: sessionKeys.session(sessionId) })
    void qc.invalidateQueries({ queryKey: [...sessionKeys.all, 'list'] })
  }, [endedCount, qc, sessionId])

  // Trace：默认显示最新一轮；点击某一轮后固定到那一轮，发送新消息后重新跟随
  const [pinnedTurnId, setPinnedTurnId] = useState<string | null>(null)
  const [selectedStep, setSelectedStep] = useState<string | null>(null)
  const lastTurn = stream.turns[stream.turns.length - 1]
  const traceTurn = stream.turns.find((t) => t.id === pinnedTurnId) ?? lastTurn
  const traceTurnIndex = traceTurn ? stream.turns.indexOf(traceTurn) + 1 : 0

  // 窄屏（≤800px）：Trace 改为从底部弹出的抽屉，默认收起，开合状态不写入偏好（偏好只属于宽屏的侧栏）
  const narrow = useIsNarrow()
  const [sideOpen, setSideOpen] = useState(() => safeGet(TRACE_KEY) !== '0')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const traceOpen = narrow ? drawerOpen : sideOpen
  const [wide, setWideState] = useState(() => safeGet(TRACE_WIDE_KEY) === '1')
  const setTraceOpen = (v: boolean) => {
    if (narrow) {
      setDrawerOpen(v)
      return
    }
    setSideOpen(v)
    safeSet(TRACE_KEY, v ? '1' : '0')
  }
  const setWide = (v: boolean) => {
    setWideState(v)
    safeSet(TRACE_WIDE_KEY, v ? '1' : '0')
  }

  const selectTurn = useCallback((turnId: string) => {
    setPinnedTurnId(turnId)
    setSelectedStep(null)
  }, [])

  const onSend = async (text: string, opts: { variables?: Record<string, string> }) => {
    try {
      const r = await sendTurn.mutateAsync({ input: text, variables: opts.variables })
      setPinnedTurnId(null)
      setSelectedStep(null)
      setAwaitingTurnId(r.turn_id)
      stream.reopen() // 连接在上一轮 idle 后已关闭，从已收到的最大 seq 继续订阅
    } catch (e) {
      if (e instanceof ApiError && e.code === 'SESSION_BUSY') toast.warning('会话正在执行上一轮，请稍候或先停止')
      else toast.error('发送失败', { description: e instanceof ApiError ? e.message : undefined })
      throw e
    }
  }

  const onStop = async () => {
    if (!runningTurnId) return
    try {
      await cancel.mutateAsync(runningTurnId)
    } catch (e) {
      toast.error('停止失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  const totals = useMemo(() => totalMetrics(stream.turns), [stream.turns])
  const hasTurns = stream.turns.length > 0
  // 回放期间用服务端累计值；有事件后用实时累计（运行中也在涨）
  const stats = session.stats
  const metrics = {
    turns: hasTurns ? stream.turns.length : (stats.turns ?? 0),
    tokens: hasTurns ? totals.totalTokens : (stats.prompt_tokens ?? 0) + (stats.completion_tokens ?? 0),
    cost: hasTurns ? totals.cost : (stats.cost ?? 0),
    llm: hasTurns ? totals.llmCalls : (stats.llm_calls ?? 0),
    tools: hasTurns ? totals.toolCalls : (stats.tool_calls ?? 0),
  }
  const varCount = Object.keys(session.variables ?? {}).length

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="session-page" data-running={running || undefined}>
      {/* 顶部：标题、绑定的 agent 版本、模式、累计指标 */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <EditableTitle value={sessionTitle(session)} onSave={onRename} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <Link
            to={`/agents/${session.agent_id}`}
            className="font-medium text-primary hover:underline"
            data-testid="agent-link"
          >
            {session.agent_name ?? session.agent_id}
          </Link>
          <Link
            to={`/agents/${session.agent_id}?tab=versions`}
            className="rounded bg-muted px-1.5 py-0.5 font-mono hover:bg-accent"
            title={`版本 ${session.version_id}`}
            data-testid="version-link"
          >
            {versionLabel(session)}
          </Link>
          <Badge variant="outline">{MODE_LABEL[session.mode]}</Badge>
          {session.replay_mode && <Badge variant="outline">{session.replay_mode === 'anchored' ? '锚定重放' : '自由重放'}</Badge>}
          <SessionStatusBadge status={running ? 'running' : session.status} />
          {varCount > 0 && (
            <Badge variant="secondary" title={JSON.stringify(session.variables)}>
              <Variable className="h-3 w-3" />
              变量 {varCount}
            </Badge>
          )}
          {session.source_session_id && (
            <Link to={`/sessions/${session.source_session_id}`} className="text-muted-foreground underline">
              来源会话
            </Link>
          )}
        </div>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground" data-testid="session-metrics">
          <span title="轮数">{metrics.turns} 轮</span>
          <span title="累计 tokens">{formatTokens(metrics.tokens)} tokens</span>
          <span title="累计成本">{formatCost(metrics.cost)}</span>
          <span title="LLM 调用 / 工具调用">
            LLM ×{metrics.llm} · 工具 ×{metrics.tools}
          </span>
          <SessionCompareActions session={session} />
          <Button
            size="sm"
            variant="outline"
            onClick={() => setTraceOpen(!traceOpen)}
            aria-pressed={traceOpen}
            data-testid="toggle-trace"
          >
            {traceOpen ? <PanelRightClose className="h-3.5 w-3.5" /> : <PanelRightOpen className="h-3.5 w-3.5" />}
            Trace
          </Button>
        </div>
      </div>

      {stream.status === 'reconnecting' && (
        <div role="status" className="flex items-center gap-2 border-b border-warning/40 bg-warning/10 px-4 py-1 text-xs text-warning">
          <WifiOff className="h-3.5 w-3.5" />
          实时连接中断，正在重连…
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        {/* 左：对话 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <ChatPane
            className="flex-1"
            turns={stream.turns}
            sessionId={sessionId}
            loading={stream.loading}
            selectedTurnId={traceTurn?.id ?? null}
            onSelectTurn={selectTurn}
            onOpenTrace={(turnId) => {
              selectTurn(turnId)
              if (narrow) setTraceOpen(true)
            }}
            onSelectStep={(turnId, stepId) => {
              setPinnedTurnId(turnId)
              setSelectedStep(stepId)
              setTraceOpen(true)
            }}
          />
          <div className="shrink-0 border-t border-border bg-background px-4 py-3">
            <MessageInput
              className="mx-auto max-w-3xl"
              running={running}
              stopping={cancel.isPending}
              onSend={onSend}
              onStop={onStop}
              autoFocus
            />
          </div>
        </div>

        {/* 右：Trace */}
        {traceOpen && (
          <aside
            className={cn(
              'flex min-h-0 flex-col bg-card',
              narrow
                ? 'absolute inset-x-0 bottom-0 z-20 h-[70%] border-t border-border shadow-xl'
                : cn('shrink-0 border-l border-border', wide ? 'w-[min(60%,860px)]' : 'w-[min(42%,560px)]'),
            )}
            aria-label="Trace"
            data-testid="trace-panel"
            data-layout={narrow ? 'drawer' : 'side'}
          >
            <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5">
              <span className="text-xs font-semibold">Trace</span>
              {traceTurn ? (
                <span className="text-xs text-muted-foreground" data-testid="trace-turn">
                  第 {traceTurnIndex} / {stream.turns.length} 轮
                </span>
              ) : (
                <span className="text-xs text-muted-foreground">暂无轮次</span>
              )}
              <div className="ml-auto flex items-center gap-1">
                {!narrow && (
                  <Button size="sm" variant="ghost" onClick={() => setWide(!wide)}>
                    {wide ? '收窄' : '加宽'}
                  </Button>
                )}
                <Button size="icon" variant="ghost" aria-label="收起 Trace" onClick={() => setTraceOpen(false)} className="h-7 w-7">
                  <PanelRightClose className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
            {traceTurn ? (
              <TraceViewer
                key={traceTurn.id}
                className="flex-1"
                steps={traceTurn.steps}
                turnId={traceTurn.id}
                sessionId={sessionId}
                startedAt={traceTurn.startedAt}
                endedAt={traceTurn.endedAt}
                running={!traceTurn.ended}
                selectedStepId={selectedStep}
                onSelectStep={setSelectedStep}
              />
            ) : (
              <p className="p-6 text-center text-xs text-muted-foreground">发送消息后，这里会显示每一轮的执行步骤。</p>
            )}
          </aside>
        )}
      </div>
    </div>
  )
}
