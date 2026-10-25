import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Equal, GitCompareArrows } from 'lucide-react'
import type { AlignTurn } from '@/api/compareTypes'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/cn'
import { charDiff, describeArgsDiff, summarizeSteps } from './compareUtils'

/** 两段文本的逐字 diff：左侧独有的删除线（红）、右侧独有的下划线（绿） */
export function TextDiff({ left, right }: { left: string; right: string }) {
  const pieces = useMemo(() => charDiff(left, right), [left, right])
  return (
    <div
      className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background p-3 text-sm leading-relaxed"
      data-testid="text-diff"
    >
      {pieces.map((p, i) =>
        p.op === 'equal' ? (
          <span key={i}>{p.text}</span>
        ) : p.op === 'insert' ? (
          <ins key={i} className="rounded-sm bg-success/20 text-foreground no-underline" data-diff="insert">
            {p.text}
          </ins>
        ) : (
          <del key={i} className="rounded-sm bg-danger/20 text-foreground" data-diff="delete">
            {p.text}
          </del>
        ),
      )}
    </div>
  )
}

/** 对齐视图下每行的附加区域：步骤对齐摘要、参数差异、可展开的 final_text 逐字 diff */
export function AlignmentPanel({ align }: { align: AlignTurn | undefined }) {
  const [open, setOpen] = useState(false)
  if (!align) return null
  const summary = summarizeSteps(align)
  const argDiffs = describeArgsDiff(align)
  const l = align.final_text?.left
  const r = align.final_text?.right
  const bothText = typeof l === 'string' && typeof r === 'string'
  const same = bothText && l === r
  const identical = summary.length === 0 && argDiffs.length === 0
  return (
    <div className="space-y-2 rounded-md border border-dashed border-border px-3 py-2 text-xs" data-testid="alignment-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-1 font-medium text-muted-foreground">
          <GitCompareArrows className="h-3.5 w-3.5" />
          步骤对齐
        </span>
        {identical ? (
          <Badge variant="success" data-testid="align-summary">
            <Equal className="h-3 w-3" />
            {align.left_turn_id && align.right_turn_id ? '步骤一致' : '无可对齐步骤'}
          </Badge>
        ) : (
          summary.map((s) => (
            <Badge key={s} variant="warning" data-testid="align-summary">
              {s}
            </Badge>
          ))
        )}
      </div>
      {argDiffs.length > 0 && (
        <ul className="space-y-0.5" data-testid="args-diff">
          {argDiffs.map((d) => (
            <li key={d} className="font-mono text-[11px] text-warning">
              {d}
            </li>
          ))}
        </ul>
      )}
      {bothText && (
        <div>
          <button
            type="button"
            className={cn('inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-accent', same ? 'text-muted-foreground' : 'text-foreground')}
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            data-testid="toggle-text-diff"
          >
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            最终回复逐字对比{same ? '（两侧相同）' : ''}
          </button>
          {open && (
            <div className="mt-1.5">
              <TextDiff left={l} right={r} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
