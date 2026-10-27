import { ArrowDown, ArrowUp, Bot, Clock, Coins, Cpu, Timer, Wrench } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { formatCost, formatDuration, formatTokens } from '@/lib/format'
import { useNow } from '@/lib/useNow'
import type { TurnMetrics, TurnState } from './sessionState'

function Item({ icon, label, title, children }: { icon: ReactNode; label: string; title: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1" title={title} data-metric={label}>
      {icon}
      <span className="font-mono tabular-nums text-foreground">{children}</span>
    </span>
  )
}

/** 轮次已用时间：结束后用 turn.end 的耗时，运行中用事件时间戳现算 */
export function turnElapsedMs(turn: Pick<TurnState, 'metrics' | 'startedAt' | 'ended'>, now: number): number | null {
  if (turn.metrics.latencyMs !== null) return turn.metrics.latencyMs
  if (turn.ended || !turn.startedAt) return null
  const t = Date.parse(turn.startedAt)
  return Number.isNaN(t) ? null : Math.max(0, now - t)
}

/** 每一轮底部的指标条（折叠标题行上也复用同一组指标）：tokens、耗时、TTFT、成本、LLM 调用次数、工具调用次数 */
export function TurnMetricsBar({
  turn,
  className,
  testId = 'turn-metrics',
  children,
}: {
  turn: Pick<TurnState, 'metrics' | 'startedAt' | 'ended'>
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
        {formatTokens(m.promptTokens)}
        <ArrowDown className="ml-1 mr-0.5 inline h-2.5 w-2.5" />
        {formatTokens(m.completionTokens)}
      </Item>
      <Item icon={<Clock className="h-3 w-3" />} label="latency" title="本轮总耗时">
        {formatDuration(elapsed)}
      </Item>
      <Item icon={<Timer className="h-3 w-3" />} label="ttft" title="首 token 时间（TTFT）">
        {formatDuration(m.ttftMs)}
      </Item>
      <Item icon={<Coins className="h-3 w-3" />} label="cost" title="成本">
        {formatCost(m.cost)}
      </Item>
      <Item icon={<Bot className="h-3 w-3" />} label="llm_calls" title="LLM 调用次数">
        LLM ×{m.llmCalls}
      </Item>
      <Item icon={<Wrench className="h-3 w-3" />} label="tool_calls" title="工具调用次数">
        ×{m.toolCalls}
      </Item>
      {children && <span className="ml-auto">{children}</span>}
    </div>
  )
}
