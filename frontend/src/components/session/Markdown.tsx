import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import { cn } from '@/lib/cn'
import { remarkMedia } from '@/lib/remarkMedia'
import { MediaView } from './MediaView'

/**
 * Markdown 渲染（CommonMark，不渲染原始 HTML）。streaming 为 true 时在末尾显示闪烁光标。
 * 图片 / 视频链接（markdown 图片、<img> / <video> 标签、裸 URL）渲染为图片（点击放大）/ 视频播放器。
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
        remarkPlugins={[remarkMedia]}
        components={{
          img: ({ node: _n, src, alt, ...props }) => {
            void _n
            // 裸 URL 的原文已在文字里，不再重复显示链接
            return <MediaView kind="image" src={src as string} alt={alt} caption={!(props as Record<string, unknown>)['data-bare']} />
          },
          video: ({ node: _n, src, ...props }) => {
            void _n
            return <MediaView kind="video" src={src as string} caption={!(props as Record<string, unknown>)['data-bare']} />
          },
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
