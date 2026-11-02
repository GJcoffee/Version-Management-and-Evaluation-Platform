import { ArrowDown, ArrowUp, Bot, Clock, Coins, Cpu, Timer, Wrench } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { formatCost, formatDuration, formatTokens } from '@/lib/format'
import { useNow } from '@/lib/useNow'
import type { TurnMetrics } from './sessionState'
import { turnTiming, turnTtftMs, type TimedTurn } from './turnTiming'
import { exactSeconds, localTimeTitle, traceStatus, upstreamTiming } from './upstreamTiming'

function Item({ icon, label, title, children }: { icon: ReactNode; label: string; title: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1" title={title} data-metric={label}>
      {icon}
      <span className="font-mono tabular-nums text-foreground">{children}</span>
    </span>
  )
}

/** 轮次完整执行耗时（录制轮次采用原始耗时）。 */
export function turnElapsedMs(turn: TimedTurn, now: number): number | null {
  return turnTiming(turn, now).durationMs
}

/** 每一轮底部的指标条（折叠标题行上也复用同一组指标）：tokens、耗时、TTFT、成本、LLM 调用次数、工具调用次数 */
export function TurnMetricsBar({
  turn,
  className,
  testId = 'turn-metrics',
  children,
}: {
  turn: TimedTurn
  className?: string
  /** 同一轮里出现两次（标题行 + 底部指标条）时，用不同的 testid 区分 */
  testId?: string
  /** 右侧附加内容（例如“查看 Trace”按钮） */
  children?: ReactNode
}) {
  const m: TurnMetrics = turn.metrics
  const running = !turn.ended
  const now = useNow(running, 500)
  const elapsed = turnElapsedMs(turn, now)
  const upstream = upstreamTiming(turn.events ?? [])
  const trace = traceStatus(turn.events ?? [])
  const metricEvents = turn.events?.filter((e) => e.type === 'metrics') ?? []
  const unified = metricEvents.length
    ? Object.assign({}, ...metricEvents.map((e) => e.payload)) as Record<string, unknown> : undefined
  const usage = unified?.usage as Record<string, unknown> | undefined
  return (
    <div
      data-testid={testId}
      className={cn('flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground', className)}
    >
      <Item
        icon={<Cpu className="h-3 w-3" />}
        label="tokens"
        title={`输入 ${m.promptTokens} + 输出 ${m.completionTokens} tokens${m.final ? '' : '（实时累计）'}`}
      >
        <ArrowUp className="mr-0.5 inline h-2.5 w-2.5" />
        {usage && usage.prompt_tokens == null ? '未提供' : formatTokens(m.promptTokens)}
        <ArrowDown className="ml-1 mr-0.5 inline h-2.5 w-2.5" />
        {usage && usage.completion_tokens == null ? '未提供' : formatTokens(m.completionTokens)}
      </Item>
      {usage && <span>观测{usage.observation_status === 'complete' ? '完整' : '部分缺失'}</span>}
      {unified && !upstream && [
        ['upstream_run_ms', '上游整轮'], ['llm_api_ms', 'LLM累计'],
        ['tool_execution_ms', '工具累计'], ['trace_fetch_ms', 'Trace补齐'],
      ].map(([key, label]) => typeof unified[key] === 'number' && <span key={key}
        title="后端记录的原始范围；并发调用累计时间可能重叠">{label} {exactSeconds(unified[key] as number)}</span>)}
      {upstream && (
        <Item icon={<Clock className="h-3 w-3" />} label="upstream_latency"
          title="HiAgent原始Trace记录的整轮运行耗时（远端用时）；不含本地传输与Trace补齐">
          HiAgent 远端用时 {exactSeconds(upstream.runMs)}
        </Item>
      )}
      <Item icon={<Clock className="h-3 w-3" />} label="latency" title={localTimeTitle(upstream)}>
        {upstream ? `本地用时 ${elapsed === null ? '—' : exactSeconds(elapsed)}` : formatDuration(elapsed)}
      </Item>
      {upstream && [
        ['llm_time', 'LLM累计', upstream.llmMs, '真实LLM调用耗时之和，不含工具、查询及传输；并发时可能重叠'],
        ['tool_time', '工具累计', upstream.toolMs, '真实工具执行耗时之和；并发时可能重叠'],
        ['trace_fetch_time', 'Trace补齐', upstream.traceFetchMs, '回答完成后补查Trace的耗时；不计入本地用时'],
        ['query_time', '查询', upstream.queryMs, '运行前配置、运行后配置及完整轨迹查询耗时之和'],
        ['sse_time', 'SSE接收', upstream.transportMs, '从发起发布API流式请求到收到message_end的本地耗时；包含远端执行，不可与其相加'],
      ].map(([key, label, value, title]) => typeof value === 'number' && (
        <Item key={String(key)} icon={<Clock className="h-3 w-3" />} label={String(key)} title={String(title)}>
          {String(label)} {exactSeconds(value)}
        </Item>
      ))}
      <Item icon={<Timer className="h-3 w-3" />} label="ttft"
        title={upstream ? '本地轮次开始至首个正文token，不等同于HiAgent首响应时间' : '首 token 时间（TTFT）'}>
        {upstream && '本地首正文 '}
        {formatDuration(turnTtftMs(turn))}
      </Item>
      <Item icon={<Coins className="h-3 w-3" />} label="cost" title="成本">
        {usage && unified?.cost == null ? '未提供' : formatCost(m.cost)}
      </Item>
      <Item icon={<Bot className="h-3 w-3" />} label="llm_calls"
        title={trace?.note ? `LLM 调用次数；Trace ${trace.status === 'unavailable' ? '未取得' : '部分核验'}：${trace.note}` : 'LLM 调用次数'}>
        {trace?.status === 'unavailable' || (usage && m.llmCalls === 0) ? 'LLM 调用 未取得' : `LLM ×${m.llmCalls}`}
      </Item>
      <Item icon={<Wrench className="h-3 w-3" />} label="tool_calls" title="工具调用次数">
        ×{m.toolCalls}
      </Item>
      {children && <span className="ml-auto">{children}</span>}
    </div>
  )
}
