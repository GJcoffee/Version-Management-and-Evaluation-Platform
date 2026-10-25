import { AlertTriangle, CheckCircle2, Info, XCircle, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useToastStore, type ToastVariant } from '@/stores/toast'

const icons = {
  default: Info,
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
} satisfies Record<ToastVariant, unknown>

const tones: Record<ToastVariant, string> = {
  default: 'text-primary',
  success: 'text-success',
  error: 'text-danger',
  warning: 'text-warning',
}

/** 全局 toast 容器；在应用根部渲染一次。触发方式见 stores/toast.ts 的 `toast.*` */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
    >
      {toasts.map((t) => {
        const Icon = icons[t.variant]
        return (
          <div
            key={t.id}
            role={t.variant === 'error' ? 'alert' : 'status'}
            className="pointer-events-auto flex items-start gap-2.5 rounded-lg border border-border bg-card p-3 shadow-lg"
          >
            <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', tones[t.variant])} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{t.title}</p>
              {t.description && <p className="mt-0.5 break-words text-xs text-muted-foreground">{t.description}</p>}
            </div>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => dismiss(t.id)}
              className="rounded p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
