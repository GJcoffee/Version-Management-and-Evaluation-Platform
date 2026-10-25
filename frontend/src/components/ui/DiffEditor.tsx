import { lazy, Suspense } from 'react'
import { LoadingBlock } from './Spinner'
import { cn } from '@/lib/cn'
import type { CodeLanguage } from './CodeEditor'

export interface DiffEditorProps {
  original: string
  modified: string
  language?: CodeLanguage
  height?: number | string
  /** 并排显示（默认）；false 为行内 */
  sideBySide?: boolean
  /** 默认只读 */
  readOnly?: boolean
  onModifiedChange?: (value: string) => void
  /** 滚动到某一行；side 指定左侧（original）或右侧（modified）；`key` 变化才会触发 */
  reveal?: { line: number; side: 'original' | 'modified'; key: number } | null
  className?: string
}

const Impl = lazy(() => import('./monaco/DiffEditorImpl'))

/** Monaco DiffEditor 封装 */
export function DiffEditor({ className, height = 420, ...props }: DiffEditorProps) {
  return (
    <div className={cn('overflow-hidden rounded-md border border-border', className)} style={{ height }}>
      <Suspense fallback={<LoadingBlock label="加载编辑器…" className="h-full" />}>
        <Impl height="100%" {...props} />
      </Suspense>
    </div>
  )
}
