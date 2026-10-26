import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { scanMedia, type MediaKind } from '@/lib/media'

/** 点击图片后的全屏预览：点击任意位置、按 Esc 或右上角按钮关闭 */
function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="图片预览"
      data-testid="lightbox"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4"
      onClick={(e) => {
        e.stopPropagation() // 不要冒泡到外层（例如选中轮次）
        onClose()
      }}
    >
      <button type="button" aria-label="关闭预览" className="absolute right-4 top-4 rounded-full bg-black/50 p-2 text-white hover:bg-black/70">
        <X className="h-5 w-5" />
      </button>
      <img src={src} alt={alt || '图片预览'} className="max-h-full max-w-full object-contain" />
    </div>,
    document.body,
  )
}

/**
 * 图片（点击放大）或视频播放器。caption 为 true 时在下方显示原始链接（文字里已经有链接原文的场合关掉）。
 * 用 span 而不是 div：可以安全地出现在段落里。
 */
export function MediaView({ kind, src, alt = '', caption = true }: { kind: MediaKind; src: string; alt?: string; caption?: boolean }) {
  const [zoom, setZoom] = useState(false)
  return (
    <span className="my-2 block max-w-full" data-testid="media" data-kind={kind}>
      {kind === 'image' ? (
        <button
          type="button"
          className="block max-w-full cursor-zoom-in rounded-md"
          aria-label="放大查看图片"
          onClick={(e) => {
            e.stopPropagation()
            setZoom(true)
          }}
        >
          <img src={src} alt={alt} loading="lazy" className="max-h-80 max-w-full rounded-md border border-border object-contain" />
        </button>
      ) : (
        <video src={src} controls preload="metadata" className="max-h-80 max-w-full rounded-md bg-black" onClick={(e) => e.stopPropagation()} />
      )}
      {caption && (
        <a href={src} target="_blank" rel="noreferrer noopener" className="mt-1 block break-all font-mono text-xs text-muted-foreground hover:underline" data-testid="media-link">
          {src}
        </a>
      )}
      {zoom && <Lightbox src={src} alt={alt} onClose={() => setZoom(false)} />}
    </span>
  )
}

/** 纯文本（保留原文，包括链接）+ 文中全部图片 / 视频的预览，用于不做 Markdown 渲染的地方（如人工标注页的输出） */
export function MediaText({ text }: { text: string }) {
  const media = useMemo(() => scanMedia(text), [text])
  return (
    <>
      {text}
      {media.length > 0 && (
        <span className="mt-2 flex flex-wrap items-start gap-3 whitespace-normal" data-testid="media-gallery">
          {media.map((m, i) => (
            <MediaView key={`${i}:${m.url}`} kind={m.kind} src={m.url} caption={false} />
          ))}
        </span>
      )}
    </>
  )
}
