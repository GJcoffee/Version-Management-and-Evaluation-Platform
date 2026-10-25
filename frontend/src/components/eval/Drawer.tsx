import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'

/** 右侧抽屉：Portal、Esc / 点击遮罩关闭。`className` 控制宽度，如 `w-[min(760px,100vw)]` */
export function Drawer({
  open,
  onClose,
  title,
  actions,
  children,
  className,
  testId,
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** 标题栏右侧（关闭按钮之前） */
  actions?: ReactNode
  children: ReactNode
  className?: string
  testId?: string
}) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    panelRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="fixed inset-0 bg-black/40" aria-hidden="true" onMouseDown={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        data-testid={testId}
        className={cn('relative flex h-full w-[min(760px,100vw)] flex-col border-l border-border bg-card shadow-xl outline-none', className)}
      >
        <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-2.5">
          <h2 id={titleId} className="min-w-0 flex-1 truncate text-sm font-semibold">
            {title}
          </h2>
          {actions}
          <button type="button" aria-label="关闭" onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
