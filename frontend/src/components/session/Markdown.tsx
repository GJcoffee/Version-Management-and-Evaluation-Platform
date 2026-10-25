import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import { cn } from '@/lib/cn'

/**
 * Markdown 渲染（CommonMark，不渲染原始 HTML）。streaming 为 true 时在末尾显示闪烁光标。
 * 样式见 index.css 的 .md-body。
 */
export const Markdown = memo(function Markdown({
  children,
  streaming,
  className,
}: {
  children: string
  streaming?: boolean
  className?: string
}) {
  return (
    <div className={cn('md-body', streaming && 'md-streaming', className)} data-streaming={streaming || undefined}>
      <ReactMarkdown
        components={{
          a: ({ node: _n, ...props }) => {
            void _n
            return <a {...props} target="_blank" rel="noreferrer noopener" />
          },
        }}
      >
        {children}
      </ReactMarkdown>
      {streaming && children === '' && <span className="stream-cursor" aria-label="正在输出" />}
    </div>
  )
})
