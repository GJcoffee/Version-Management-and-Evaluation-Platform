import { useEffect } from 'react'
import { X } from 'lucide-react'
import { TraceViewer, type TurnState } from '@/components/session'
import { Button } from '@/components/ui/Button'

export interface TraceDrawerProps {
  /** 左侧 / 右侧，用于标题 */
  title: string
  turn: TurnState | undefined
  sessionId: string
  selectedStepId: string | null
  onSelectStep: (stepId: string | null) => void
  highlightSteps?: ReadonlySet<string>
  onClose: () => void
}

/** 右侧抽屉：查看某一侧某一轮的 Trace */
export function TraceDrawer({ title, turn, sessionId, selectedStepId, onSelectStep, highlightSteps, onClose }: TraceDrawerProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <aside
      className="absolute inset-y-0 right-0 z-30 flex w-[min(62%,860px)] flex-col border-l border-border bg-card shadow-xl"
      aria-label="Trace"
      data-testid="trace-drawer"
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5">
        <span className="text-xs font-semibold">Trace</span>
        <span className="truncate text-xs text-muted-foreground" data-testid="trace-title">
          {title}
        </span>
        <Button size="icon" variant="ghost" aria-label="关闭 Trace" onClick={onClose} className="ml-auto h-7 w-7">
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {turn ? (
        <TraceViewer
          key={turn.id}
          className="flex-1"
          steps={turn.steps}
          turnId={turn.id}
          sessionId={sessionId}
          startedAt={turn.startedAt}
          endedAt={turn.endedAt}
          running={!turn.ended}
          selectedStepId={selectedStepId}
          onSelectStep={onSelectStep}
          highlightSteps={highlightSteps}
        />
      ) : (
        <p className="p-6 text-center text-xs text-muted-foreground">这一轮还没有数据。</p>
      )}
    </aside>
  )
}
