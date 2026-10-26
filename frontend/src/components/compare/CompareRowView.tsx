import { memo } from 'react'
import { Hourglass, Loader2, Minus } from 'lucide-react'
import type { CompareMode } from '@/api/compareTypes'
import { TurnView, type TurnState } from '@/components/session'
import { cn } from '@/lib/cn'
import { AlignmentPanel } from './AlignmentPanel'
import type { CompareRow } from './compareUtils'
import { MetricsCompare } from './MetricsCompare'

export type Side = 'left' | 'right'

export interface TraceTarget {
  side: Side
  turnId: string
}

interface CellProps {
  side: Side
  turnId: string | null
  turn: TurnState | undefined
  sessionId: string
  mode: CompareMode
  /** 重放仍在进行：右侧没有的轮次显示“排队中” */
  replayActive: boolean
  highlight: ReadonlySet<string> | undefined
  selected: boolean
  /** 这一行是单侧消息（分叉） */
  fork?: boolean
  onOpenTrace: (side: Side, turnId: string, stepId?: string) => void
}

function Placeholder({ icon, text, testid }: { icon: React.ReactNode; text: string; testid: string }) {
  return (
    <div
      className="flex min-h-24 flex-1 flex-col items-center justify-center gap-1.5 rounded-md border border-dashed border-border text-xs text-muted-foreground"
      data-testid={testid}
    >
      {icon}
      {text}
    </div>
  )
}

function Cell({ side, turnId, turn, sessionId, mode, replayActive, highlight, selected, fork, onOpenTrace }: CellProps) {
  let body
  if (turn) {
    body = (
      <TurnView
        turn={turn}
        sessionId={sessionId}
        selected={selected}
        onSelectTurn={(id) => onOpenTrace(side, id)}
        highlightSteps={highlight}
        onSelectStep={(id, stepId) => onOpenTrace(side, id, stepId)}
      />
    )
  } else if (turnId) {
    body = <Placeholder icon={<Loader2 className="h-4 w-4 animate-spin" />} text="加载中…" testid="cell-loading" />
  } else if (fork) {
    body = <Placeholder icon={<Minus className="h-4 w-4" />} text="这条消息没有发给这一侧" testid="cell-missing" />
  } else if (mode === 'replay' && side === 'right') {
    body = replayActive ? (
      <Placeholder icon={<Hourglass className="h-4 w-4" />} text="排队中" testid="cell-queued" />
    ) : (
      <Placeholder icon={<Minus className="h-4 w-4" />} text="未重放" testid="cell-missing" />
    )
  } else {
    body = <Placeholder icon={<Minus className="h-4 w-4" />} text="这一侧没有此轮" testid="cell-missing" />
  }
  return (
    <div className={cn('flex min-w-0 flex-col rounded-lg border border-border bg-card p-3')} data-testid={`cell-${side}`}>
      {body}
    </div>
  )
}

export interface CompareRowViewProps {
  row: CompareRow
  /** 行号（从 1 开始，展示用；单侧消息没有行号） */
  ordinal: number
  mode: CompareMode
  left: TurnState | undefined
  right: TurnState | undefined
  leftSessionId: string
  rightSessionId: string
  replayActive: boolean
  alignOn: boolean
  leftHighlight: ReadonlySet<string> | undefined
  rightHighlight: ReadonlySet<string> | undefined
  trace: TraceTarget | null
  onOpenTrace: (side: Side, turnId: string, stepId?: string) => void
}

/** 同一个 pair 的一行：左右两轮（高度对齐）+ 指标对照 + （对齐视图）步骤对齐摘要与文本 diff */
export const CompareRowView = memo(function CompareRowView(p: CompareRowViewProps) {
  const { left, right } = p
  return (
    <section className="space-y-2" data-testid="compare-row" data-pair={p.row.pair} data-fork={p.row.fork}>
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {p.row.fork ? (
          <span className="rounded bg-warning/15 px-1.5 py-0.5 text-warning" data-testid="fork-label">
            单侧消息 · 仅{p.row.fork === 'left' ? '左' : '右'}侧
          </span>
        ) : (
          <span className="rounded bg-muted px-1.5 py-0.5 font-mono">第 {p.ordinal} 轮</span>
        )}
        <span className="h-px flex-1 bg-border" />
      </div>
      <div className="grid grid-cols-2 items-stretch gap-3">
        <Cell
          side="left"
          turnId={p.row.leftTurnId}
          turn={left}
          sessionId={p.leftSessionId}
          mode={p.mode}
          replayActive={p.replayActive}
          highlight={p.alignOn ? p.leftHighlight : undefined}
          selected={p.trace?.side === 'left' && p.trace.turnId === p.row.leftTurnId}
          fork={!!p.row.fork}
          onOpenTrace={p.onOpenTrace}
        />
        <Cell
          side="right"
          turnId={p.row.rightTurnId}
          turn={right}
          sessionId={p.rightSessionId}
          mode={p.mode}
          replayActive={p.replayActive}
          highlight={p.alignOn ? p.rightHighlight : undefined}
          selected={p.trace?.side === 'right' && p.trace.turnId === p.row.rightTurnId}
          fork={!!p.row.fork}
          onOpenTrace={p.onOpenTrace}
        />
      </div>
      {!p.row.fork && <MetricsCompare left={left} right={right} />}
      {p.alignOn && !p.row.fork && <AlignmentPanel align={p.row.align} />}
    </section>
  )
})
