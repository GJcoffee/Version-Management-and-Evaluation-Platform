import { lazy, Suspense } from 'react'
import { LoadingBlock } from './Spinner'
import { cn } from '@/lib/cn'

export type CodeLanguage = 'yaml' | 'json' | 'markdown' | 'plaintext'

/** 编辑器内的行标注（如 validate 返回的错误） */
export interface CodeMarker {
  line: number
  column?: number
  endLine?: number
  endColumn?: number
  message: string
  severity?: 'error' | 'warning' | 'info'
}

export interface CodeEditorProps {
  value: string
  onChange?: (value: string) => void
  language?: CodeLanguage
  readOnly?: boolean
  /** 数字（px）或 CSS 长度，如 '100%' */
  height?: number | string
  markers?: CodeMarker[]
  wordWrap?: boolean
  lineNumbers?: boolean
  /** 同一页面有多个编辑器且需要各自 undo 栈时，给每个传不同的 path */
  path?: string
  className?: string
}

// Monaco 体积大：按需加载，不进入首屏 bundle
const Impl = lazy(() => import('./monaco/CodeEditorImpl'))

/** Monaco 编辑器封装（本地打包，离线可用）；readOnly 模式用于只读展示 */
export function CodeEditor({ className, height = 320, ...props }: CodeEditorProps) {
  return (
    <div className={cn('overflow-hidden rounded-md border border-border', className)} style={{ height }}>
      <Suspense fallback={<LoadingBlock label="加载编辑器…" className="h-full" />}>
        <Impl height="100%" {...props} />
      </Suspense>
    </div>
  )
}
