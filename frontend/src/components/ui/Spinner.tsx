import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span role="status" aria-label={label ?? '加载中'} className="inline-flex items-center">
      <Loader2 className={cn('h-4 w-4 animate-spin text-muted-foreground', className)} />
    </span>
  )
}

/** 区块级加载占位 */
export function LoadingBlock({ label = '加载中…', className }: { label?: string; className?: string }) {
  return (
    <div className={cn('flex items-center justify-center gap-2 py-10 text-muted-foreground', className)}>
      <Spinner />
      <span>{label}</span>
    </div>
  )
}
