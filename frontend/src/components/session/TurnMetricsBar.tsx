import { ArrowDown, ArrowUp, Bot, Clock, Coins, Cpu, Timer, Wrench } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { formatCost, formatDuration, formatTokens } from '@/lib/format'
import { useNow } from '@/lib/useNow'
import type { TurnMetrics } from './sessionState'
import { turnTiming, turnTtftMs, type TimedTurn } from './turnTiming'
import { exactSeconds, remoteBreakdown, remoteTitle, remoteValueText, reportedUsage } from './upstreamTiming'

function Item({ icon, label, title, children }: { icon: ReactNode; label: string; title: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1" title={title} data-metric={label}>
      {icon}
      <span className="font-mono tabular-nums text-foreground">{children}</span>
    </span>
  )
}

/** 轮次完整执行耗时（录制轮次采用原始耗时；HiAgent 轮次是远端用时，补齐前 / 未取得为 null）。 */
export function turnElapsedMs(turn: TimedTurn, now: number): number | null {
  return turnTiming(turn, now).durationMs
}

/**
 * 每一轮底部的指标条（折叠标题行上也复用同一组指标）：tokens、耗时、TTFT、成本、LLM 调用次数、工具调用次数。
 * 有远端数据的轮次（HiAgent）只显示远端口径：用时 = HiAgent Trace 的运行时长，首 token 时间与成本不适用；
 * Trace 补齐中显示「同步中」，未取得显示「未取得」。
 */
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
  const trace = turn.trace ?? null
  const now = useNow(!turn.ended && !trace, 500)
  const elapsed = turnElapsedMs(turn, now)
  const breakdown = trace && trace.status !== 'pending' ? remoteBreakdown(turn.events ?? []) : null
  // HiAgent 可能不返回整轮 token：如实显示「未提供」，而不是 0
  const reported = trace ? reportedUsage(turn.events ?? []) : undefined
  const syncing = trace?.status === 'pending'
  const missing = trace?.status === 'unavailable'
  return (
    <div
      data-testid={testId}
      className={cn('flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground', className)}
    >
      <Item
        icon={<Cpu className="h-3 w-3" />}
        label="tokens"
        title={`输入 ${m.promptTokens} + 输出 ${m.completionTokens} tokens${m.final ? '' : '（实时累计）'}${trace ? '（HiAgent 整轮用量）' : ''}`}
      >
        <ArrowUp className="mr-0.5 inline h-2.5 w-2.5" />
        {reported && reported.prompt_tokens == null ? '未提供' : formatTokens(m.promptTokens)}
        <ArrowDown className="ml-1 mr-0.5 inline h-2.5 w-2.5" />
        {reported && reported.completion_tokens == null ? '未提供' : formatTokens(m.completionTokens)}
      </Item>
      {trace ? (
        <Item icon={<Clock className="h-3 w-3" />} label="latency" title={remoteTitle(trace, 'HiAgent 用时')}>
          HiAgent 用时 {remoteValueText(trace, elapsed)}
        </Item>
      ) : (
        <Item icon={<Clock className="h-3 w-3" />} label="latency" title="本轮总耗时">
          {formatDuration(elapsed)}
        </Item>
      )}
      {breakdown?.llmMs != null && (
        <Item icon={<Clock className="h-3 w-3" />} label="llm_time"
          title="HiAgent Trace 中各次 LLM 调用耗时之和，不含工具；并发时可能重叠">
          LLM累计 {exactSeconds(breakdown.llmMs)}
        </Item>
      )}
      {breakdown?.toolMs != null && (
        <Item icon={<Clock className="h-3 w-3" />} label="tool_time"
          title="HiAgent Trace 中各次工具执行耗时之和；并发时可能重叠">
          工具累计 {exactSeconds(breakdown.toolMs)}
        </Item>
      )}
      {!trace && (
        <Item icon={<Timer className="h-3 w-3" />} label="ttft" title="首 token 时间（TTFT）">
          {formatDuration(turnTtftMs(turn))}
        </Item>
      )}
      {!trace && (
        <Item icon={<Coins className="h-3 w-3" />} label="cost" title="成本">
          {formatCost(m.cost)}
        </Item>
      )}
      <Item icon={<Bot className="h-3 w-3" />} label="llm_calls"
        title={trace ? remoteTitle(trace, 'LLM 调用次数') : 'LLM 调用次数'}>
        {syncing ? 'LLM 同步中' : missing ? 'LLM 调用 未取得' : `LLM ×${m.llmCalls}`}
      </Item>
      <Item icon={<Wrench className="h-3 w-3" />} label="tool_calls" title="工具调用次数">
        ×{m.toolCalls}
      </Item>
      {children && <span className="ml-auto">{children}</span>}
    </div>
  )
}
