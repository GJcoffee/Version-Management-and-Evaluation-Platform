import { createContext, useContext, useId, useState, type HTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

interface TabsCtx {
  value: string
  setValue: (v: string) => void
  baseId: string
}
const Ctx = createContext<TabsCtx | null>(null)
function useTabs() {
  const c = useContext(Ctx)
  if (!c) throw new Error('Tabs 子组件必须放在 <Tabs> 内')
  return c
}

export interface TabsProps {
  /** 受控值 */
  value?: string
  /** 非受控初始值 */
  defaultValue?: string
  onValueChange?: (v: string) => void
  children: ReactNode
  className?: string
}

/**
 * <Tabs defaultValue="a">
 *   <TabsList><TabsTrigger value="a">A</TabsTrigger></TabsList>
 *   <TabsContent value="a">...</TabsContent>
 * </Tabs>
 */
export function Tabs({ value, defaultValue = '', onValueChange, children, className }: TabsProps) {
  const [inner, setInner] = useState(defaultValue)
  const baseId = useId()
  const current = value ?? inner
  const setValue = (v: string) => {
    if (value === undefined) setInner(v)
    onValueChange?.(v)
  }
  return (
    <Ctx.Provider value={{ value: current, setValue, baseId }}>
      <div className={className}>{children}</div>
    </Ctx.Provider>
  )
}

export function TabsList({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div role="tablist" className={cn('flex items-center gap-1 border-b border-border', className)} {...props} />
}

export function TabsTrigger({
  value,
  children,
  className,
  badge,
}: {
  value: string
  children: ReactNode
  className?: string
  badge?: ReactNode
}) {
  const { value: cur, setValue, baseId } = useTabs()
  const active = cur === value
  return (
    <button
      type="button"
      role="tab"
      id={`${baseId}-tab-${value}`}
      aria-selected={active}
      aria-controls={`${baseId}-panel-${value}`}
      onClick={() => setValue(value)}
      className={cn(
        '-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors',
        active
          ? 'border-primary text-foreground'
          : 'border-transparent text-muted-foreground hover:text-foreground',
        className,
      )}
    >
      {children}
      {badge}
    </button>
  )
}

export function TabsContent({ value, children, className }: { value: string; children: ReactNode; className?: string }) {
  const { value: cur, baseId } = useTabs()
  if (cur !== value) return null
  return (
    <div role="tabpanel" id={`${baseId}-panel-${value}`} aria-labelledby={`${baseId}-tab-${value}`} className={className}>
      {children}
    </div>
  )
}
